# CIIM — Climate Intervention Infrastructure Model

A Python model of the infrastructure behind a stratospheric aerosol injection
(SAI) deployment program: what would have to be developed, built, staffed, and
supplied, year by year, to deliver a given deployment pattern — and what it
would cost.

**Status: early development.** The only deployment method implemented so far is
a fictional one (a fleet of teleporters consuming unobtanium),
used to exercise the model's machinery while it is built.

Generative AI tools were used to help write the code and documentation in this repo.
I take full responsibility as the author for everything produced using these tools.

**[Live demo](https://samkaufmann1.github.io/CIIM/)** — runs the actual model
in your browser via WebAssembly.

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
operating costs (labor, consumables, feedstock) for the active fleet. A
scenario may sweep any scenario parameter over a range; results are stacked into a single table, one row per
(case, year).

Conventions: SI base units throughout (altitude in meters, mass in kg), with
one exception — time is in years. All costs are real 2025 US
dollars. Deployment pattern CSVs are in Tg/year and converted to kg on load.

## Running it

Requires Python ≥ 3.11. From the repository root:

```
pip install -e .
python -m CIIM_SAI
```

This runs the packaged scenario, prints a summary, and writes the full table
to `outputs/deployment_schedule.csv`. From a notebook or your own code, skip
the command line and call the model directly:

```python
from CIIM_SAI.run import run

results = run()   # one DataFrame, all cases
```

To change what is modeled, edit the YAML files under `src/CIIM_SAI/inputs/`.

## Repository layout

```
src/CIIM_SAI/          the model
  load_inputs.py       reads and validates all input files
  run.py               expands sweeps, runs cases, stacks results
  deployment_methods/  one deploy_<name>.py per method: its input schema
                       and its cost/schedule calculation
  inputs/              packaged default inputs (YAML + CSV)
docs/                  the browser demo served by GitHub Pages: a static
                       page plus the packaged model as a Python wheel
```

Adding a deployment method means adding two files: an input file
`inputs/deployment_methods/<name>.yaml` and a module
`deployment_methods/deploy_<name>.py` exposing `deployment_schedule(inputs)`.
The method named in `scenario.yaml` is dispatched to dynamically; no registry
to update.

## License

See [LICENSE](LICENSE).
