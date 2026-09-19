"""Cost and schedule model for balloon-borne deployment.

Spherical latex balloons are filled with a lift gas and with the deployed material as a
payload gas, released from launch facilities, and burst at the injection
altitude, releasing both. Balloons are disposable, so one is consumed per launch; the capital in
this method is launch facilities, launchpads, and the drones that collect the
debris.

A key future capability to be implemented is non-elastic plastic balloons. These
would have non-spherical geometries, such as cylinders or tetroons. Since this
module assumes spherical balloons, it will need to be modified to cover those cases.

Defines the shape of inputs/deployment_methods/balloon.yaml. Reads no files:
everything arrives via Inputs.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from pydantic import Field, model_validator

from CIIM_SAI.climatology import (
    GAS_CONSTANT,
    MOLAR_MASS_AIR,
    find_atmospheric_temperature_and_pressure,
)
from CIIM_SAI.deployment_methods.scheduling import (
    determine_units_required,
    find_program_years,
    schedule_assets,
    spread_capital,
    spread_development,
)
from CIIM_SAI.load_inputs import Frozen, Inputs, Material


def find_gas_fill(
    balloon_mass: float,          # kg, the empty envelope
    burst_diameter: float,        # m, the diameter the latex bursts at
    altitude: float,              # m, where the balloon is to reach that diameter
    buoyancy_margin: float,       # net lift as a fraction of displaced air mass; proportion between 0 and 1
    molar_mass_lift: float,       # kg/mol
    molar_mass_payload: float,    # kg/mol
) -> tuple[float, float]:
    """Kilograms of lift gas and of payload gas in one balloon.

    The fill is set by the altitude at which the balloon is meant to burst. A
    balloon bursts when the latex reaches its burst diameter, so a larger fill
    reaches that diameter lower down: the target altitude fixes the volume's
    pressure and temperature, and so fixes how many moles of gas go in. Aiming
    higher means less gas, and therefore less lift and less payload.

    Two facts make the solve a system of two linear equations rather than an ascent
    simulation.

        Free lift does not change with altitude. These balloons are not pressurized, so the
        gas inside sits at ambient pressure and temperature, and the ratio of
        gas density to air density stays at the ratio of their molar masses all
        the way up. The mass of gas is fixed, so the displaced air mass --
        density of air times volume -- is fixed too. This is why the balance 
        can be written at burst altitude, where volume is known because it is,
        by definition, the maximum volume of the balloon.

        Displaced air mass is just total moles times the molar mass of air.
        Equal volumes at equal pressure and temperature hold equal moles, so
        the air pushed aside has the same mole count as the gas inside, and
        there is no need to compute a density at all.

    Writing N for total moles at burst, the two equations are

        n_lift + n_payload = N = P * V / (R * T)
        balloon_mass + n_lift * M_lift + n_payload * M_payload
            = (1 - buoyancy_margin) * N * M_air

    the second being Archimedes' principle with the margin held back as net lift.
    Substituting the first into the second and solving for n_lift gives the
    expression below.

    Raises ValueError if an input is out of range, or if the solve does not land
    on a physical fill. Both gas counts must come out non-negative, and the two
    ways of failing have different causes:

        moles_payload < 0 -- the envelope is too heavy for the air it displaces.
        At high enough altitude even an all-lift-gas balloon cannot carry the
        envelope while holding back the margin.

        moles_lift < 0 -- the fill needs no lift gas, because the payload gas is
        light enough on its own. This model assumes a payload gas denser than
        air, so this points at molar_mass_payload rather than at altitude.

    Both messages are phrased for molar_mass_lift < molar_mass_payload. The
    algebra is symmetric in the two gases and needs only that their molar masses
    differ, so swapping the arguments still returns a correct fill with the two
    values swapped -- but a failure will then be reported under the other
    message. The underlying condition either way is that
    (1 - buoyancy_margin) * M_air - balloon_mass / N, the mean molar mass the
    fill must hit, falls between the two molar masses.
    """
    if not 0.0 < buoyancy_margin < 1.0:
        raise ValueError(
            f"buoyancy_margin is net lift as a fraction of displaced air mass and "
            f"must lie strictly between 0 and 1; got {buoyancy_margin!r}. At 0 the "
            f"balloon is neutrally buoyant and never ascends; at 1 or above it "
            f"would have to weigh nothing at all. A margin passed as a percentage "
            f"rather than a fraction will trigger this error message."
        )
    if molar_mass_lift == molar_mass_payload: # IDK if this error check is really that important, but it doesn't hurt
        raise ValueError(
            "molar_mass_lift and molar_mass_payload are equal, so the fill is "
            "singular: every split of the moles gives the same total mass"
        )

    temperature, pressure = find_atmospheric_temperature_and_pressure(altitude)

    volume = 4.0 / 3.0 * math.pi * (burst_diameter / 2.0) ** 3
    moles_total = pressure * volume / (GAS_CONSTANT * temperature)
    displaced_air_mass = moles_total * MOLAR_MASS_AIR

    moles_lift = (
        moles_total * ((1.0 - buoyancy_margin) * MOLAR_MASS_AIR - molar_mass_payload)
        - balloon_mass
    ) / (molar_mass_lift - molar_mass_payload)
    moles_payload = moles_total - moles_lift

    if moles_payload < 0.0:
        raise ValueError(
            f"a {balloon_mass:,.1f} kg balloon bursting at {altitude:,.0f} m displaces "
            f"{displaced_air_mass:,.2f} kg of air, which cannot lift the envelope and a "
            f"{buoyancy_margin:.0%} margin even filled entirely with lift gas"
        )
    if moles_lift < 0.0:
        raise ValueError(
            f"the payload gas is buoyant enough at {altitude:,.0f} m to need no lift gas; "
            f"this model assumes a payload gas denser than air"
        )

    return moles_lift * molar_mass_lift, moles_payload * molar_mass_payload



# --- Classes from balloon.yaml -----------------------------------------------
# One class per nesting level in the YAML.


class Development(Frozen):
    NRE: float = Field(ge=0, description="Total non-recurring engineering cost, USD")
    duration_years: int = Field(gt=0, description="Years of development before the first order")


class BalloonDesign(Frozen):
    """One entry under balloon.designs: a balloon that can be bought and flown."""

    mass: float = Field(gt=0, description="Empty envelope, kg")
    burst_diameter: float = Field(gt=0, description="Diameter the latex bursts at, m")
    cost: float = Field(ge=0, description="Purchase price of one balloon, USD")
    launch_time_hours: float = Field(gt=0, description="Pad time to fill and release one balloon")
    launch_crew: int = Field(ge=0, description="People needed to launch one balloon")
    source: str


class Balloon(Frozen):
    lift_gas: str = Field(description="A material in material.yaml, carrying a molar mass")
    buoyancy_margin: float = Field(
        gt=0, lt=1, description="Net lift as a fraction of displaced air mass"
    )
    designs: dict[str, BalloonDesign]

    @model_validator(mode="after")
    def at_least_one_design(self) -> Balloon:
        if not self.designs:
            raise ValueError("balloon.designs needs at least one design to launch")
        return self


class Pad(Frozen):
    cost: float = Field(ge=0, description="USD per launchpad")
    lifetime_years: int = Field(gt=0, description="Years in service before replacement")


class Facility(Frozen):
    pads: int = Field(gt=0, description="Launchpads one facility holds")
    cost: float = Field(ge=0, description="USD per facility, excluding its pads")
    build_years: int = Field(ge=0, description="Years from order to operating")
    lifetime_years: int = Field(gt=0, description="Years in service before replacement")


class Launch(Frozen):
    """Assumptions shared by every launchpad and facility."""

    operating_hours_per_day: float = Field(gt=0, le=24) # lol thanks claude for making sure there aren't more than 24 hours in a day
    operating_days_per_year: float = Field(gt=0, le=366)
    support_staff_per_crew: float = Field(ge=0, description="Non-launch staff per launch crew")
    maintenance_rate: float = Field(ge=0, description="Per year, against installed capital")
    misc_operating_rate: float = Field(ge=0, description="Per year, against installed capital")
    pad: Pad
    facility: Facility


class DebrisCollection(Frozen):
    drone_cost: float = Field(ge=0, description="USD per drone")
    drone_lifetime_years: int = Field(gt=0)
    drone_availability: float = Field(gt=0, le=1, description="Fraction of the owned fleet flyable")
    flight_distance: float = Field(ge=0, description="One way to the dump site, m")
    flight_speed: float = Field(gt=0, description="Averaged over the mission, m/s")
    ground_cycle_seconds: float = Field(ge=0, description="Per catch, on the ground")
    ground_crew_overhead: float = Field(
        ge=0, description="Crew hours per hour of drone ground cycle"
    )


class Labor(Frozen):
    salary: float = Field(ge=0, description="Pay plus overhead, USD per person per year")


class BalloonMethod(Frozen):
    """The contents of balloon.yaml.

    Named for the file rather than for the balloon block inside it, which the
    Balloon class above describes. Design names (weather_4kg) are dict keys the
    model does not enumerate, as labor roles and consumables are elsewhere.
    """

    development: Development
    balloon: Balloon
    launch: Launch
    debris_collection: DebrisCollection
    labor: Labor


class BalloonOptions(Frozen):
    """The scenario's method_options when the deployment method is balloon."""

    design: str = Field(description="A key under balloon.designs in balloon.yaml")


