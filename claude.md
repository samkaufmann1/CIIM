# Working on CIIM

Read `README.md` first — it describes what the model does, its input files, and
how the browser version works. This file covers how to work in the repo.

## Working style

Keep the code tight and interpretable: this is a research model that has to stay
readable and auditable by one person, not a production system.

## Conventions

- **Functions are verbs, variables are nouns** (`schedule_assets` the function,
  `assets` the value). Not style — same-name collisions have bitten this codebase.
- **SI base units everywhere**: meters, kilograms, USD. The single exception is
  time, which is in years. Deployment pattern CSVs are in Tg/year purely as a
  file-format convention and are converted to kg at load.
- **YAML exponents need a signed exponent**: `1.0e+9` parses as a float, `1e9`
  parses as a string.
- Validation lives in the schemas, not in the calculations. If a value could be
wrong, add a pydantic constraint or a `model_validator` rather than a check at
the point of use. The one exception is the sweep block's targets, checked in
`load_inputs`: a `Scenario` validator can only see `Scenario`, and whether a
path is sweepable depends on every input file.

## Architecture

The load-bearing property is **one model, several front ends**. `load_inputs()`
produces a validated immutable `Inputs`; `run()` consumes it and returns one
DataFrame. The command line, notebooks, and the web page are all just callers.
Nothing may reimplement model logic — most relevantly, the web page must never
compute in JavaScript what Python can compute.

- `load_inputs.py`'s pydantic schemas
  define every input file's shape except the deployment-method files, which are
  passed through as raw dicts and validated by the method module that owns them.
- `climatology.py` turns a temperature target into a deployment pattern when a
  scenario names one instead of naming masses directly. It reads no files and
  imports nothing from `load_inputs`: everything arrives as an argument, which
  makes its entry point's signature the list of everything the derivation
  depends on. It returns Tg/year, so `check_pattern` validates and converts it
  exactly as it would a hand-written CSV — nothing downstream, `run.py` or a
  method module, can tell which mode produced the pattern.
- Each input file declares which of its own variables may be swept, in a
  top-level `sweepable:` list of paths relative to that file. `load_inputs`
  strips those declarations before any schema sees the data — every schema
  forbids unknown keys — and namespaces them by file, so a sweep block names
  `scenario.altitude` or `method.unit.cost`. Declaring per file is what keeps
  a new deployment method from needing an edit anywhere central, and what
  lets method modules stay ignorant that sweeping exists.  
- `deployment_methods/scheduling.py` holds the timing arithmetic every method
  needs: how many assets a demand calls for, when they enter service and retire,
  and how development and capital costs fall across the years. It knows nothing
  about what the assets are, and takes plain numbers rather than any method's
  schema. A helper used only by method modules lives inside
  `deployment_methods/`; one used more widely, like `climatology.py`, stays at
  the top level.
- `run.py` expands the sweep into one `Inputs` per case and dispatches by
  importing `deploy_<method>` dynamically — adding a method requires no edit here.
- A deployment method module's entire contract is `deployment_schedule(inputs)
  -> DataFrame`, indexed by year. Everything else in such a module is private to
  it, including the schemas it owns: the root class validating `<name>.yaml` is
  named `<Name>Method`, and the class validating the scenario's `method_options`
  is `<Name>Options`. Checks that span two input files — a design the scenario
  names against the designs the method file defines — live in the method module
  as small `find_*` functions, since neither schema can see the other's file.
  Every method must produce `demand`, `capacity`, `utilization`,
  `development_cost`, `capex`, `opex` and `total_cost`; those are what the front
  ends and the charts may assume. Every other column is the method's own, so
  anything comparing methods works from that set alone.
- Every method's schedule must carry `demand`, `capacity`, `utilization`,
  `development_cost`, `capex`, `opex` and `total_cost`, because the front ends
  compare across methods on those. Everything else is the method's own: the
  teleporter's `active` and `ordered`, the balloon's `launchpads_active` and
  `drones_flying`. Anything a front end aggregates must come from the guaranteed
  set.
- `docs/app.py` is called function-by-name from JavaScript in `docs/index.html`.
  It runs under Pyodide, where `/ciim_inputs` is a writable copy of the packaged
  inputs in a virtual filesystem.