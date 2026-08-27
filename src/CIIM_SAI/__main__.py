"""To run CIIM_SAI from the command line:

    python -m CIIM_SAI

Loads the packaged inputs, runs every case in the scenario's parameter sweep,
prints a summary, and writes the full table to outputs/.

This way of running the model is for demonstration only.
Loading lives in load_inputs.py and the model in run.py, so
a notebook or a GUI calls those directly and never goes through this file.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from CIIM_SAI.load_inputs import load_inputs
from CIIM_SAI.run import run



# Outputs go to the working directory, never inside the package: once installed,
# the package may live in a read-only site-packages directory.
OUTPUT_DIR = Path.cwd() / "outputs"


def main() -> None:
    inputs = load_inputs()
    scenario = inputs.scenario

    print(f"method   : {scenario.deployment_method}")
    print(f"pattern  : {scenario.deployment_pattern} ({scenario.deployed_material})")
    print(f"altitude : {scenario.altitude:,.0f} m")
    print(f"currency : real {inputs.currency_year} USD")

    # this part only runs if there is a scenario sweep
    # scenario.sweep is a dict. When there's no sweep block in the YAML, pydantic's default_factory=dict makes it an empty dict rather than None.
    # A for loop over an empty collection runs its body zero times, so if there's no sweep, nothing prints.
    for name, sweep in scenario.sweep.items():
        print(f"parameter sweep    : {name} from {sweep.start:g} to {sweep.stop:g} step {sweep.step:g}")

    results = run(inputs)

    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda v: f"{v:,.0f}")

    if results["case"].nunique() == 1:
        print()
        print(results.drop(columns="case").to_string(index=False))
    else:
        totals = results.groupby(list(scenario.sweep)).agg(
            units_bought=("entering_service", "sum"),
            development=("development_cost", "sum"),
            capex=("capex", "sum"),
            opex=("opex", "sum"),
            total_cost=("total_cost", "sum"),
        )
        print(f"\n{len(totals)} cases:\n")
        print(totals.to_string())

    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "deployment_schedule.csv"
    results.to_csv(path, index=False)
    print(f"\nfull table written to {path}")


if __name__ == "__main__":
    main()