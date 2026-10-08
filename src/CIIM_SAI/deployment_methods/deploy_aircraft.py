"""Cost and schedule model for aircraft deployment.

Purpose-built lofters climb to the injection altitude, disperse their payload
during cruise, and return. The capital in this method is the fleet and the
basing that supports its movements.

Defines the shape of inputs/deployment_methods/aircraft.yaml. Reads no files:
everything arrives via Inputs.
"""

from __future__ import annotations

import warnings

from pydantic import Field, model_validator

from dataclasses import dataclass

import pandas as pd

from CIIM_SAI.load_inputs import Frozen, Inputs, Material
from CIIM_SAI.deployment_methods.scheduling import determine_units_required


# --- Classes from aircraft.yaml ----------------------------------------------
# One class per nesting level in the YAML.


class Segment(Frozen):
    """A flight segment flown at a fixed cost: climb and descent.

    No payload leaves the aircraft during either, so neither needs a rate --
    just how long it takes and what it burns.
    """

    duration_hours: float = Field(gt=0, description="Wall-clock time in the segment")
    fuel: float = Field(ge=0, description="kg burned over the segment")


class Reserve(Frozen):
    """Fuel carried the whole mission against a diversion, and normally not burned.

    Deliberately has no duration: the reserve is not flown, so it contributes
    nothing to mission time. Its weight still has to be lifted, which is the
    only reason the model knows about it. extra="forbid" makes giving it a
    duration an error rather than a field quietly ignored.
    """

    fuel: float = Field(ge=0, description="kg carried but not burned")


class Cruise(Frozen):
    """The dispersing segment, whose duration the model solves rather than reads."""

    fuel_burn_rate: float = Field(gt=0, description="kg/s at the target altitude")


class MissionProfile(Frozen):
    """One entry under a design's mission_profiles: how it flies to one altitude.

    Climb, descent and reserve are fixed costs of reaching that altitude at all.
    What is left of the takeoff weight once they and the empty aircraft are paid
    for is the cruise useful weight, which the solve splits between fuel and
    payload.
    """

    takeoff_weight: float = Field(gt=0, description="kg at brake release")
    climb: Segment
    cruise: Cruise
    descent: Segment
    reserve: Reserve

    @property
    def fixed_fuel(self) -> float:
        """Climb, descent, and reserve fuel excluded from the cruise weight budget."""
        return self.climb.fuel + self.descent.fuel + self.reserve.fuel

    @property
    def fixed_hours(self) -> float:
        """Flight time outside cruise. The reserve adds none, by definition."""
        return self.climb.duration_hours + self.descent.duration_hours


class Development(Frozen):
    NRE: float = Field(ge=0, description="Total non-recurring engineering cost, USD")
    duration_years: int = Field(gt=0, description="Years of development before the first order")


class AircraftDesign(Frozen):
    """One entry under aircraft.designs: an aircraft that can be bought and flown."""

    unit_cost: float = Field(ge=0, description="Purchase price of one aircraft, USD")
    lead_time_years: int = Field(ge=0, description="Years from order to delivery")
    lifetime_years: int = Field(gt=0, description="Calendar life")
    lifetime_cycles: int = Field(gt=0, description="Airframe lifetime in flights")
    availability: float = Field(gt=0, le=1, description="Fraction of the owned fleet not in maintenance")
    maintenance_rate: float = Field(ge=0, description="Per year, against unit cost")
    max_payload: float = Field(gt=0, description="kg the aircraft may carry")
    operating_empty_weight: float = Field(gt=0, description="kg, the aircraft with no fuel or payload")
    ground_cycle_hours: float = Field(gt=0, description="Taxi, check, crew change, reload and refuel")
    aircrew_size: int = Field(ge=0, description="People per crew; 0 is uncrewed")
    payload_emission_rate: float = Field(gt=0, description="kg/s the dispersal system puts out in cruise")
    development: Development
    mission_profiles: dict[int, MissionProfile]
    source: str

    @model_validator(mode="after")
    def profiles_leave_room_for_cruise(self) -> AircraftDesign:
        """Every profile must get off the ground with something left to cruise on.

        Takeoff weight, empty weight and the three fixed fuel loads are all
        properties of the file, with nothing the scenario can change, so a
        profile that does not close is a typo rather than an infeasible mission
        -- which is why this is a schema check and not a failure in the solve.
        It covers every profile, including ones this run will not fly: a file
        with one bad row is not a file to trust the other rows of.
        """
        if not self.mission_profiles:
            raise ValueError("an aircraft needs at least one mission profile to fly")

        for altitude, profile in sorted(self.mission_profiles.items()):
            useful = profile.takeoff_weight - self.operating_empty_weight - profile.fixed_fuel
            if useful <= 0:
                raise ValueError(
                    f"the {altitude:,} m mission profile leaves {useful:,.0f} kg for "
                    f"cruise: a {profile.takeoff_weight:,.0f} kg takeoff weight does not "
                    f"cover the {self.operating_empty_weight:,.0f} kg empty aircraft plus "
                    f"{profile.fixed_fuel:,.0f} kg of climb, descent and reserve fuel"
                )
        return self


