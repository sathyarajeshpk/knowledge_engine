# Learning Guide — M0

This guide teaches the **concepts** used in M0. It assumes you can program —
variables, functions, loops, `if` statements — but that Python *architecture* is
new to you.

Read this first, then `docs/playbook/M0_FOUNDATION.md` for the codebase itself.

Every concept follows the same shape: **the problem → the mechanism → our code**.

---

## 1. Modules and packages

### The problem

A program in one file becomes unreadable. Split it across files and you need a
way for them to find each other.

### The mechanism

A **module** is one `.py` file. A **package** is a directory of modules
containing `__init__.py`.

```
engine/ke/
├── __init__.py     ← makes "ke" a package
├── models.py       ← module "ke.models"
├── pack.py         ← module "ke.pack"
└── validate.py     ← module "ke.validate"
```

Import with dots:

```python
from ke.models import FeatureId          # one name
from ke.pack import Pack, PackError      # several
import ke.validate                       # whole module
```

### The concept that actually matters: dependency direction

Modules must not import each other in a circle. Ours form a stack:

```
__main__.py     (top — knows everyone)
    ↓
validate.py     (knows pack + models)
    ↓
pack.py         (knows models)
    ↓
models.py       (knows nobody — pure definitions)
```

`models.py` imports nothing from `ke`. It cannot, because everything depends on
it. If two modules import each other, Python raises `ImportError: cannot import
name ... (most likely due to a circular import)`.

**Rule of thumb:** the more fundamental a module, the fewer imports it has.

### `__init__.py`

Ours holds the package docstring, `__version__`, and `SCHEMA_VERSION`. Anything
here is available as `from ke import SCHEMA_VERSION`. Keep it small — it runs on
every import of anything in the package.

### `__main__.py` — the special name

Naming a module `__main__.py` inside a package makes this work:

```bash
python -m ke validate
```

`-m ke` means "import the package `ke` and run its `__main__` module". This is
how `pip`, `pytest` and `venv` are all invoked.

---

## 2. `pyproject.toml`

### The problem

Your code needs a name, a Python version, a list of libraries it needs, and a way
to be installed. Without a standard file, every project invents its own.

### The mechanism

`pyproject.toml` is the modern standard (it replaced `setup.py`). TOML is a
config format — think INI with types. `[section]` headers, `key = value` pairs.

### Ours, explained

```toml
[project]
name = "knowledge-engine"
requires-python = ">=3.11"
dependencies = ["PyYAML>=6.0"]
```

`requires-python = ">=3.11"` matters: we use `StrEnum`, added in 3.11.

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0"]
```

`pytest` is needed to *develop* but not to *run*. `pip install -e ".[dev]"`
installs both; `pip install .` installs only PyYAML.

```toml
[project.scripts]
ke = "ke.__main__:main"
```

Creates a `ke` command that calls `main()` in `ke/__main__.py`. Now both
`python -m ke validate` and `ke validate` work.

```toml
[tool.setuptools]
package-dir = { "" = "engine" }
```

**This is the interesting one.** It says "packages live inside `engine/`, not at
the repository root". That is why `engine/ke/` imports as `ke`, not
`engine.ke` — and it is what lets `engine/` be lifted into its own repository
later without moving a single file.

```toml
[tool.pytest.ini_options]
testpaths = ["engine/tests"]
pythonpath = ["engine"]
```

Tools read their config from here too, so the project has one config file.

### Editable installs

```bash
python -m pip install -e ".[dev]"
```

`-e` = **editable**. Python links to your source directory instead of copying it.
Edit `models.py` and the change is live immediately. Without `-e`, every edit
needs a reinstall.

---

## 3. Type hints

```python
def relative(self, path: Path) -> str:
```

`path: Path` means "expects a `Path`". `-> str` means "returns a string".

**Python does not enforce these.** They are documentation that tools can read —
your editor autocompletes and warns, and a reader knows the shape without
tracing the code.

Ones you will see in our code:

```python
str | None                  # a string OR None
list[Finding]               # a list of Findings
dict[str, Any]              # string keys, any values
tuple[str, ...]             # a tuple of any number of strings
Iterator[Path]              # something you can loop over
```

At the top of most files:

```python
from __future__ import annotations
```

This lets a class reference itself in its own hints (`FeatureId.parse` returns
`FeatureId`, which does not exist yet while the class body is being read). Cheap,
harmless, and it makes hints evaluate lazily.

---

## 4. Dataclasses

### The problem

A plain class needs boilerplate to hold data:

```python
class Revision:
    def __init__(self, revision, date, summary):
        self.revision = revision
        self.date = date
        self.summary = summary
    def __repr__(self): ...
    def __eq__(self, other): ...
