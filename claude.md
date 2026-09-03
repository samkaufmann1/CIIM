# Working on CIIM

Read `README.md` first — it describes what the model does, its input files, and
how the browser version works. This file covers how to work in the repo.

## Working style

Keep the code tight and interpretable: this is a research model that has to stay
readable and auditable by one person, not a production system.

## Conventions

- **Functions are verbs, variables are nouns** (`build_fleet` the function,
  `fleet` the value). Not style — same-name collisions have bitten this codebase.
- **SI base units everywhere**: metres, kilograms, USD. The single exception is
  time, which is in years. Deployment pattern CSVs are in Tg/year purely as a
  file-format convention and are converted to kg at load.
- **YAML exponents need a signed exponent**: `1.0e+9` parses as a float, `1e9`
  parses as a string.
- Validation lives in the schemas, not in the calculations. If a value could be
  wrong, add a pydantic constraint or a `model_validator` rather than a check at
  the point of use.

## Architecture

The load-bearing property is **one model, several front ends**. `load_inputs()`
produces a validated immutable `Inputs`; `run()` consumes it and returns one
DataFrame. The command line, notebooks, and the web page are all just callers.
Nothing may reimplement model logic — most relevantly, the web page must never
compute in JavaScript what Python can compute.

- `load_inputs.py` is the only module that reads files. Its pydantic schemas
  define every input file's shape except the deployment-method files, which are
  passed through as raw dicts and validated by the method module that owns them.
- `run.py` expands the sweep into one `Inputs` per case and dispatches by
  importing `deploy_<method>` dynamically — adding a method requires no edit here.
- A deployment method module's entire contract is `deployment_schedule(inputs)
  -> DataFrame`, indexed by year. Everything else in such a module is private to it.
- `docs/app.py` is called function-by-name from JavaScript in `docs/index.html`.
  It runs under Pyodide, where `/ciim_inputs` is a writable copy of the packaged
  inputs in a virtual filesystem.