class Aircraft(Frozen):
    fuel: str = Field(description="A material in material.yaml, carrying a cost per kg")
    designs: dict[str, AircraftDesign]

    @model_validator(mode="after")
    def at_least_one_design(self) -> Aircraft:
        if not self.designs:
            raise ValueError("aircraft.designs needs at least one design to fly")
        return self


class Operations(Frozen):
    """The concept of operations, stated rather than assumed."""

    operating_hours_per_day: float = Field(gt=0, le=24)
    operating_days_per_year: float = Field(gt=0, le=366)
    excess_capacity: float = Field(
        ge=0, description="Fleet bought above what demand strictly requires, as a fraction"
    )


class Aircrew(Frozen):
    shifts_per_week: float = Field(gt=0, le=7)
    salary: float = Field(ge=0, description="USD per person per year, before overhead")


class GroundCrew(Frozen):
    shifts_per_week: float = Field(gt=0, le=7)
    salary: float = Field(ge=0, description="USD per person per year, before overhead")
    team_size: int = Field(gt=0, description="People servicing one aircraft between cycles")
    ground_cycle_share: float = Field(
        gt=0, le=1, description="Fraction of the aircraft's ground cycle the team works"
    )
    extra_hours_per_cycle: float = Field(ge=0)


class Support(Frozen):
    ratio: float = Field(ge=0, description="Heads per aircrew + ground crew head")
    salary: float = Field(ge=0, description="USD per person per year, before overhead")


class Labor(Frozen):
    shift_hours: float = Field(gt=0, le=24)
    weeks_per_year: float = Field(gt=0, le=52)
    overhead_rate: float = Field(ge=0, description="Applied to every salary")
    aircrew: Aircrew
    ground_crew: GroundCrew
    support: Support


class Basing(Frozen):
    """Pooled basing capacity, bought in units of a fixed movement throughput."""

    cost_per_annual_movement: float = Field(ge=0, description="USD of capital per movement per year")
    movements_per_flight: int = Field(gt=0, description="A takeoff and a landing is two")
    annual_movements_per_unit: float = Field(gt=0, description="Capacity of one base unit")
    build_years: int = Field(ge=0, description="Years from order to operating")
    lifetime_years: int = Field(gt=0, description="Years in service before replacement")
    maintenance_rate: float = Field(ge=0, description="Per year, against installed capital")
    source: str


class AircraftMethod(Frozen):
    """The contents of aircraft.yaml.

    Named for the file rather than for the aircraft block inside it, which the
    Aircraft class above describes. Design names are dict keys the model does
    not enumerate, as balloon designs are.
    """

    aircraft: Aircraft
    operations: Operations
    labor: Labor
    basing: Basing

    @model_validator(mode="after")
    def ground_crew_fits_a_shift(self) -> AircraftMethod:
        """A ground crew has to finish at least one cycle within one shift.

        The check spans two blocks -- the team's hours come from labor, the
        aircraft's ground cycle from the design -- so it can only live here,
        where both are in scope. Without it a long enough ground cycle makes the
        cycles a team can service per shift zero, and the headcount divides by it.
        """
        for name, design in self.aircraft.designs.items():
            hours = (design.ground_cycle_hours * self.labor.ground_crew.ground_cycle_share
                     + self.labor.ground_crew.extra_hours_per_cycle)
            if hours > self.labor.shift_hours:
                raise ValueError(
                    f"servicing one {name} takes a ground crew {hours:,.2f} hours, more "
                    f"than the {self.labor.shift_hours:,.0f} hour shift, so no crew can "
                    f"complete a cycle"
                )
        return self


