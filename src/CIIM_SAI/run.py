"""Running the model: expand the parameter sweep, run each case, collect results."""

from __future__ import annotations

import itertools
from importlib import import_module
from types import ModuleType

import pandas as pd

from CIIM_SAI.load_inputs import Inputs, SweepRange, load_inputs, with_overrides


def get_method(name: str) -> ModuleType:
    """Import the module implementing deployment method `name`.
    Each deployment method corresponds to a .py file under inputs/deployment_methods"""
    return import_module(f"CIIM_SAI.deployment_methods.deploy_{name}")


def parameter_sweep(sweep: dict[str, SweepRange]) -> list[dict[str, float]]:
    """Expand a sweep block into one set of parameter values per case.

    An empty sweep block gives a single empty case, so everything downstream
    handles swept and unswept runs identically.
    """
    if not sweep:
        return [{}]
    names = list(sweep)
    grids = [sweep[name].values() for name in names]
    return [dict(zip(names, values)) for values in itertools.product(*grids)]


def run(base: Inputs | None = None) -> pd.DataFrame:
    """Run every case in the sweep and concatenate the results.

    One row per (case, year). Swept parameters appear as columns, so a sweep and
    a single run have the same shape and nothing downstream branches on which
    it got.
    """
    base = base if base is not None else load_inputs()
    # use the inputs provided if there are any; otherwise load inputs using the usual process

    schedules = []
    for case_id, combo in enumerate(parameter_sweep(base.scenario.sweep)):
        case = with_overrides(base, combo)
        method = get_method(case.scenario.deployment_method)
        schedule = method.deployment_schedule(case).reset_index()
        schedule.insert(0, "case", case_id)
        # insert a case ID into the output of the model to track which case it refers to
        for position, (name, value) in enumerate(combo.items(), start=1):
            schedule.insert(position, name, value)
        schedules.append(schedule)

    return pd.concat(schedules, ignore_index=True)