# --- Checks that span more than one input file -------------------------------
# Neither of these can live in a schema: each compares something the scenario
# names against something another file defines, so they belong where both are
# in hand, like Inputs.__post_init__.


def find_design(method: BalloonMethod, options: BalloonOptions) -> BalloonDesign:
    """The design the scenario selected, from the ones the method file offers."""
    design = method.balloon.designs.get(options.design)
    if design is None:
        raise ValueError(
            f"method_options.design is {options.design!r}, which balloon.yaml does not "
            f"define. Designs offered: {', '.join(sorted(method.balloon.designs))}"
        )
    return design


def find_gas(materials: dict[str, Material], name: str, role: str) -> Material:
    """A gas from material.yaml, with the molar mass this method needs.

    molar_mass is optional on Material, since a material moved as a bulk mass
    has no use for one, so a gas without it fails here rather than as an
    arithmetic error further down. `role` names which gas for the message.
    """
    material = materials.get(name)
    if material is None:
        raise ValueError(
            f"the {role} is {name!r}, which material.yaml does not define. "
            f"Materials: {', '.join(sorted(materials))}"
        )
    if material.molar_mass is None:
        raise ValueError(
            f"the {role} {name!r} has no molar_mass in material.yaml, which balloon "
            f"deployment needs in order to size the gas fill"
        )
    return material