class AircraftOptions(Frozen):
    """The scenario's method_options when the deployment method is aircraft."""

    design: str = Field(description="A key under aircraft.designs in aircraft.yaml")
    payload_emission_rate: float | None = Field(
    default=None,
    gt=0,
    description="kg/s; omitted means use the selected aircraft design's rate",
    )


# --- Checks that span more than one input file -------------------------------


def find_design(method: AircraftMethod, options: AircraftOptions) -> AircraftDesign:
    """The design the scenario selected, from the ones the method file offers."""
    design = method.aircraft.designs.get(options.design)
    if design is None:
        raise ValueError(
            f"method_options.design is {options.design!r}, which aircraft.yaml does not "
            f"define. Designs offered: {', '.join(sorted(method.aircraft.designs))}"
        )
    return design


def find_profile(design: AircraftDesign, altitude: float) -> MissionProfile:
    """The mission profile for this altitude, which must be one the design has.

    Profiles are supplied for specific altitudes; this model does not interpolate between them.

    Altitudes reach here as floats from the scenario and are keyed as integers
    in the file, so a whole number matches and anything else is rejected rather
    than silently truncated onto the profile below it.
    """
    offered = ", ".join(f"{a:,} m" for a in sorted(design.mission_profiles))
    if altitude != int(altitude):
        raise ValueError(
            f"altitude {altitude:,.1f} m is not a whole number of meters, so it cannot "
            f"name a mission profile. Profiles: {offered}"
        )
    profile = design.mission_profiles.get(int(altitude))
    if profile is None:
        raise ValueError(
            f"this aircraft has no mission profile at {altitude:,.0f} m. "
            f"Profiles: {offered}"
        )
    return profile


def find_fuel(materials: dict[str, Material], name: str) -> Material:
    """The fuel from material.yaml, which needs nothing but a price.

    Unlike the balloon's lift gas this is burned rather than lofted, so no
    molar mass is wanted and a fuel with a forcing block would be a mistake in
    material.yaml rather than something to check here.
    """
    fuel = materials.get(name)
    if fuel is None:
        raise ValueError(
            f"aircraft.fuel is {name!r}, which material.yaml does not define. "
            f"Materials: {', '.join(sorted(materials))}"
        )
    return fuel


# --- What a front end may ask of this method ---------------------------------


def option_choices(method: dict) -> dict[str, list[str]]:
    """The method_options a front end may offer, and the values each may take."""
    return {"design": sorted(AircraftMethod(**method).aircraft.designs)}


def deployable_materials(materials: dict) -> list[str]:
    """Materials this method can deploy, out of everything material.yaml defines.

    The payload rides in a tank rather than filling an envelope, so unlike the
    balloon this needs no molar mass -- anything with a forcing block is an SAI
    agent an aircraft could carry. That is what keeps the fuel out of the list.
    """
    return sorted(
        name for name, values in materials.items() if values.get("forcing") is not None
    )


# --- Mission calculations ----------------------------------------------------


@dataclass(frozen=True)
class Mission:
    """Derived properties of one full-payload flight and its ground cycle.

    Masses are in kg and durations in hours. fuel_burned excludes reserve.
    cycle_hours includes the ground cycle and is also the assumed crew duty.
    crews_per_flight counts complete crews, not individual crew members;
    each crew contains design.aircrew_size people.
    """

    payload: float                 # kg deployed per flight
    cruise_fuel: float             # kg burned during cruise
    fuel_burned: float             # kg burned in climb, cruise, and descent
    cruise_hours: float
    flight_hours: float             # climb + cruise + descent
    cycle_hours: float              # flight + ground cycle; also crew duty
    crews_per_flight: int           # 0 uncrewed, 1 single-crewed, 2 double-crewed


