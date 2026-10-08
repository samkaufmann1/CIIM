"""Cost and schedule model for aircraft deployment.

Purpose-built lofters climb to the injection altitude, disperse their payload
during cruise, and return. The capital in this method is the fleet and the
basing that supports its movements.

Defines the shape of inputs/deployment_methods/aircraft.yaml. Reads no files:
everything arrives via Inputs.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from math import floor

import pandas as pd
from pydantic import Field, model_validator

from CIIM_SAI.load_inputs import Frozen, Inputs, Material
from CIIM_SAI.deployment_methods.scheduling import (
    determine_units_required,
    find_program_years,
    schedule_assets,
    spread_capital,
    spread_development,
)


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
    shift_hours: float,
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

        Crew duty includes the full ground cycle. Duties up to one configured
    shift use one crew; duties above one and up to two shifts use two crews
    and give a warning. Longer crewed duties are rejected. These thresholds
    are modeling assumptions. Double-crewing does not change the modeled
    aircraft weight or performance. Uncrewed missions have no duty limit.

    shift_hours comes from the validated labor inputs and must match the
    shift length used when calculating annual aircrew requirements.
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
        if cycle_hours > 2 * shift_hours:
            raise ValueError(
                f"This crewed duty cycle lasts {cycle_hours:.2f} hours, "
                f"exceeding two {shift_hours:g}-hour shifts. It would require "
                "at least three crews. This may not be feasible, and CIIM "
                "does not currently allow analysis of such a scenario. "
                "Please increase payload emission rate to reduce cruise length."
            )

        crews_per_flight = 1
        if cycle_hours > shift_hours:
            crews_per_flight = 2
            warnings.warn(
                f"This crewed duty cycle lasts {cycle_hours:.2f} hours, "
                f"exceeding one {shift_hours:g}-hour shift. It would have "
                "to be double-crewed. This may pose additional constraints "
                "or reduce performance in ways that are not represented "
                "in CIIM, so results for this simulation may be overly "
                "optimistic. Consider increasing payload emission rate "
                "to reduce cruise length.",
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




def calculate_ground_crew(
    flights: pd.Series,
    design: AircraftDesign,
    labor: Labor,
) -> pd.Series:
    """Ground-crew headcount required each year, in complete teams.

    Each team services one aircraft at a time. Service time is the specified
    share of the aircraft's ground cycle plus extra time per cycle.

    Round cycles per shift downward: a team must finish each service within
    its shift. Round the annual team requirement upward, then multiply by
    team size to obtain headcount.

    This estimates staffing from annual workload. It assumes flights can be
    staggered to use team capacity and does not model peaks within a day.
    AircraftMethod validation ensures one service fits within a shift.
    """
    ground = labor.ground_crew
    service_hours = (
        design.ground_cycle_hours * ground.ground_cycle_share
        + ground.extra_hours_per_cycle
    )
    cycles_per_shift = floor(labor.shift_hours / service_hours)
    cycles_per_team_per_year = (
        cycles_per_shift
        * ground.shifts_per_week
        * labor.weeks_per_year
    )
    teams_required = determine_units_required(
        flights, cycles_per_team_per_year
    )
    return teams_required * ground.team_size


def calculate_basing_required(
    flights: pd.Series,
    basing: Basing,
) -> pd.Series:
    """Base capacity blocks required each year for the scheduled flights.

    Each flight generates movements_per_flight movements, normally one
    takeoff and one landing. Capacity is purchased in whole blocks of
    annual_movements_per_unit; rounding upward represents its lumpiness.

    Blocks are pooled capacity, not individual geographically located bases.
    Requirements follow actual flights; no additional basing margin is
    applied for spare aircraft.
    """
    movements = flights * basing.movements_per_flight
    return determine_units_required(
        movements, basing.annual_movements_per_unit
    )


def calculate_aircrew(
    flights: pd.Series,
    mission: Mission,
    design: AircraftDesign,
    labor: Labor,
) -> pd.Series:
    """Aircrew headcount required each year, in complete crews.

    Single-crewed missions fit a whole number of flight-plus-ground cycles
    into each shift. Double-crewed missions consume one shift from each
    of two crews per flight, following the mission calculator's approximation.

    Annual crew-shifts are shared across the workforce. Round the number
    of crews employed upward, then multiply by people per crew.

    Uses the same configured shift length passed to calculate_mission().
    The mission's crews_per_flight therefore determines whether each flight
    fits within one shift or requires two crew-shifts.
    """
    if design.aircrew_size == 0:
        return pd.Series(0, index=flights.index, dtype=int)

    if mission.crews_per_flight == 2:
        crew_shifts_required = flights * 2
    else:
        flights_per_shift = floor(labor.shift_hours / mission.cycle_hours)
        crew_shifts_required = flights / flights_per_shift

    shifts_per_crew_per_year = (
        labor.aircrew.shifts_per_week * labor.weeks_per_year
    )
    crews_required = determine_units_required(
        crew_shifts_required, shifts_per_crew_per_year
    )
    return crews_required * design.aircrew_size


def calculate_support_staff(
    aircrew: pd.Series,
    ground_crew: pd.Series,
    labor: Labor,
) -> pd.Series:
    """Other personnel, rounded upward to whole people.

    Apply the support ratio to combined aircrew and ground-crew headcount.
    For uncrewed aircraft, this category also covers remote operations;
    no separate remote-operator requirement is calculated.
    """
    staff_required = (aircrew + ground_crew) * labor.support.ratio
    return determine_units_required(staff_required, 1.0)

def calculate_labor_costs(
    aircrew: pd.Series,
    ground_crew: pd.Series,
    support_staff: pd.Series,
    labor: Labor,
) -> pd.DataFrame:
    """Annual salary costs in USD, including overhead for every group.

    Headcounts are annual staffing requirements. Each person incurs a full
    year's salary; hiring, training, and severance costs are not modeled.
    
    Returns a year-indexed table with aircrew_cost, ground_crew_cost,
    support_staff_cost, and their sum, labor_cost. Every cost column
    includes overhead. Input headcounts must share the same year index.
    """
    costs = pd.DataFrame({
        "aircrew_cost": aircrew * labor.aircrew.salary,
        "ground_crew_cost": ground_crew * labor.ground_crew.salary,
        "support_staff_cost": support_staff * labor.support.salary,
    })
    costs *= 1 + labor.overhead_rate
    costs["labor_cost"] = costs.sum(axis=1)
    return costs


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

    Maintenance downtime rotates across the fleet, so availability is
    already accounted for in fleet sizing and is not applied again when
    distributing flights. Spare capacity reduces average cycles per aircraft.

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

# --- Annual deployment schedule ---------------------------------------------


def deployment_schedule(inputs: Inputs) -> pd.DataFrame:
    """Build the annual aircraft deployment and cost table.

    Indexed by year, from the first development or construction activity
    through the last year of the deployment pattern.

    Aircraft development precedes the first aircraft order. Base construction
    proceeds independently, so its lead time can overlap development and
    aircraft procurement. Purchases anticipate demand and replacements;
    manufacturing capacity is not constrained.

    Demand is summed across latitudes. Every flight carries the full solved
    payload, so deployed_mass can exceed demand after flights are rounded up.
    Fuel, material purchases, and operating labor follow actual flights.
    Maintenance follows all aircraft and basing capacity retained in service.

    Principal outputs:
        demand                   Requested deployment, kg/year.
        deployed_mass            Actual deployment, kg/year.
        flights                  Whole flights flown during the year.
        payload_per_flight       Solved payload, kg.
        fuel_per_flight          Fuel burned, excluding reserve, kg.
        fuel_kg                  Total annual fuel burned.
        aircraft_required        Minimum owned fleet required.
        aircraft_active          Owned fleet retained in service.
        base_units_required      Minimum basing capacity blocks required.
        base_units_active        Capacity blocks retained in service.
        basing_capacity          Available movements per year.
        capacity                 Deployment capacity, kg/year, limited by
                                 aircraft availability or basing throughput.
        utilization              Requested demand / capacity; NaN when
                                 capacity is zero.
        aircrew, ground_crew,
        support_staff, workers   Annual personnel headcounts.
        development_cost         Aircraft non-recurring engineering.
        capex                    Aircraft and basing capital spending.
        opex                     Labor, fuel, deployed material, and maintenance.
        total_cost               Development + capex + opex.

    Additional columns expose mission durations, crew requirements, asset
    deliveries and retirements, orders, and individual cost components.
    Capacity is an infrastructure throughput measure; it does not impose
    an additional limit from the workforce sized for actual flights.
    """
    method = AircraftMethod(**inputs.method)
    options = AircraftOptions(**inputs.scenario.method_options)
    design = find_design(method, options)
    profile = find_profile(design, inputs.scenario.altitude)
    fuel = find_fuel(inputs.materials, method.aircraft.fuel)
    material = inputs.materials[inputs.scenario.deployed_material]

    operations = method.operations
    basing = method.basing
    development = design.development

    emission_rate = (
        options.payload_emission_rate
        if options.payload_emission_rate is not None
        else design.payload_emission_rate
    )
    mission = calculate_mission(
        design,
        profile,
        emission_rate,
        shift_hours=method.labor.shift_hours,
    )

    # The longest independent preparation timeline determines program start.
    # Pass zero development years because the aircraft timeline below already
    # includes development; basing must not wait for it to finish.
    preparation_years = max(
        development.duration_years + design.lead_time_years,
        basing.build_years,
    )
    years = find_program_years(
        inputs.pattern,
        lead_time_years=preparation_years,
        development_years=0,
    )
    demand = inputs.pattern.sum(axis=1).reindex(years, fill_value=0.0)
    first_deployment_year = int(demand.index[demand > 0].min())

    flights = calculate_flights(demand, mission.payload)
    flights_per_aircraft_per_year = (
        operations.operating_hours_per_day
        * operations.operating_days_per_year
        / mission.cycle_hours
    )
    aircraft_required = calculate_aircraft_required(
        flights,
        flights_per_aircraft_per_year,
        design.availability,
        operations.excess_capacity,
    )
    fleet = schedule_aircraft(
        flights,
        aircraft_required,
        design.lifetime_years,
        design.lifetime_cycles,
    )

    base_units_required = calculate_basing_required(flights, basing)
    bases = schedule_assets(base_units_required, basing.lifetime_years)
    bases = bases.rename(columns={
        "entering_service": "base_units_entering_service",
        "retiring": "base_units_retiring",
        "active": "base_units_active",
    })

    schedule = pd.DataFrame({
        "demand": demand,
        "flights": flights,
        "deployed_mass": flights * mission.payload,
        "base_units_required": base_units_required,
    }).join(fleet).join(bases)

    # Mission properties are constant within a case, including a sweep case.
    schedule["payload_emission_rate"] = emission_rate
    schedule["payload_per_flight"] = mission.payload
    schedule["cruise_fuel_per_flight"] = mission.cruise_fuel
    schedule["fuel_per_flight"] = mission.fuel_burned
    schedule["cruise_hours"] = mission.cruise_hours
    schedule["flight_hours"] = mission.flight_hours
    schedule["cycle_hours"] = mission.cycle_hours
    schedule["crews_per_flight"] = mission.crews_per_flight
    schedule["flights_per_aircraft_per_year"] = flights_per_aircraft_per_year
    schedule["fuel_kg"] = flights * mission.fuel_burned

    # Excess capacity affects procurement, not physical aircraft productivity.
    # Availability is applied once when calculating owned-fleet throughput.
    schedule["aircraft_capacity"] = (
        schedule["aircraft_active"]
        * flights_per_aircraft_per_year
        * design.availability
        * mission.payload
    )
    schedule["basing_capacity"] = (
        schedule["base_units_active"] * basing.annual_movements_per_unit
    )
    basing_mass_capacity = (
        schedule["basing_capacity"]
        / basing.movements_per_flight
        * mission.payload
    )
    schedule["capacity"] = pd.concat(
        [schedule["aircraft_capacity"], basing_mass_capacity], axis=1
    ).min(axis=1)
    schedule["utilization"] = (
        demand / schedule["capacity"].where(schedule["capacity"] > 0)
    )

    schedule["aircrew"] = calculate_aircrew(
        flights, mission, design, method.labor
    )
    schedule["ground_crew"] = calculate_ground_crew(
        flights, design, method.labor
    )
    schedule["support_staff"] = calculate_support_staff(
        schedule["aircrew"], schedule["ground_crew"], method.labor
    )
    schedule["workers"] = schedule[
        ["aircrew", "ground_crew", "support_staff"]
    ].sum(axis=1)
    schedule = schedule.join(calculate_labor_costs(
        schedule["aircrew"],
        schedule["ground_crew"],
        schedule["support_staff"],
        method.labor,
    ))

    # Development ends when the first aircraft order is placed, regardless
    # of whether base construction began earlier.
    first_aircraft_order = first_deployment_year - design.lead_time_years
    development_start = first_aircraft_order - development.duration_years
    development_years = pd.RangeIndex(
        development_start, first_aircraft_order, name="year"
    )
    schedule["development_cost"] = spread_development(
        development_years, development.NRE, development.duration_years
    ).reindex(years, fill_value=0.0)

    schedule["aircraft_ordered"] = schedule[
        "aircraft_entering_service"
    ].shift(-design.lead_time_years, fill_value=0)
    schedule["base_units_ordered"] = schedule[
        "base_units_entering_service"
    ].shift(-basing.build_years, fill_value=0)

    # Basing's price is capital per unit of annual movement capacity.
    base_unit_cost = (
        basing.cost_per_annual_movement * basing.annual_movements_per_unit
    )
    schedule["aircraft_capex"] = spread_capital(
        schedule["aircraft_entering_service"],
        design.unit_cost,
        design.lead_time_years,
    )
    schedule["basing_capex"] = spread_capital(
        schedule["base_units_entering_service"],
        base_unit_cost,
        basing.build_years,
    )
    schedule["capex"] = (
        schedule["aircraft_capex"] + schedule["basing_capex"]
    )

    schedule["aircraft_maintenance_cost"] = (
        schedule["aircraft_active"]
        * design.unit_cost
        * design.maintenance_rate
    )
    schedule["basing_maintenance_cost"] = (
        schedule["base_units_active"]
        * base_unit_cost
        * basing.maintenance_rate
    )
    schedule["maintenance_cost"] = (
        schedule["aircraft_maintenance_cost"]
        + schedule["basing_maintenance_cost"]
    )
    schedule["fuel_cost"] = schedule["fuel_kg"] * fuel.cost
    schedule["deployed_material_cost"] = (
        schedule["deployed_mass"] * material.cost
    )
    schedule["opex"] = schedule[[
        "maintenance_cost",
        "fuel_cost",
        "deployed_material_cost",
        "labor_cost",
    ]].sum(axis=1)
    schedule["total_cost"] = (
        schedule["development_cost"] + schedule["capex"] + schedule["opex"]
    )
    return schedule