```

Every field named four times.

### The mechanism

```python
from dataclasses import dataclass

@dataclass
class Revision:
    revision: int
    date: date
    summary: str = ""
```

The decorator generates `__init__`, `__repr__` and `__eq__` from the annotations.
`summary: str = ""` gives a default — **fields with defaults must come after
fields without them.**

### `frozen=True` — immutability

```python
@dataclass(frozen=True)
class FeatureId:
    prefix: str
```

```python
fid = FeatureId.parse("MSF-2026-04-001")
fid.year = 2027        # FrozenInstanceError
```

We freeze `FeatureId` because a Feature ID is permanent by rule. Making it
unchangeable in code means the rule cannot be broken by accident.

`KnowledgeObject` is **not** frozen — it has ~30 fields and gets built up in
stages. Its protection comes from `with_engine_fields()` instead.

### `order=True` — sortability

```python
@dataclass(frozen=True, order=True)
class FeatureId:
    prefix: str
    year: int
    month: int
    sequence: int
```

Generates comparison methods that compare fields **in declaration order** —
prefix, then year, then month, then sequence. So `sorted(ids)` gives
chronological order for free. Field order is a design decision, not cosmetic.

### `field(default_factory=...)`

```python
@dataclass
class RunReport:
    sources: list[SourceHealth] = field(default_factory=list)
```

You **cannot** write `sources: list = []`. That list would be created once and
shared by every instance — a classic Python bug. `default_factory=list` calls
`list()` fresh for each instance.

### `replace()` — copy with changes

```python
from dataclasses import replace
new_obj = replace(old_obj, title="New title")
```

Returns a **new** object; the original is untouched. This is what
`with_engine_fields()` uses, so a rejected write cannot half-apply.

### `__post_init__`

Runs after the generated `__init__`, for validation:

```python
def __post_init__(self) -> None:
    if not FEATURE_ID_PATTERN.match(str(self)):
        raise ValueError(f"invalid Feature ID components: {self!r}")
```

An invalid `FeatureId` cannot exist — construction fails.

---

## 5. Enums

### The problem

```python
difficulty = "intermediate"
```

Nothing stops `"Intermediate"`, `"medium"`, or `"itnermediate"`. You find out
months later when grouping produces four categories that should be one.

### The mechanism

```python
from enum import StrEnum

