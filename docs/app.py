from CIIM_SAI.load_inputs import load_inputs, INPUTS_DIR
from CIIM_SAI.run import run
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots
from pathlib import Path
import shutil
import json
import yaml

results = None
sweep_params: list[str] = []


def materialize_inputs(dest: str = "/ciim_inputs") -> None:
    """Copy the packaged input files into a writable directory.

    The GUI edits files here, and run_model() reads the directory back
    through the normal loader, so GUI input gets exactly the packaged
    inputs' validation.
    """
    dest_path = Path(dest)
    if dest_path.exists():
        shutil.rmtree(dest_path)
    shutil.copytree(INPUTS_DIR, dest_path)

def run_model(inputs_dir: str | None = None) -> str:
    """Run the scenario in inputs_dir (packaged defaults if None); keep results; return a summary."""
    global results, sweep_params
    inputs = load_inputs(Path(inputs_dir)) if inputs_dir else load_inputs()
    scenario = inputs.scenario
    results = run(inputs)
    sweep_params = list(scenario.sweep)

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




# "retro" font and color scheme. I like monospace font.
RETRO = dict(
    font=dict(family='ui-monospace, Consolas, "Courier New", monospace', size=12, color="#333"),
    paper_bgcolor="#fafaf8",
    plot_bgcolor="#fafaf8",
    margin=dict(l=60, r=20, t=50, b=45),
)

AXIS = dict(gridcolor="#e8e8e4", zeroline=False)


def cost_chart() -> str:
    """All cases: total cost vs year, colored by the swept parameter."""
    param = sweep_params[0] if len(sweep_params) == 1 else "case"
    values = sorted(results[param].unique())
    lo, hi = values[0], values[-1]
    swept = hi > lo

    fig = go.Figure()
    for v in values:
        sub = results[results[param] == v].sort_values("year")
        frac = 0.15 + 0.85 * (v - lo) / (hi - lo) if swept else 0.5
        tag = (f"case {v}" if param == "case" else f"{v:g}") if swept else "total cost"
        fig.add_trace(go.Scatter(
            x=sub["year"], y=sub["total_cost"] / 1e9,
            mode="lines",
            line=dict(color=sample_colorscale("Viridis", frac)[0], width=2),
            name=tag,
            hovertemplate=(f"{tag}, " if swept else "") + "%{x}: $%{y:.2f}B<extra></extra>",
            showlegend=False,
        ))

    if swept:   # a colorbar only makes sense when there is a range to explain
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(
                colorscale="Viridis", cmin=lo, cmax=hi,
                colorbar=dict(title=param, thickness=12),
                showscale=True,
            ),
            hoverinfo="none", showlegend=False,
        ))

    fig.update_layout(
        title="Annual program cost" + (f" by {param}" if swept else ""),
        xaxis_title="year", yaxis_title="cost ($B, real 2025 USD)",
        **RETRO,
    )
    fig.update_xaxes(**AXIS)
    fig.update_yaxes(**AXIS)
    return fig.to_json()


COMPONENTS = [
    ("development_cost", "development", "#4e79a7"),
    ("capex",            "capex",       "#f28e2b"),
    ("opex",             "opex",        "#76b7b2"),
]

def describe_case(case) -> str:
    if len(sweep_params) != 1:
        return f"case {case}"
    param = sweep_params[0]
    v = results.loc[results["case"] == case, param].iloc[0]
    return f"{v:g}"

def component_chart() -> str:
    """Stacked cost components vs time for cheapest, median, and priciest cases."""
    totals = results.groupby("case")["total_cost"].sum().sort_values()
    picks = [
        ("cheapest", totals.index[0]),
        ("median",   totals.index[len(totals) // 2]),
        ("priciest", totals.index[-1]),
    ]
    # a single run (or tiny sweep) can pick the same case twice; keep one of each
    seen = set()
    picks = [(lbl, c) for lbl, c in picks if c not in seen and not seen.add(c)]

    if len(picks) == 1:
        titles = ["single case"]
    else:
        titles = [f"{label} ({describe_case(case)})" for label, case in picks]

    fig = make_subplots(rows=1, cols=len(picks), shared_yaxes=True, subplot_titles=titles)

    for col, (label, case) in enumerate(picks, start=1):
        sub = results[results["case"] == case].sort_values("year")
        for column, name, color in COMPONENTS:
            fig.add_trace(
                go.Scatter(
                    x=sub["year"], y=sub[column] / 1e9,
                    mode="lines",
                    line=dict(width=0.5, color=color),
                    stackgroup=f"panel{col}",     # stacking is per panel
                    name=name,
                    legendgroup=name,             # one legend entry toggles all panels
                    showlegend=(col == 1),
                    hovertemplate=f"{name}, %{{x}}: $%{{y:.2f}}B<extra></extra>",
                ),
                row=1, col=col,
            )

    fig.update_layout(title="Cost components over time", **RETRO)
    fig.update_xaxes(title_text="year", **AXIS)
    fig.update_yaxes(**AXIS)
    fig.update_yaxes(title_text="cost ($B, real 2025 USD)", col=1)
    return fig.to_json()


def scenario_form_init() -> str:
    """Current scenario values and dropdown options, as JSON for the form."""
    root = Path("/ciim_inputs")
    scenario = yaml.safe_load((root / "scenario" / "scenario.yaml").read_text(encoding="utf-8"))
    methods = sorted(p.stem for p in (root / "deployment_methods").glob("*.yaml"))
    materials = sorted(yaml.safe_load((root / "material.yaml").read_text(encoding="utf-8")))
    return json.dumps({"scenario": scenario, "methods": methods, "materials": materials})


def write_scenario(scenario_json: str) -> None:
    """Replace scenario.yaml in the working inputs dir with the form's values."""
    data = json.loads(scenario_json)
    if not data.get("sweep"):
        data.pop("sweep", None)          # no sweep block rather than an empty one
    path = Path("/ciim_inputs") / "scenario" / "scenario.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")