def calculate_mission(
    design: AircraftDesign,
    profile: MissionProfile,
    emission_rate: float,
) -> Mission:
    """Solve the cruise weight budget and derive full-flight requirements.

    Inputs have already passed their schemas: emission_rate is positive,
    and the profile leaves positive useful weight after empty weight and
    climb, descent, and reserve fuel are subtracted.

    Cruise duration, fuel mass, and payload mass are three unknowns linked
    by three equations:

        fuel + payload = useful_weight
        fuel / fuel_burn_rate = cruise_seconds
        payload / emission_rate = cruise_seconds

    This is solvable analytically. Substitution gives:

        cruise_seconds = useful_weight / (fuel_burn_rate + emission_rate)

    Multiplying this duration by each rate gives the corresponding mass.
    Both rates are in kg/s, so the solved duration is in seconds. Slower
    emission allocates more of the available weight to fuel and less to
    payload.

    This solution uses the entire cruise weight budget. If the resulting
    payload exceeds the aircraft's payload limit, the mission is rejected
    rather than recalculated at a lower takeoff weight.

    Reserve fuel is carried throughout but is not normally burned.

    Crew duty includes the full ground cycle. Crewed duties up to eight
    hours use one crew; duties above eight and up to sixteen use two crews
    and give a warning. Longer crewed duties are rejected. These thresholds
    are modeling assumptions. Double-crewing does not change the modeled
    aircraft weight or performance. Uncrewed missions have no duty cycle length limit.
    """
    useful_weight = (
        profile.takeoff_weight
        - design.operating_empty_weight
        - profile.fixed_fuel
    )
    cruise_seconds = useful_weight / (
        profile.cruise.fuel_burn_rate + emission_rate
    )
    payload = emission_rate * cruise_seconds
    cruise_fuel = profile.cruise.fuel_burn_rate * cruise_seconds

    if payload > design.max_payload:
        raise ValueError(
            f"The mission requires {payload:,.1f} kg of payload, exceeding "
            f"the aircraft's maximum payload of {design.max_payload:,.1f} kg. "
            "CIIM does not automatically reduce the payload. Review the "
            "mission profile, aircraft payload limit, or payload emission rate."
        )

    cruise_hours = cruise_seconds / 3600
    flight_hours = profile.fixed_hours + cruise_hours
    cycle_hours = flight_hours + design.ground_cycle_hours
    fuel_burned = profile.climb.fuel + cruise_fuel + profile.descent.fuel

    crews_per_flight = 0
    if design.aircrew_size > 0:
        if cycle_hours > 16:
            raise ValueError(
                "This is a crewed duty cycle lasting more than 16 hours, "
                "meaning that it would have to be triple-crewed. This may "
                "not be feasible, and CIIM does not currently allow analysis "
                "of such a scenario. Please increase payload emission rate "
                "to reduce cruise length."
            )

        crews_per_flight = 1
        if cycle_hours > 8:
            crews_per_flight = 2
            warnings.warn(
                "This is a crewed duty cycle lasting more than 8 hours, "
                "meaning that it would have to be double-crewed. This may "
                "pose additional constraints or reduce performance in ways "
                "that are not represented in CIIM, so results for this "
                "simulation may be overly optimistic. Consider increasing "
                "payload emission rate to reduce cruise length.",
                UserWarning,
                stacklevel=2,
            )

    return Mission(
        payload=payload,
        cruise_fuel=cruise_fuel,
        fuel_burned=fuel_burned,
        cruise_hours=cruise_hours,
        flight_hours=flight_hours,
        cycle_hours=cycle_hours,
        crews_per_flight=crews_per_flight,
    )


# --- Deployment operations ---------------------------------------------------


def calculate_flights(
    demand: pd.Series,
    payload_per_flight: float,
) -> pd.Series:
    """Whole flights required each year to meet or exceed deployment demand.

    demand is a year-indexed Series in kg; payload_per_flight is the
    mission's solved payload in kg. Every flight carries that full payload,
    so rounding upward can deploy slightly more material than requested.

    Actual deployed mass is flights * payload_per_flight. Zero demand
    requires zero flights.
    """
    return determine_units_required(demand, payload_per_flight)


