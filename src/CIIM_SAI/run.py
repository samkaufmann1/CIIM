"""Running the model: expand the parameter sweep, run each case, collect results."""

from __future__ import annotations

import itertools
from importlib import import_module
from types import ModuleType

import pandas as pd

from CIIM_SAI.load_inputs import Inputs, load_inputs, with_overrides



def get_method(name: str) -> ModuleType:
    """Import the module implementing deployment method `name`.
    Each deployment method corresponds to a .py file under inputs/deployment_methods"""
    return import_module(f"CIIM_SAI.deployment_methods.deploy_{name}")


def parameter_sweep(base: Inputs) -> list[Inputs]:
    """Expand the scenario's sweep block into one Inputs per combination of values.

    A scenario with no sweep block gives a one-element list, so everything
    downstream handles swept and unswept runs identically.
    """
    if not base.scenario.sweep:
        return [base]
    names = list(base.scenario.sweep)
    grids = [base.scenario.sweep[name].values() for name in names]
    return [with_overrides(base, dict(zip(names, combo))) for combo in itertools.product(*grids)]

def run(base: Inputs | None = None) -> pd.DataFrame:
    """Run every case in the sweep and concatenate the results.

    One row per (case, year). Swept parameters appear as columns, so a sweep and
    a single run have the same shape and nothing downstream branches on which
    it got.
    """
    base = base if base is not None else load_inputs() 
    # use the inputs provided if there are any; otherwise load inputs using the usual process
    swept = list(base.scenario.sweep)
    # identify parameters being swept

    schedules = []
    for case_id, case in enumerate(parameter_sweep(base)):
        method = get_method(case.scenario.deployment_method)
        schedule = method.deployment_schedule(case).reset_index()
        schedule.insert(0, "case", case_id)
        # insert a case ID into the output of the model to track which case it refers to
        for position, name in enumerate(swept, start=1):
            schedule.insert(position, name, getattr(case.scenario, name))
        schedules.append(schedule)

    return pd.concat(schedules, ignore_index=True)



