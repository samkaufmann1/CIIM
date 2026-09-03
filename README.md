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
| `scenario/scenario.yaml` | Which deployment method, material, and deployment pattern to run; injection altitude; optional parameter sweeps |
| `scenario/deployment_patterns/*.csv` | Deployed mass (Tg/year) by year and latitude |
| `deployment_methods/*.yaml` | One file per deployment method: development (NRE), unit cost, lifetime, capacity, lead time, labor, consumables |
| `material.yaml` | Materials: cost per kg, molar mass, sources |
| `finance.yaml` | Currency conventions |

From the deployment pattern, the model determines how many units must be in
service each year, then schedules orders, deliveries, and retirements around
unit lifetime and lead time, and prices the result: development spread over the
years before the first order, capital spread across each unit's lead time, and
operating costs (labor, consumables, and the deployed material itself) for the
active fleet. A scenario may sweep any numeric scenario parameter over a range; results
are stacked into a single table, one row per (case, year).

Conventions: SI base units throughout (altitude in metres, mass in kg), with one
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
- **Latitude is carried but unused.** Deployment patterns are resolved by
  latitude, but the model sums across latitudes and costs the total; nothing
  yet depends on where material goes.
- **Single-parameter sweeps in practice.** The sweep machinery is ultimately intended to take a Cartesian
  product of any number of parameters, but currently only scenario-level parameters are
  sweepable, and the GUI and charts assume one at a time.

## Running it

Requires Python ≥ 3.11. From the repository root:

```
pip install -e .
python -m CIIM_SAI
```

This runs the packaged scenario, prints a summary, and writes the full table to
`outputs/deployment_schedule.csv`. From a notebook or your own code, skip the
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