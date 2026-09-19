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

import pandas as pd
from pydantic import Field

from CIIM_SAI.deployment_methods.scheduling import (
    determine_units_required,
    find_program_years,
    schedule_assets,
    spread_capital,
    spread_development,
)

from CIIM_SAI.load_inputs import Frozen, Inputs



# --- Classes from teleporter.yaml -----------------------------------------
# One class per nesting level in the YAML. 

class LaborRole(Frozen):
    count: int = Field(ge=0, description="People required per teleporter")
    salary: float = Field(ge=0, description="USD per person per year")


class Consumption(Frozen):
    kg_per_year_per_altitude: float = Field(
        ge=0, description="kg per teleporter per year, per meter of altitude"
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


class TeleporterMethod(Frozen):
    """The contents of teleporter.yaml. Keys named by the user (teleportationist, unobtanium)
    are dict keys; the model does not need to enumerate them."""

    development: Development
    unit: Unit



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
      
    teleporter = TeleporterMethod(**inputs.method)
    unit = teleporter.unit

    years = find_program_years(
        inputs.pattern, unit.lead_time_years, teleporter.development.duration_years
    )
    demand = inputs.pattern.sum(axis=1).reindex(years, fill_value=0.0)

    units_required = determine_units_required(demand, unit.capacity_per_year)
    fleet = schedule_assets(units_required, unit.lifetime_years)

    schedule = pd.DataFrame({"demand": demand, "units_required": units_required}).join(fleet)
    schedule["capacity"] = schedule["active"] * unit.capacity_per_year
    schedule["utilization"] = schedule["demand"] / schedule["capacity"]
    schedule["ordered"] = schedule["entering_service"].shift(-unit.lead_time_years).fillna(0).astype(int)


    # Now that capacity requirements are determined, calculate the cost and other requirements to fulfill them.

    schedule["development_cost"] = spread_development(
        years, teleporter.development.NRE, teleporter.development.duration_years
    )
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

