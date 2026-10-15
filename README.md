# CIIM — Climate Intervention Infrastructure Model

A Python model of the infrastructure behind a stratospheric aerosol injection
(SAI) deployment program: what would have to be developed, built, staffed, and
supplied, year by year, to deliver a given deployment pattern — and what it
would cost.

**Status: early development.** The model implements balloon deployment and
crewed or uncrewed aircraft deployment. Balloons carry a payload gas to the
injection altitude and release it when they burst. Aircraft climb to the
injection altitude and disperse their payload during cruise. A fictional
teleporter is also included as a simple test case for the model.

The included aircraft and balloon designs use placeholder costs and performance data,
not validated designs. Their results illustrate model behavior;
the inputs must be replaced before those results can support deployment
cost estimates.

Generative AI tools were used to help write the code and documentation in this
repo. I take full responsibility as the author for everything produced using
these tools.

**[Live demo](https://samkaufmann1.github.io/CIIM/)** — runs the model in
your browser via WebAssembly.

## How it works

All inputs are files packaged under `src/CIIM_SAI/inputs/`:

| File | Contents |
|---|---|
| `scenario/scenario.yaml` | Which deployment method and material to run; injection altitude; either a deployment pattern or a temperature target with an injection latitude; `method_options` for choices that mean something only to the chosen method, such as the selected design or an aircraft payload emission rate override; optional parameter sweeps |
| `scenario/deployment_patterns/*.csv` | Deployed mass (Tg/year) by year and latitude |
| `scenario/temperature_patterns/*.csv` | Warming expected without SAI, and warming wanted (°C) by year |
| `deployment_methods/*.yaml` | One file per deployment method, in whatever shape that method's module defines: development, the assets it buys, what it consumes, and who operates it |
| `material.yaml` | Materials: cost per kg, and for a deployed material its radiative forcing per Tg per year |
| `climate.yaml` | Temperature response to forcing by injection latitude; which residence-time table to use |
| `stratospheric_lifetimes/*.csv` | Aerosol residence time (months) by injection altitude and latitude |
| `finance.yaml` | Currency conventions |

All files indicate which variables, if any, can be used as part of a parameter sweep.

A scenario says how much is deployed in one of two ways. It can name a
deployment pattern directly — masses by year and latitude. Or it can name a
temperature target: the warming expected without SAI and the warming wanted,
from which `climatology.py` derives the mass needed. It does that by converting
the cooling required into a radiative forcing using a sensitivity that depends
on injection latitude, then into a mass using the deployed material's forcing
per Tg per year, then adjusting for how long aerosol survives at that altitude
and latitude. The result is a deployment pattern in the same format as a
hand-written one, and the rest of the model cannot tell the difference.

From the deployment pattern, each method calculates what it must own and
operate each year: aircraft and basing capacity, balloon launch facilities
and recovery drones, or fictional teleporters. It schedules purchases,
deliveries, and retirements using the relevant lifetimes and lead times,
then calculates development, capital, and operating costs.

The aircraft method derives payload, fuel consumption, and flight duration
from the selected design's mission profile and payload emission rate.
Flights are rounded up and use full mission payloads. Flight requirements
determine fleet size, basing capacity, fuel consumption, and labor.
Aircraft delivery cohorts track both calendar age and accumulated flight
cycles. Base construction can overlap aircraft development and procurement.

A scenario may sweep parameters declared sweepable in the input files.
Results are stacked into one table, with one row per (case, year).

Conventions: SI base units (altitude in meters, mass in kg, cost in USD) unless
the variable's own name says otherwise — `launch_time_hours`,
`ground_cycle_seconds`, `reference_lifetime_months`. Time is the one quantity
that varies: it is in years wherever the name does not say, which covers every
lifetime, lead time and duration. Costs are real dollars of the year declared in
`finance.yaml`; the model performs no deflation, so that field states what the
input figures are assumed to be, it does not convert them. Deployment pattern
CSVs are in Tg/year and converted to kg on load.

### What the model does not do

Worth stating plainly, since several of these look like omissions rather than
choices:

- **No discounting.** All figures are undiscounted annual flows.
- **No price-level conversion.** See `finance.yaml` above.
- **No production limits.** Any number of units can be ordered in a year.
- **No balloon manufacturing.** The balloon model buys balloons at a unit price and says
  nothing about who makes them. Deployment at the scale modeled here would
  consume balloons at a multiple of current world production, and the assumed
  price already presumes a manufacturing base that does not exist. Since the
  balloons are the largest single line in the cost, this is the largest gap in
  the model.
- **Homogeneous assets.** A method may offer several designs, but one run flies
  one of them: no mixed fleets, no learning curve, no mid-life refits.
- **No demand-driven retirement.** Falling demand does not retire surplus
  assets. Retained capacity continues to incur maintenance costs. Aircraft
  retire under either their calendar-life or cycle-life limit. Scheduling
  uses annual time steps: a cohort reaching its cycle limit during a year
  serves that year and retires at the next year's start.
- **Latitude enters coarsely.** In climate mode, injection latitude affects
  both the temperature response to forcing and aerosol residence time.
  Ground-launch methods also require sites at the latitudes they serve.
  Aircraft basing is pooled capacity rather than a set of geographically
  located airports; the aircraft model does not calculate routes or
  location-specific construction costs.
- **Discrete aircraft mission profiles.** Aircraft can operate only at the
  altitudes supplied for the selected design. The model does not interpolate
  between profiles.
- **The climate representation is deliberately crude.** Response is linear in
  injection rate, cooling is instantaneous with no ocean lag, and only one
  injection latitude is modeled at a time. `climatology.py`'s docstring states
  these in full; together they bound what a cost per degree of cooling can mean.

## Running it

Requires Python ≥ 3.11. From the repository root:

```
pip install -e .
python -m CIIM_SAI
```

This runs the packaged scenario, prints a summary, and writes the full table to
`outputs/deployment_schedule.csv`. If the scenario names a temperature target
rather than a deployment pattern, the pattern derived from it is written to
`outputs/derived_deployment_pattern.csv` as well — that file is itself a valid
deployment pattern, so pointing `deployment_pattern` at it reproduces the run
exactly. From a notebook or your own code, skip the
command line and call the model directly:

```python
from CIIM_SAI.run import run

results = run()   # one DataFrame, all cases
```

To change what is modeled, edit the YAML files under `src/CIIM_SAI/inputs/`.

## The browser version

`docs/` is a self-contained web app, served by GitHub Pages at the demo link
above, that runs the real model client-side — no server, and no reimplementation
of the model in JavaScript. It works by loading Pyodide (CPython compiled to
WebAssembly), installing this package from a built wheel, and calling the same
`load_inputs()` and `run()` the command line uses.

```
docs/index.html    the page: forms, charts, and the JavaScript that drives them
docs/app.py        Python called from the page: runs the model, builds the
                   charts as Plotly JSON, reads and writes the input files
docs/*.whl         this package, built — what the page actually runs
```

The division of labor is that Python computes and JavaScript displays. Form
values are written back out as YAML into a working copy of the inputs directory
in Pyodide's virtual filesystem, then read through the normal loader, so GUI
input is validated exactly like a hand-edited file.

The browser builds method-option controls from metadata supplied by each
deployment method. It preserves method selections and edited assumptions
when switching forms, displays supported aircraft altitudes, and checks
altitude sweeps before calculating deployment schedules. Model warnings
appear alongside successful results; identical warning messages from a
sweep are shown once. Failed runs clear previous results and disable their
downloads.

**A change to the model does not reach the site until the wheel is rebuilt.**
After editing anything under `src/`:

```
python -m build
copy dist\ciim_sai-0.1.0-py3-none-any.whl docs\      # cp on macOS/Linux
```

then commit the updated wheel along with the source. Changes to `docs/app.py`
or `docs/index.html` alone need no rebuild. To try the site locally, serve
`docs/` over HTTP (`python -m http.server` from inside it, then visit
`localhost:8000`) — opening the file directly will not work, because the page
fetches the wheel and `app.py` at runtime.

## Repository layout

```
src/CIIM_SAI/          the model
  load_inputs.py       reads and validates the model's input files
  run.py               expands sweeps, runs cases, stacks results
  climatology.py       derives a deployment pattern from a temperature target
  __main__.py          command-line entry point; demonstration only
  deployment_methods/  one deploy_<name>.py per method: its input schema
                       and its cost/schedule calculation, plus scheduling.py,
                       the asset timing arithmetic they share
  inputs/              packaged default inputs (YAML + CSV)
docs/                  the browser version (see above)
```

Adding a deployment method means adding two files: an input file
`inputs/deployment_methods/<name>.yaml` and a module
`deployment_methods/deploy_<name>.py` exposing `deployment_schedule(inputs)`.

A method may also expose optional frontend hooks:

- `option_fields(method_yaml)` describes scenario method-option controls,
  including choices, numeric inputs, labels, and optional help text.
- `deployable_materials(material_yaml)` lists materials the method can deploy.
- `altitudes_by_design(method_yaml)` maps design names to supported discrete
  injection altitudes.

These hooks take raw input dictionaries. Without a hook, the browser offers
no method-option controls, all materials, or no discrete altitude guidance,
respectively. The model's schemas and method-level checks remain responsible
for validation. Methods and their optional hooks are discovered by name;
there is no central registry.

## License

See [LICENSE](LICENSE).