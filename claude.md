# Working on CIIM

Read `README.md` first — it describes what the model does, its input files, and
how the browser version works. This file covers how to work in the repo.

## Working style

Keep the code tight and interpretable: this is a research model that has to stay
readable and auditable by one person, not a production system.

## Conventions

- **Functions are verbs, variables are nouns** (`schedule_assets` the function,
  `assets` the value). Not style — same-name collisions have bitten this codebase.
- **SI base units everywhere**: meters, kilograms, USD — unless the variable
  name says otherwise, which is how hours, seconds and months appear
  (`launch_time_hours`, `ground_cycle_seconds`). Unsuffixed time is in years.
  Deployment pattern CSVs are in Tg/year purely as a file-format convention and
  are converted to kg at load.
- **YAML exponents need a signed exponent**: `1.0e+9` parses as a float, `1e9`
  parses as a string.
- Validate individual input values and relationships within one file in
  pydantic schemas. Checks spanning files belong in method-level lookup
  helpers or the loader. Checks involving derived mission quantities belong
  where those quantities are calculated. The browser may perform Python
  preflight checks for clearer feedback, but these do not replace the
  model's validation. Sweep targets are checked in `load_inputs` because
  their validity depends on declarations across the input files.


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
  top-level `sweepable:` list of paths relative to that file. Some sweepable
  paths may include *, indicating an arbitrary intermediate path. `load_inputs`
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
- Aircraft cohort scheduling lives in `deploy_aircraft.py`, because retirement
  depends on both calendar age and accumulated flight cycles. Deliveries and
  retirements occur at annual boundaries. Base construction proceeds
  independently of aircraft development. Mission profiles are discrete;
  neither the model nor the frontend should interpolate missing altitudes.
  The packaged aircraft designs contain placeholder data.
- `run.py` expands the sweep into one `Inputs` per case and dispatches by
  importing `deploy_<method>` dynamically — adding a method requires no edit here.
- A deployment method exposes one required function:
  `deployment_schedule(inputs) -> DataFrame`, indexed by year.
  Optional frontend hooks are:
  `option_fields(method_yaml) -> {option: metadata}`,
  `deployable_materials(material_yaml) -> [name]`, and
  `altitudes_by_design(method_yaml) -> {design: [altitudes]}`.
  These take raw file dictionaries because forms are prepared before a
  complete `Inputs` object exists. Missing hooks mean no option controls,
  unrestricted material choices, or no discrete altitude guidance,
  respectively. Keep method-specific knowledge inside the method module.
  Its input schemas remain `<Name>Method` and `<Name>Options`; cross-file
  lookups remain small method-level helpers such as `find_design()`.
- Every method's schedule must carry `demand`, `capacity`, `utilization`,
  `development_cost`, `capex`, `opex` and `total_cost`, because the front ends
  compare across methods on those. Everything else is the method's own: the
  teleporter's `active` and `ordered`, the balloon's `launchpads_active` and
  `drones_flying`. Anything a front end aggregates must come from the guaranteed
  set.
- `docs/app.py` runs under Pyodide, using `/ciim_inputs` as a writable copy of
  the packaged inputs. `docs/index.html` calls its functions through
  `callPython()`; `runPython(appCode)` is reserved for loading the definitions.
  Keep model calculations and altitude preflight validation in Python.

- Form changes must preserve edited assumptions and per-method options.
  Prepare replacement form metadata before replacing controls, and restore
  saved inputs if preparation fails. Preserve selected CSV filenames across
  mode changes; Reset and Import intentionally discard remembered selections.

- Capture model warnings during loading and execution and display distinct
  messages alongside successful results. Clear warnings, results, charts,
  and download availability together before another run, reset, or import,
  and after a failed run.

- Changes under `src/`, including packaged inputs and frontend metadata hooks,
  require rebuilding the wheel used by `docs/index.html`. Changes confined to
  `docs/app.py` or `docs/index.html` do not. Local Python and simulated DOM
  checks do not replace a browser test against the rebuilt wheel.