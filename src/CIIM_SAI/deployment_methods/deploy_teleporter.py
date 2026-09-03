"""Cost and schedule model for the fictional teleporter deployment method.

Defines the shape of inputs/deployment_methods/teleporter.yaml and computes the
annual program table from it. Reads no files: everything arrives via Inputs.

A deployment method module's entire public interface is
deployment_schedule(inputs) -> DataFrame indexed by year. run.py imports it by
name (deploy_<method>) and calls that one function; everything else here is
private to this method. The module also owns the schema for its own YAML file,
which load_inputs passes through unvalidated as a raw dict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import Field

from CIIM_SAI.load_inputs import Frozen, Inputs



# --- Classes from teleporter.yaml -----------------------------------------
# One class per nesting level in the YAML. 

class LaborRole(Frozen):
    count: int = Field(ge=0, description="People required per teleporter")
    salary: float = Field(ge=0, description="USD per person per year")


class Consumption(Frozen):
    kg_per_year_per_altitude: float = Field(
        ge=0, description="kg per teleporter per year, per metre of altitude"
    )


class Development(Frozen):
    NRE: float = Field(ge=0, description="Total non-recurring engineering cost, USD")
    duration_years: int = Field(gt=0, description="Years of development before the first order")


class Unit(Frozen):
    cost: float = Field(ge=0, description="Purchase price of one teleporter, USD")
    lifetime_years: int = Field(gt=0, description="Years in service before replacement")
    capacity_per_year: float = Field(gt=0, description="kg delivered per teleporter per year")
    lead_time_years: int = Field(ge=0, description="Years from order to entering service")
    labor: dict[str, LaborRole]
    consumption: dict[str, Consumption]


class Teleporter(Frozen):
    """The contents of teleporter.yaml. Keys named by the user (teleportationist, unobtanium)
    are dict keys; the model does not need to enumerate them."""

    development: Development
    unit: Unit


# --- Functions to enable calculation of deployment requirements ----------------------------------------------------------


def determine_units_required(demand: pd.Series, capacity: float) -> pd.Series:
    """Teleporters that must be in service each year to deliver `demand`."""
    # Rounded before the ceiling: 2.0000000000000004 units of demand is a float
    # artefact, and ceiling it would buy a whole spurious teleporter.
    return np.ceil((demand / capacity).round(9)).astype(int)


def build_fleet(units_required: pd.Series, lifetime_years: int) -> pd.DataFrame:
    """Entries, retirements and fleet size, year by year.

    A teleporter entering service in year y retires at y + lifetime_years, so a
    year's retirements are the entries from lifetime_years ago
    The loop carries a single number: how many are currently in service.

    Teleporters never retire early, so overcapacity is possible.
    """
    entering: dict[int, int] = {}
    retiring: dict[int, int] = {}
    active: dict[int, int] = {}
    fleet = 0

    for year, required in units_required.items():
        retiring[year] = entering.get(year - lifetime_years, 0)
        fleet -= retiring[year]
        entering[year] = max(0, int(required) - fleet)
        fleet += entering[year]
        active[year] = fleet

    return pd.DataFrame(
        {"entering_service": entering, "retiring": retiring, "active": active}
    ).rename_axis("year")


# --- Cost spreading ----------------------------------------------------------



def spread_development(
    years: pd.RangeIndex, first_order_year: int, development: Development
) -> pd.Series:
    """NRE spread evenly over the years ending just before the first order.

    Development must finish before anything can be ordered, so the window is
    [first_order - duration, first_order - 1].
    """
    spend = pd.Series(0.0, index=years)
    window_start = first_order_year - development.duration_years
    # .loc slicing on an integer index includes both endpoints.
    spend.loc[window_start : first_order_year - 1] = development.NRE / development.duration_years
    return spend



def spread_capital(entering_service: pd.Series, cost: float, lead_time_years: int) -> pd.Series:
    """Unit cost paid evenly across the lead time, completing at delivery.

    A teleporter entering service in year d is paid for in even installments across years
    d - lead_time .. d - 1, so a year's capital outlay covers every unit due to
    arrive within the lead time ahead. With no lead time the whole cost falls in
    the delivery year.

    Even spreading is a financing assumption: real procurement uses deposits and
    milestone payments. It is neutral, and it only affects the ramp-up and the
    tail — for a fleet in steady replacement the annual totals are identical.
    """
    if lead_time_years == 0:
        return entering_service * float(cost)
    arriving_soon = sum(entering_service.shift(-k) for k in range(1, lead_time_years + 1))
    return arriving_soon.fillna(0.0) * (cost / lead_time_years)


def deployment_schedule(inputs: Inputs) -> pd.DataFrame:
    """Build the year-by-year program table for meeting inputs.pattern.

    Indexed by year, from the start of development through the last year of the
    deployment pattern. All masses in kg, all costs in USD of the currency year.

    Deployment and fleet:
        demand            kg of the deployed material, summed across latitudes
        units_required    teleporters that must be in service to meet demand
        entering_service  teleporters entering service
        retiring          teleporters reaching the end of their life
        active            teleporters in service
        capacity          kg deliverable by the active fleet
        utilization       demand / capacity; NaN before any fleet exists
        ordered           teleporters ordered, lead_time_years before service

    Costs:
        development_cost        NRE, spread over the years before the first order
        capex                   unit purchases, spread across the lead time
        labor_cost              crew for the active fleet
        consumables_cost        materials consumed by the active fleet
        deployed_material_cost  the deployed material itself
        opex                    labor + consumables + deployed material
        total_cost               development + capex + opex

    Plus one <material>_kg column per entry in the method's consumption block —
    so the column set varies with the YAML, not just with the method.
    """
      
    teleporter = Teleporter(**inputs.method)
    unit = teleporter.unit
    pattern = inputs.pattern

    # Work backwards from the first year anything is deployed: teleporters must
    # be in service by then, so they were ordered lead_time_years earlier, and
    # development had to finish before that first order.
    first_deployment = int(pattern.index[pattern.sum(axis=1) > 0].min())
    first_order = first_deployment - unit.lead_time_years
    years = pd.RangeIndex(
        first_order - teleporter.development.duration_years,
        int(pattern.index.max()) + 1,
        name="year",
    )    
    demand = inputs.pattern.sum(axis=1).reindex(years, fill_value=0.0)

    units_required = determine_units_required(demand, unit.capacity_per_year)
    fleet = build_fleet(units_required, unit.lifetime_years)

    schedule = pd.DataFrame({"demand": demand, "units_required": units_required}).join(fleet)
    schedule["capacity"] = schedule["active"] * unit.capacity_per_year
    schedule["utilization"] = schedule["demand"] / schedule["capacity"]
    schedule["ordered"] = schedule["entering_service"].shift(-unit.lead_time_years).fillna(0).astype(int)


    # Now that capacity requirements are determined, calculate the cost and other requirements to fulfill them.

    schedule["development_cost"] = spread_development(years, first_order, teleporter.development)
    schedule["capex"] = spread_capital(
        schedule["entering_service"], unit.cost, unit.lead_time_years
    )

    labor_per_unit = sum(role.count * role.salary for role in unit.labor.values())
    schedule["labor_cost"] = schedule["active"] * labor_per_unit

    consumables_cost = pd.Series(0.0, index=years)
    for name, consumption in unit.consumption.items():
        kg = schedule["active"] * consumption.kg_per_year_per_altitude * inputs.scenario.altitude
        schedule[f"{name}_kg"] = kg
        consumables_cost = consumables_cost + kg * inputs.materials[name].cost
    schedule["consumables_cost"] = consumables_cost

    deployed_material = inputs.materials[inputs.scenario.deployed_material]
    schedule["deployed_material_cost"] = schedule["demand"] * deployed_material.cost

    schedule["opex"] = schedule[["labor_cost", "consumables_cost", "deployed_material_cost"]].sum(axis=1)
    schedule["total_cost"] = (
        schedule["development_cost"] + schedule["capex"] + schedule["opex"]
    )
    return schedule

