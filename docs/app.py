from CIIM_SAI.load_inputs import load_inputs
from CIIM_SAI.run import run

results = None


def run_model() -> str:
    """Run the packaged scenario; keep results for export; return a summary."""
    global results
    inputs = load_inputs()
    scenario = inputs.scenario
    results = run(inputs)

    lines = [
        f"method   : {scenario.deployment_method}",
        f"pattern  : {scenario.deployment_pattern} ({scenario.deployed_material})",
        f"altitude : {scenario.altitude:,.0f} m",
        f"currency : real {inputs.currency_year} USD",
    ]
    for name, sweep in scenario.sweep.items():
        lines.append(f"sweep    : {name} from {sweep.start:g} to {sweep.stop:g} step {sweep.step:g}")

    if results["case"].nunique() == 1:
        lines.append("")
        lines.append(
            results.drop(columns="case").to_string(
                index=False, float_format=lambda v: f"{v:,.0f}"
            )
        )
    else:
        totals = results.groupby(list(scenario.sweep)).agg(
            units_bought=("entering_service", "sum"),
            development=("development_cost", "sum"),
            capex=("capex", "sum"),
            opex=("opex", "sum"),
            total_cost=("total_cost", "sum"),
        )
        lines.append(f"\n{len(totals)} cases:\n")
        lines.append(totals.to_string(float_format=lambda v: f"{v:,.0f}"))
    return "\n".join(lines)


def results_csv() -> str:
    return results.to_csv(index=False)