def calculate_aircraft_required(
    flights: pd.Series,
    flights_per_aircraft_per_year: float,
    availability: float,
    excess_capacity: float,
) -> pd.Series:
    """Minimum whole owned fleet required each year.

    flights_per_aircraft_per_year is the operating calendar divided by
    mission cycle time, before maintenance availability is applied. It
    remains fractional because cycles can span days.

    Availability reduces the productive capacity of each owned aircraft.
    Excess capacity adds a fleet margin: 0.15 means buying 15% above the
    requirement after allowing for maintenance. Round upward only after
    applying both adjustments.

    This is the required fleet, not annual purchases. schedule_aircraft()
    accounts for aircraft retained from previous years and replacements.
    """

    capacity_per_owned_aircraft = (
        flights_per_aircraft_per_year
        * availability
        / (1 + excess_capacity)
    )
    return determine_units_required(flights, capacity_per_owned_aircraft)


# --- Aircraft fleet scheduling -----------------------------------------------


@dataclass
class Cohort:
    """Aircraft delivered in one year, sharing an average accumulated cycle count."""
    delivered: int             # calendar year entering service
    count: int
    cycles: float = 0.0        # accumulated cycles per aircraft


def schedule_aircraft(
    flights: pd.Series,
    aircraft_required: pd.Series,
    lifetime_years: int,
    lifetime_cycles: int,
) -> pd.DataFrame:
    """Retire, deliver, and fly aircraft in annual steps.

    flights and aircraft_required share a consecutive integer year index.
    The caller sizes aircraft_required using availability and excess capacity.

    At the start of each year, retire cohorts whose calendar life has elapsed
    or whose accumulated cycles have reached the limit. Deliver enough aircraft
    to cover any fleet shortfall, then share this year's flights equally across
    all aircraft in service.
    Surplus aircraft remain in service and incur full annual maintenance.

    Cycle retirement uses the previous year's closing cycle count. A cohort
    reaching its limit during a year serves that entire year and retires at the
    next year's start. This annual approximation can overshoot the cycle limit
    by less than one year's flying; no within-year replacements are modeled.

    The caller shifts deliveries backward by the lead time to schedule orders
    and capital spending, using the same annual convention as other methods.
    
    Output columns:
        aircraft_required          Minimum owned fleet needed for this year.
        aircraft_entering_service  Aircraft delivered at the year's start.
        aircraft_retiring_calendar Aircraft retired under the calendar limit.
        aircraft_retiring_cycles   Aircraft retired under the cycle limit.
        aircraft_active            All aircraft retained in service, including
                                   maintenance downtime and spare capacity.
        cycles_per_aircraft        This year's flights per owned aircraft,
                                   added to each cohort's accumulated cycles.
    """
    cohorts: list[Cohort] = []
    rows = []

    for year, annual_flights in flights.items():
        required = int(aircraft_required.loc[year])
        retiring_calendar = retiring_cycles = 0
        survivors = []
        for cohort in cohorts:
            if year - cohort.delivered >= lifetime_years:
                # Count simultaneous calendar and cycle retirement only once.
                retiring_calendar += cohort.count
            elif cohort.cycles >= lifetime_cycles - 1e-8:
                # Tolerance avoids delaying retirement for floating-point noise.
                retiring_cycles += cohort.count
            else:
                survivors.append(cohort)
        cohorts = survivors

        active = sum(cohort.count for cohort in cohorts)
        entering = max(0, required - active)
        if entering:
            cohorts.append(Cohort(int(year), entering))
            active += entering

        cycles_per_aircraft = float(annual_flights) / active if active else 0.0
        for cohort in cohorts:
            cohort.cycles += cycles_per_aircraft

        rows.append({
            "year": year,
            "aircraft_required": required,
            "aircraft_entering_service": entering,
            "aircraft_retiring_calendar": retiring_calendar,
            "aircraft_retiring_cycles": retiring_cycles,
            "aircraft_active": active,
            "cycles_per_aircraft": cycles_per_aircraft,
        })

    return pd.DataFrame(rows).set_index("year")