# Working on CIIM

Read `README.md` first — it describes what the model does, its input files, and
how the browser version works. This file covers how to work in the repo.

## Working style

The author wants to review and approve each piece before it is implemented.
**Propose code, explain it, and wait** — do not implement multi-file changes
unprompted. Explanations of *why* a design is the way it is are wanted; walls of
generated code are not.

Do not write to the author's filesystem. Deliver drafts into the conversation
and let the author place them.

Keep the code tight and interpretable: this is a research model that has to stay
readable and auditable by one person, not a production system. Prefer a plain
function over a class, a named constant over a magic number, and deletion over
accumulation. If a comment would be needed to explain a clever line, write the
unclever line instead.

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

## Before finishing a change

- Model changed? Rebuild the wheel and copy it into `docs/`, or the site silently
  keeps running the old model (see README).
- Columns added to the schedule? Update the `deployment_schedule` docstring,
  which documents the full column set.
- Check `python -m CIIM_SAI` still runs, and that the page still loads when
  served locally from `docs/`.
- Offer a commit message when a step is complete; the author commits.
