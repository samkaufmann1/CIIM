"""Python half of the browser GUI. Runs under Pyodide, not on a filesystem.

docs/index.html fetches this file, executes it once to define these functions,
then calls them by name from JavaScript: run_model() and results_csv() for
running and exporting, cost_chart() and component_chart() for figures (returned
as Plotly JSON for the page to render), and the form/config helpers to read and
write input files.

/ciim_inputs is a writable copy of the packaged inputs in Pyodide's virtual
filesystem. The GUI edits files there and the model reads them back through the
normal loader, so GUI input is validated exactly like hand-edited YAML — which
is why nothing here validates anything itself.

Module-level `results`, `sweep_params`, `inputs`, and `currency_year` persist between the
separate JavaScript calls that make up one run-then-render-then-export cycle.
"""

from CIIM_SAI.load_inputs import load_inputs, pop_sweepable, INPUTS_DIR
from CIIM_SAI.run import run
from CIIM_SAI.climatology import determine_cooling
import plotly.graph_objects as go
from plotly.colors import sample_colorscale
from plotly.subplots import make_subplots
from pathlib import Path
import shutil
import json
import yaml

results = None
inputs = None
sweep_params: list[str] = []
currency_year = None

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
    global results, inputs, sweep_params, currency_year
    inputs = load_inputs(Path(inputs_dir)) if inputs_dir else load_inputs()
    scenario = inputs.scenario
    results = run(inputs)
    sweep_params = list(scenario.sweep)
    currency_year = inputs.currency_year

    lines = [f"method   : {scenario.deployment_method}"]
    if scenario.deployment_pattern is not None:
        lines.append(f"pattern  : {scenario.deployment_pattern} ({scenario.deployed_material})")
    else:
        lines.append(f"target   : {scenario.temperature_pattern} ({scenario.deployed_material})")
        lines.append(f"latitude : {scenario.latitude} degrees, north and south")
    lines += [
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
        if inputs.temperature is not None:
            totals["per_degree_year"] = (
                totals["total_cost"] / determine_cooling(inputs.temperature).sum()
            )
        lines.append(f"\n{len(totals)} cases:\n")
        lines.append(totals.to_string(float_format=lambda v: f"{v:,.0f}"))
    return "\n".join(lines)


def results_csv() -> str:
    return results.to_csv(index=False)

def cost_per_degree_year() -> str:
    """Program cost divided by the cooling it buys, for a single climate run.

    Empty for a sweep: degree-years are fixed by the temperature pattern, which
    is not sweepable, so this is only total cost rescaled and belongs in the
    totals table as a column rather than as one headline. Empty in override mode
    too, where masses are named directly and there is no target to divide by.
    """
    if inputs is None or inputs.temperature is None or results["case"].nunique() != 1:
        return ""
    degree_years = determine_cooling(inputs.temperature).sum()
    return f"${results['total_cost'].sum() / degree_years / 1e9:,.2f}B per degree-year"


def derived_pattern_csv() -> str:
    """The pattern climatology derived for the base scenario, as CSV text.

    In Tg/year, the unit a deployment pattern file uses; Inputs.pattern is kg.
    The base scenario only: under a sweep every case derives its own pattern,
    and the form describes the base.
    """
    if inputs is None or inputs.temperature is None:
        return ""
    return (inputs.pattern / 1.0e9).to_csv()


# "retro" font and color scheme. I like monospace font.
RETRO = dict(
    font=dict(family='ui-monospace, Consolas, "Courier New", monospace', size=12, color="#333"),
    paper_bgcolor="#fafaf8",
    plot_bgcolor="#fafaf8",
    margin=dict(l=60, r=20, t=50, b=45),
)

AXIS = dict(gridcolor="#e8e8e4", zeroline=False)


def cost_chart() -> str:
    """All cases: total cost vs year, colored by the swept parameter.

    Returns "" when more than one parameter is swept: the lines would still be
    correct, but there is nothing meaningful to color them by, and a colorbar
    of case numbers explains nothing.
    """
    if len(sweep_params) > 1:
        return ""

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
        xaxis_title="year", yaxis_title=f"cost ($B, real {currency_year} USD)",
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
    fig.update_yaxes(title_text=f"cost ($B, real {currency_year} USD)", col=1)
    return fig.to_json()


def scenario_form_init() -> str:
    """Current scenario values, dropdown options, and sweepable parameters."""
    root = Path("/ciim_inputs")
    scenario = yaml.safe_load((root / "scenario" / "scenario.yaml").read_text(encoding="utf-8"))
    material = yaml.safe_load((root / "material.yaml").read_text(encoding="utf-8"))
    climate = yaml.safe_load((root / "climate.yaml").read_text(encoding="utf-8"))
    method = yaml.safe_load(
        (root / "deployment_methods" / f"{scenario['deployment_method']}.yaml")
        .read_text(encoding="utf-8"))

    sweepable = (pop_sweepable("scenario", scenario)
                 + pop_sweepable("material", material)
                 + pop_sweepable("method", method))

        # A scenario-namespace path is only worth offering if this scenario actually
    # has that field set. In override mode latitude is absent, so sweeping it
    # would fail -- correctly, but only after the user had filled in a start,
    # stop and step. Phrased as "the field is set" rather than naming latitude,
    # a future climate-only field is handled without anyone remembering to.
    sweepable = [p for p in sweepable
                 if not p.startswith("scenario.")
                 or scenario.get(p.split(".", 1)[1]) is not None]

    return json.dumps({
        "scenario": scenario,
        "methods": sorted(p.stem for p in (root / "deployment_methods").glob("*.yaml")),
        "materials": sorted(material),
        "sweepable": sweepable,
        # The form seeds these when the mode radio flips to a mode whose file
        # and latitude are not yet set.
        "latitudes": sorted(climate["cooling_per_forcing"]),
        "patterns": sorted(p.name for p in (root / "scenario" / "deployment_patterns").glob("*.csv")),
        "temperature_patterns": sorted(
            p.name for p in (root / "scenario" / "temperature_patterns").glob("*.csv")),
    })

def write_scenario(scenario_json: str) -> None:
    """Replace scenario.yaml in the working inputs dir with the form's values."""
    path = Path("/ciim_inputs") / "scenario" / "scenario.yaml"
    data = json.loads(scenario_json)
    existing = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "sweepable" in existing:
        data["sweepable"] = existing["sweepable"]     # a declaration, not a form field
    if not data.get("sweep"):
        data.pop("sweep", None)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def pattern_csv(filename: str) -> str:
    """The text of one deployment pattern CSV in the working inputs dir."""
    return (Path("/ciim_inputs") / "scenario" / "deployment_patterns" / filename).read_text(encoding="utf-8")

def temperature_csv(filename: str) -> str:
    """The text of one temperature pattern CSV in the working inputs dir."""
    return (Path("/ciim_inputs") / "scenario" / "temperature_patterns" / filename).read_text(encoding="utf-8")

def flatten(data: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Depth-first (dot.path, value) pairs for every leaf of a nested dict. Used to pull input assumption fields out of the input files."""
    pairs = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            pairs.extend(flatten(value, path))
        else:
            pairs.append((path, value))
    return pairs

    # Each file is nested, but the form is a flat list of boxes. flatten() names
    # every setting by the chain of keys that reaches it — for instance
    # unit.labor.teleportationist.salary. The page sends that name back with the
    # edited value, and write_overrides uses it to find the setting again.
def inputs_form_init() -> str:
    """Field descriptions for the non-scenario input files, as JSON for the form."""
    root = Path("/ciim_inputs")
    scenario = yaml.safe_load((root / "scenario" / "scenario.yaml").read_text(encoding="utf-8"))
    files = [
        (f"deployment_methods/{scenario['deployment_method']}.yaml",
         f"deployment method: {scenario['deployment_method']}"),
        ("material.yaml", "materials"),
        ("finance.yaml", "finance"),
    ]
    spec = []
    for rel, title in files:
        data = yaml.safe_load((root / rel).read_text(encoding="utf-8"))
        data.pop("sweepable", None)
        fields = [
            {"path": path, "value": value,
             "kind": "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else "text"}
            for path, value in flatten(data)
            # `source` records where a figure came from. It belongs in a paper
            # or in an exported config, not in a box someone can type over.
            # So exclude it here.
            if path.split(".")[-1] != "source"
        ]
        spec.append({"file": rel, "title": title, "fields": fields})
    return json.dumps({"files": spec})


def write_overrides(overrides_json: str) -> None:
    """Apply {file: {dot.path: value}} onto the working input files and rewrite them."""
    overrides = json.loads(overrides_json)
    root = Path("/ciim_inputs")
    for rel, fields in overrides.items():
        path = root / rel
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for dotted, value in fields.items():
            node = data
            *parents, leaf = dotted.split(".")
            for key in parents:
                node = node[key]
            node[leaf] = value
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def export_config() -> str:
    """The full working input set as one YAML document.

    Only the files this scenario uses: an override scenario carries its
    deployment pattern, a climate one carries its temperature pattern, the
    climate parameters, and the residence-time table they name. import_config
    restores the packaged defaults before applying a config, so whichever files
    a config leaves out come back as the packaged examples rather than as gaps.
    """
    root = Path("/ciim_inputs")
    scenario = yaml.safe_load((root / "scenario" / "scenario.yaml").read_text(encoding="utf-8"))
    method_name = scenario["deployment_method"]

    def named_file(directory: str, name: str) -> dict:
        return {"name": name, "csv": (root / directory / name).read_text(encoding="utf-8")}

    config = {
        "scenario": scenario,
        "deployment_method": {
            "name": method_name,
            "values": yaml.safe_load(
                (root / "deployment_methods" / f"{method_name}.yaml").read_text(encoding="utf-8")
            ),
        },
        "material": yaml.safe_load((root / "material.yaml").read_text(encoding="utf-8")),
        "finance": yaml.safe_load((root / "finance.yaml").read_text(encoding="utf-8")),
    }

    if scenario.get("deployment_pattern") is not None:
        config["deployment_pattern"] = named_file(
            "scenario/deployment_patterns", scenario["deployment_pattern"])
    else:
        climate = yaml.safe_load((root / "climate.yaml").read_text(encoding="utf-8"))
        config["temperature_pattern"] = named_file(
            "scenario/temperature_patterns", scenario["temperature_pattern"])
        config["climate"] = climate
        config["stratospheric_lifetimes"] = named_file(
            "stratospheric_lifetimes", climate["stratospheric_lifetimes"])

    return yaml.safe_dump(config, sort_keys=False)


def import_config(text: str) -> None:
    """Replace the working inputs with a config document.

    The packaged tree is restored first, so a config that omits the other
    mode's files leaves the packaged examples in place rather than holes --
    switching mode after an import then still gives a working default.
    """
    config = yaml.safe_load(text)
    root = Path("/ciim_inputs")
    materialize_inputs(str(root))          # clean, complete tree to overwrite

    method = config["deployment_method"]
    (root / "deployment_methods" / f"{method['name']}.yaml").write_text(
        yaml.safe_dump(method["values"], sort_keys=False), encoding="utf-8")
    (root / "material.yaml").write_text(
        yaml.safe_dump(config["material"], sort_keys=False), encoding="utf-8")
    (root / "finance.yaml").write_text(
        yaml.safe_dump(config["finance"], sort_keys=False), encoding="utf-8")
    if "climate" in config:
        (root / "climate.yaml").write_text(
            yaml.safe_dump(config["climate"], sort_keys=False), encoding="utf-8")

    for key, directory in [("deployment_pattern", "scenario/deployment_patterns"),
                           ("temperature_pattern", "scenario/temperature_patterns"),
                           ("stratospheric_lifetimes", "stratospheric_lifetimes")]:
        if key in config:
            (root / directory / config[key]["name"]).write_text(
                config[key]["csv"], encoding="utf-8")

    (root / "scenario" / "scenario.yaml").write_text(
        yaml.safe_dump(config["scenario"], sort_keys=False), encoding="utf-8")


def write_pattern(csv_text: str, filename: str) -> str:
    """Save an uploaded pattern CSV into the working inputs dir; return its filename."""
    name = Path(filename).name or "uploaded_pattern.csv"
    (Path("/ciim_inputs") / "scenario" / "deployment_patterns" / name).write_text(
        csv_text, encoding="utf-8")
    return name

def write_temperature_pattern(csv_text: str, filename: str) -> str:
    """Save an uploaded temperature pattern CSV into the working inputs dir."""
    name = Path(filename).name or "uploaded_temperature_pattern.csv"
    (Path("/ciim_inputs") / "scenario" / "temperature_patterns" / name).write_text(
        csv_text, encoding="utf-8")
    return name