# --- Deployment operations ---------------------------------------------------------------


def find_launches_per_pad(launch: Launch, design: BalloonDesign) -> float:
    """Balloons one launchpad can release in a year."""
    return launch.operating_hours_per_day * launch.operating_days_per_year / design.launch_time_hours


def find_catches_per_drone(debris: DebrisCollection, launch: Launch) -> float:
    """Balloons one flying drone can recover in a year.

    A cycle is the round trip to the dump site plus the time on the ground
    dropping the envelope and swapping batteries. Drones are taken to work the
    same hours as the launchpads: balloons burst hours after release and drift
    far, so landings really spread past the launch window, but modelling that
    needs a dispersal model the rest of this method does not have.
    """
    cycle_seconds = 2.0 * debris.flight_distance / debris.flight_speed + debris.ground_cycle_seconds
    operating_seconds = launch.operating_hours_per_day * launch.operating_days_per_year * 3600.0
    return operating_seconds / cycle_seconds


def count_facilities(
    launchpads_required: pd.Series, pads_per_facility: int, deployed_latitudes: int
) -> pd.Series:
    """Facilities needed to hold the launchpads, one per latitude at minimum.

    Balloons are launched where they are to be injected, so a program covering
    several latitudes needs a facility at each however few pads it takes. The
    floor applies only in years that deploy: before the first launch there is
    nothing to hold.
    """
    by_capacity = determine_units_required(launchpads_required, pads_per_facility)
    return by_capacity.clip(lower=deployed_latitudes).where(launchpads_required > 0, 0)