class Difficulty(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
```

```python
Difficulty("beginner")      # → Difficulty.BEGINNER
Difficulty("banana")        # → ValueError
```

**Invalid values become impossible.** `validate.py` relies on this: it does not
check enums itself — it calls `KnowledgeObject.from_metadata_dict()` and catches
`ValueError`.

### Why `StrEnum` specifically

```python
Difficulty.BEGINNER == "beginner"   # True
```

A `StrEnum` member *is* a string. So it writes to YAML as a plain
`difficulty: beginner` — readable by a human in the GitHub UI, by `grep`, by any
tool. Validation in code, plain text on disk.

With plain `Enum`, YAML would contain something like
`!!python/object/apply:ke.models.Difficulty` — unreadable, and a security risk to
load.

### `IntEnum`

```python
class Tier(IntEnum):
    ACT_NOW = 1
    LEARN_SOON = 2
    AWARENESS = 3
```

Same idea for numbers. `Tier.ACT_NOW == 1` is `True`, and `tier: 1` in YAML.

---

## 6. Sets and `frozenset`

### The mechanism

A set is an unordered collection of unique items with fast membership tests and
algebraic operators:

```python
a = {"x", "y"}
b = {"y", "z"}
a & b      # {"y"}          intersection — in both
a | b      # {"x","y","z"}  union — in either
a - b      # {"x"}          difference — in a but not b
"x" in a   # True           membership (fast)
```

`frozenset` is an immutable set. We use it so ownership classes cannot be
modified at runtime.

### How we use it

```python
ENGINE_OWNED_FIELDS = frozenset({"title", "source_url", ...})
USER_OWNED_FIELDS   = frozenset({"learning_status", "notes", ...})

assert not (ENGINE_OWNED_FIELDS & USER_OWNED_FIELDS)
```

"The intersection must be empty" = "no field is in both classes". One line
expresses a whole safety rule.

And in `validate.py`:

```python
for name in sorted(ALL_METADATA_FIELDS - set(metadata)):
    # declared but missing from the file → error

for name in sorted(set(metadata) - ALL_METADATA_FIELDS):
    # in the file but unknown → probably a typo → warning
```

Two set subtractions replace what would otherwise be nested loops.

---

## 7. Assertions at import time

```python
assert not (ENGINE_OWNED_FIELDS & ENGINE_PROPOSED_FIELDS)
```

This is not inside a function. It runs **when the module is imported**.

If someone adds a field to two ownership classes, `import ke.models` raises
`AssertionError` — every test fails instantly, CI goes red, and nobody can
install the package. The bug is caught at the earliest possible moment instead of
at 3am on a Sunday when the cron overwrites your notes.

> Use import-time assertions only for invariants about the *code itself*, never
> for user input. `python -O` strips assertions, and validating user data is
> `validate.py`'s job.

---

## 8. YAML

### The problem

You need a config format a human can comfortably read and edit. JSON has no
comments and is fussy about commas.

### The mechanism

Indentation-based, comments with `#`:

```yaml
name: microsoft-fabric        # a string
schema_version: 1             # a number
tier: 1

tags:                         # a list
  - direct-lake
  - semantic-model

tags: [direct-lake, semantic-model]    # same list, inline

limits:                       # a nested mapping
  max_summary_words: 120

notes: null                   # null → Python None
needs_review: false           # boolean
published_date: 2026-04-15    # YAML parses this as a real date object
```

### In Python

```python
import yaml

data = yaml.safe_load(text)                      # YAML → dict
text = yaml.safe_dump(data, sort_keys=False)     # dict → YAML
```

**Always `safe_load`, never `load`.** Plain `load` can construct arbitrary Python
objects, which means a malicious file can execute code. `safe_load` handles only
plain data types.

`sort_keys=False` preserves your ordering — we want identity fields first, not
alphabetical.

### The gotcha we handle

YAML parses `2026-04-15` into a `datetime.date`, but `"2026-04-15"` (quoted) into
a string. Someone hand-editing a file may add quotes. So:

```python
def _coerce_date(value):
    if isinstance(value, datetime):  return value.date()
    if isinstance(value, date):      return value
    if isinstance(value, str):       return date.fromisoformat(value.strip())
    raise ValueError(f"cannot read {value!r} as a date")
```

Accept both rather than depending on how someone typed it.

---

## 9. Command-line interfaces with `argparse`

### The mechanism

```python
import argparse

parser = argparse.ArgumentParser(prog="ke")
subcommands = parser.add_subparsers(dest="command", required=True)

validate = subcommands.add_parser("validate")
validate.add_argument("--pack", metavar="NAME")
validate.add_argument("--strict", action="store_true")
```

- `add_subparsers` gives `ke validate`, and later `ke harvest`, `ke search`.
- `--pack NAME` takes a value.
- `action="store_true"` is a flag: present → `True`, absent → `False`.
- `--help` is generated automatically.

### The dispatch pattern

```python
validate.set_defaults(handler=_run_validate)

def main(argv=None):
    args = build_parser().parse_args(argv)
    return int(args.handler(args))
```

`set_defaults` attaches the *function* to the parsed arguments. `main()` then
calls whatever handler the subcommand chose — no `if command == "validate": ...
elif ...` chain to grow across nine milestones.

### Exit codes

A command-line program returns a number to the shell. `0` means success;
anything else means failure.

```python
return 1 if has_errors(findings, strict=args.strict) else 0
```

**This is the entire contract with CI.** GitHub Actions runs `python -m ke
validate` and fails the build if the exit code is non-zero. Ours: `0` clean,
`1` findings, `2` unusable pack.

### `argv=None` for testability

```python
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
```

`parse_args(None)` reads real command-line arguments. Passing a list overrides
them — which is how `test_cli.py` calls `main(["validate", "--strict"])` without
launching a subprocess.

---

## 10. Testing with pytest

### The mechanism

Any function named `test_*` is a test. Use plain `assert`.

```python
def test_feature_id_roundtrips_through_string():
    parsed = FeatureId.parse("MSF-2026-04-001")
    assert str(parsed) == "MSF-2026-04-001"
```

```bash
python -m pytest engine/tests -q          # all, quiet
python -m pytest engine/tests -x          # stop at first failure
python -m pytest engine/tests -k registry # only matching names
python -m pytest path::test_name -v       # one test, verbose
```

### Fixtures — reusable setup

```python
@pytest.fixture
def pack_root(tmp_path):
    root = tmp_path / "domain-packs" / "test-pack"
    ...
    return root

def test_empty_pack_is_valid(pack_root):     # ← just name it as a parameter
    assert check(pack_root) == []
```

pytest sees the parameter name, finds the matching fixture, runs it, passes the
result. Fixtures live in `conftest.py` and are available to every test file
automatically.

`tmp_path` is built in: a fresh temporary directory per test. Tests write real
files without touching your repository, and cleanup is automatic.

### Fixtures can build on fixtures

```python
@pytest.fixture
def populated_pack(pack_root):     # takes the empty pack…
    write_object(pack_root, make_object())
    ...                            # …and adds one object
```

### Parametrised tests

```python
@pytest.mark.parametrize("raw", [
    "MSF-2026-4-001", "MSF-2026-13-001", "msf-2026-04-001",
])
def test_feature_id_rejects_malformed_input(raw):
    assert FeatureId.is_valid(raw) is False
```

One function, one test run **per value** — so a failure names the exact input.

### Asserting that something raises

```python
with pytest.raises(PermissionError):
    obj.with_engine_fields(notes="clobbered")
```

The test passes only if that exception is raised. This is how we prove the
ownership guard works.

### Why our tests write files by hand

`conftest.py` builds knowledge objects with `yaml.safe_dump` rather than using
engine code — because `ke.store` does not exist until M2, and more importantly:

> Testing a checker with its own writer hides exactly the class of bug the
> checker exists to find.

If the writer had a bug, a writer-based test would produce a matching wrong file
and pass.

---

## 11. GitHub Actions

### The mechanism

A YAML file in `.github/workflows/` describing jobs GitHub runs on your events.

```yaml
name: CI

on:                        # when
  push:
    branches: ["**"]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest # a fresh VM per run
    steps:
      - uses: actions/checkout@v4          # a prebuilt action
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: python -m pip install -e ".[dev]"   # a shell command
      - run: python -m pytest engine/tests -q
      - run: python -m ke validate
```

Each `run` step executes in a shell. **If any step exits non-zero, the job
fails.** That is why exit codes (§9) matter.

### Details worth knowing

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
```

Pushing twice quickly cancels the first run. GitHub Free gives 2,000 Actions
minutes per month on private repos; this stops us burning them on results nobody
reads.

```yaml
permissions:
  contents: read
```

Least privilege. CI only reads code, so that is all it gets. M6's weekly workflow
will need `contents: write` to commit results.

```yaml
timeout-minutes: 10
```

A hung job cannot drain the monthly budget.

### The custom guard

Our last step greps every scheduled workflow for `ke generate`:

```bash
if grep -qE "^\s*schedule:" "$workflow"; then
  if grep -qE "ke +generate" "$workflow"; then offenders+=("$workflow"); fi
fi
```

The project's whole cost model and vendor-independence rest on the scheduled
pipeline never calling an AI model. **A rule that important should be checked by
a machine, not remembered by a person** — so it is enforced from M0, before the
weekly workflow it constrains even exists.

---

## 12. Validation: the pattern worth internalising

### Two ways to report a problem

**Raise an exception** — stops at the first problem:

```python
if not path.exists():
    raise FileNotFoundError(path)
```

**Return findings** — collects all of them:

```python
@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    location: str
    message: str

def _check_ownership(location, obj) -> list[Finding]:
    findings = []
    for name in obj.overrides:
        if name not in ENGINE_PROPOSED_FIELDS:
            findings.append(Finding(Level.ERROR, "OWN001", location, "..."))
    return findings
```

We collect. Three reasons:

1. **One run shows every problem.** Fixing 12 issues one CI run at a time is
   miserable.
2. **Severity becomes the caller's decision.** Same findings; `--strict` decides
   whether warnings are fatal.
3. **Tests assert on codes.** `assert "OWN001" in codes(findings)` keeps passing
   when you improve the wording.

### Stable codes

`ID003` always means duplicate Feature ID. Codes are greppable in CI logs,
searchable in the codebase, documented in `docs/SCHEMA.md` §9, and stable for
tests to assert on.

### Errors vs warnings

| | Meaning | Effect |
|---|---|---|
| **Error** | The rule is broken. Data may be at risk. | Exit 1 |
| **Warning** | Suspicious but recoverable. | Exit 0 (unless `--strict`) |

A missing `images/` directory is a warning — trivially recreated. A duplicate
Feature ID is an error — it breaks a core repository rule.

---

## 13. Repository structure as a design decision

### Code and data are separated

```
engine/         all code, knows nothing about Microsoft Fabric
domain-packs/   all data, contains no code
```

Grep proves it:

```bash
grep -ri "microsoft-fabric" engine/     # no matches
```

Three benefits:

1. **New packs are free.** `Pack.discover()` scans a directory, so adding Power
   BI in M8 means creating a folder — `git diff engine/` stays empty.
2. **The engine can move out.** `package-dir = {"" = "engine"}` means `engine/`
   is already a self-contained package.
3. **Knowledge outlives the engine.** Markdown and YAML in Git are readable in 20
   years whether or not this Python code still runs. That is what "GitHub is the
   single source of truth" means in practice.

### Configuration as data, not code

Categories, word limits and thresholds live in `pack.yml`:

```yaml
limits:
  max_summary_words: 120
categories: [data-engineering, data-warehouse, ...]
```

Tuning them is editing YAML, not changing code and rerunning tests. In M3,
classification *rules* move here for the same reason.

### Why a knowledge object is a directory

```
MSF-2026-04-001-direct-lake-ga/
├── feature.md
├── metadata.yaml
├── artifacts/
├── images/
└── references/
```

A single `.md` file would be lighter — until you attach a tutorial and an
infographic. Then it must become a directory, and **its path changes**, breaking
every index entry and link pointing at it.

The trade: an empty directory per object, versus path stability forever. Path
stability wins.

---

## 14. Recap

| Concept | Where to see it |
|---|---|
| Packages, import direction | `engine/ke/` |
| `pyproject.toml`, editable installs | `pyproject.toml` |
| Dataclasses, `frozen`, `order`, `replace` | `models.py` — `FeatureId`, `KnowledgeObject` |
| Enums as controlled vocabularies | `models.py` — `Difficulty`, `Tier` |
| `frozenset` algebra as a safety rule | `models.py` — ownership registry |
| Import-time assertions | `models.py` — the three `assert`s |
| YAML, `safe_load`, date coercion | `pack.py`, `models._coerce_date` |
| `argparse`, dispatch, exit codes | `__main__.py` |
| pytest fixtures, parametrize, `raises` | `engine/tests/` |
| GitHub Actions, least privilege | `.github/workflows/ci.yml` |
| Findings over exceptions | `validate.py` |

### Try these

```bash
python -m pip install -e ".[dev]"
python -m pytest engine/tests -q
python -m ke validate
python -m ke validate --help
```

Then break something on purpose and watch the guardrail fire:

1. Add `garbage_field: true` to a `pack.yml` — nothing happens (unknown *pack*
   keys are allowed; unknown *metadata* keys warn).
2. In `models.py`, add `"notes"` to `ENGINE_OWNED_FIELDS` while leaving it in
   `USER_OWNED_FIELDS`. Run the tests. The `AssertionError` appears at import,
   before a single test runs. **Undo this.**
3. Run `python -m pytest engine/tests -k ownership -v` and read the test names —
   they describe the safety rules in English.
