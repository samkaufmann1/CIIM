# CIIM — Climate Intervention Infrastructure Model

A Python model of the infrastructure behind a stratospheric aerosol injection
(SAI) deployment program: what would have to be developed, built, staffed, and
supplied, year by year, to deliver a given deployment pattern — and what it
would cost.

**Status: early development.** The only deployment method implemented so far is
a fictional one (a fleet of teleporters consuming unobtanium), used to exercise
the model's machinery while it is built. No output of this model is a research
result.

Generative AI tools were used to help write the code and documentation in this
repo. I take full responsibility as the author for everything produced using
these tools.

**[Live demo](https://samkaufmann1.github.io/CIIM/)** — runs the actual model in
your browser via WebAssembly.

## How it works

All inputs are files packaged under `src/CIIM_SAI/inputs/`:

| File | Contents |
|---|---|
| `scenario/scenario.yaml` | Which deployment method and material to run; injection altitude; either a deployment pattern or a temperature target with an injection latitude; optional parameter sweeps |
| `scenario/deployment_patterns/*.csv` | Deployed mass (Tg/year) by year and latitude |
| `scenario/temperature_patterns/*.csv` | Warming expected without SAI, and warming wanted (°C) by year |
| `deployment_methods/*.yaml` | One file per deployment method: development (NRE), unit cost, lifetime, capacity, lead time, labor, consumables |
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

From the deployment pattern, the model determines how many units must be in
service each year, then schedules orders, deliveries, and retirements around
unit lifetime and lead time, and prices the result: development spread over the
years before the first order, capital spread across each unit's lead time, and
operating costs (labor, consumables, and the deployed material itself) for the
active fleet. A scenario may sweep any numeric parameter over a range; results
are stacked into a single table, one row per (case, year).

Conventions: SI base units throughout (altitude in meters, mass in kg), with one
exception — time is in years. Costs are real dollars of the year declared in
`finance.yaml`; the model performs no deflation, so that field states what the
input figures are assumed to be, it does not convert them. Deployment pattern
CSVs are in Tg/year and converted to kg on load.

### What the model does not do

Worth stating plainly, since several of these look like omissions rather than
choices:

- **No discounting.** All figures are undiscounted annual flows.
- **No price-level conversion.** See `finance.yaml` above.
- **No production limits.** Any number of units can be ordered in a year.
- **Homogeneous units.** One capacity, one cost, one lifetime per method; no
  variants, no learning curve, no mid-life refits.
- **No early retirement.** A unit serves exactly its lifetime, even if demand
  has fallen and it is idle.
- **Latitude affects the derivation, not the costing.** In climate mode the
  injection latitude sets both the temperature response to forcing and the
  aerosol's residence time, so it changes how much material is needed. Once the
  mass is known the model sums across latitudes and costs the total; nothing
  about building or running the fleet depends on where the material goes.
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
  load_inputs.py       reads and validates all input files; the only module
                       that touches the filesystem
  run.py               expands sweeps, runs cases, stacks results
  climatology.py       derives a deployment pattern from a temperature target
  __main__.py          command-line entry point; demonstration only
  deployment_methods/  one deploy_<name>.py per method: its input schema
                       and its cost/schedule calculation
  inputs/              packaged default inputs (YAML + CSV)
docs/                  the browser version (see above)
```

Adding a deployment method means adding two files: an input file
`inputs/deployment_methods/<name>.yaml` and a module
`deployment_methods/deploy_<name>.py` exposing `deployment_schedule(inputs)`.
The method named in `scenario.yaml` is dispatched to dynamically; there is no
registry to update.

## License

See [LICENSE](LICENSE).