def deployment_schedule(inputs: Inputs) -> pd.DataFrame:
    """Build the year-by-year program table for meeting inputs.pattern.

    Indexed by year, from the start of development through the last year of the
    deployment pattern. All masses in kg, all costs in USD of the currency year.

    Deployment:
        demand                  kg of the deployed material, summed across latitudes
        payload_gas_per_balloon kg carried by one balloon; fixed by the altitude
        lift_gas_per_balloon    kg of lift gas in one balloon
        balloons                launches in the year
        capacity                kg deliverable by the active launchpads
        utilization             demand / capacity; NaN before any pads exist

    Infrastructure; * is entering / retiring / active:
        launchpads_*            pads sized by launches per pad per year
        facilities_*            facilities holding those pads, one per latitude at minimum
        drones_*                drones owned, flying ones divided by availability
        launchpads_required     pads demand calls for; below active where capacity is idle
        facilities_required     facilities demand calls for, on the same basis
        drones_flying           drones needed in the air

    Labor:
        launch_workers          launch crews plus support staff on active pads
        drone_pilots            one per flying drone
        ground_crew             servicing drones between flights
        workers                 all three

    Costs:
        development_cost        NRE, spread over the years before the first order
        capex                   pads, facilities and drones
        maintenance_cost        against all capital in service, used or not
        misc_operating_cost     utilities and consumables, against capital in use
        balloon_cost            the balloons themselves, consumed one per launch
        lift_gas_cost           lift gas, with lift_gas_kg the quantity
        deployed_material_cost  the payload gas
        labor_cost              currently just number of workers multiplied by a single cost per worker
        opex                    maintenance_cost, misc_operating_cost, balloon_cost,
                                lift_gas_cost, deployed_material_cost, and labor_cost
        total_cost              development + capex + opex
    """
    method = BalloonMethod(**inputs.method)
    options = BalloonOptions(**inputs.scenario.method_options)
    design = find_design(method, options)
    lift_gas = find_gas(inputs.materials, method.balloon.lift_gas, "lift gas")
    payload_gas = find_gas(inputs.materials, inputs.scenario.deployed_material, "payload gas")

    launch = method.launch
    pad = launch.pad
    facility = launch.facility
    debris = method.debris_collection

    # One balloon's fill is fixed by the injection altitude, so it is the same
    # in every year of this case.
    lift_per_balloon, payload_per_balloon = find_gas_fill(
        design.mass, design.burst_diameter, inputs.scenario.altitude,
        method.balloon.buoyancy_margin, lift_gas.molar_mass, payload_gas.molar_mass,
    )

    years = find_program_years(
        inputs.pattern, facility.build_years, method.development.duration_years
    )
    demand = inputs.pattern.sum(axis=1).reindex(years, fill_value=0.0)
    deployed_latitudes = int((inputs.pattern.sum(axis=0) > 0).sum())

    balloons = determine_units_required(demand, payload_per_balloon)
    launches_per_pad = find_launches_per_pad(launch, design)
    launchpads_required = determine_units_required(balloons, launches_per_pad)
    facilities_required = count_facilities(launchpads_required, facility.pads, deployed_latitudes)

    catches_per_drone = find_catches_per_drone(debris, launch)
    drones_flying = determine_units_required(balloons, catches_per_drone)
    drones_required = determine_units_required(drones_flying, debris.drone_availability)

    launchpads = schedule_assets(launchpads_required, pad.lifetime_years)
    facilities = schedule_assets(facilities_required, facility.lifetime_years)
    drones = schedule_assets(drones_required, debris.drone_lifetime_years)

    schedule = pd.DataFrame(index=years)
    schedule["demand"] = demand
    schedule["payload_gas_per_balloon"] = payload_per_balloon
    schedule["lift_gas_per_balloon"] = lift_per_balloon
    schedule["balloons"] = balloons

    for name, assets in (("launchpads", launchpads), ("facilities", facilities), ("drones", drones)):
        schedule[f"{name}_entering"] = assets["entering_service"]
        schedule[f"{name}_retiring"] = assets["retiring"]
        schedule[f"{name}_active"] = assets["active"]
    schedule["launchpads_required"] = launchpads_required
    schedule["facilities_required"] = facilities_required
    schedule["drones_flying"] = drones_flying

    schedule["capacity"] = schedule["launchpads_active"] * launches_per_pad * payload_per_balloon
    schedule["utilization"] = schedule["demand"] / schedule["capacity"]

    operating_hours = launch.operating_hours_per_day * launch.operating_days_per_year
    schedule["launch_workers"] = schedule["launchpads_active"] * (
        design.launch_crew + launch.support_staff_per_crew
    )
    schedule["drone_pilots"] = drones_flying
    schedule["ground_crew"] = np.ceil(
        balloons * (debris.ground_cycle_seconds / 3600.0) * debris.ground_crew_overhead
        / operating_hours
    ).astype(int)
    schedule["workers"] = (
        schedule["launch_workers"] + schedule["drone_pilots"] + schedule["ground_crew"]
    )

    schedule["lift_gas_kg"] = balloons * lift_per_balloon

    schedule["development_cost"] = spread_development(
        years, method.development.NRE, method.development.duration_years
    )
    schedule["capex"] = (
        spread_capital(launchpads["entering_service"], pad.cost, facility.build_years)
        + spread_capital(facilities["entering_service"], facility.cost, facility.build_years)
        + spread_capital(drones["entering_service"], debris.drone_cost, 0)
    )

    # Maintenance keeps an asset from decaying whether or not it is used, so it
    # falls on everything in service. Utilities and consumables only accrue
    # where something is happening, so they fall on what demand actually calls
    # for -- which is less than what is in service wherever a fall in demand has
    # left capacity idle. A facility counts as in use as a whole unit, so one
    # running at a tenth of its pad capacity still draws full utilities.
    installed_capital = (
        schedule["launchpads_active"] * pad.cost + schedule["facilities_active"] * facility.cost
    )
    operating_capital = (
        schedule["launchpads_required"] * pad.cost
        + schedule["facilities_required"] * facility.cost
    )
    schedule["maintenance_cost"] = installed_capital * launch.maintenance_rate
    schedule["misc_operating_cost"] = operating_capital * launch.misc_operating_rate
    schedule["balloon_cost"] = balloons * design.cost
    schedule["lift_gas_cost"] = schedule["lift_gas_kg"] * lift_gas.cost
    schedule["deployed_material_cost"] = demand * payload_gas.cost
    schedule["labor_cost"] = schedule["workers"] * method.labor.salary

    schedule["opex"] = schedule[[
        "maintenance_cost", "misc_operating_cost", "balloon_cost",
        "lift_gas_cost", "deployed_material_cost", "labor_cost",
    ]].sum(axis=1)
    schedule["total_cost"] = (
        schedule["development_cost"] + schedule["capex"] + schedule["opex"]
    )
    return schedule