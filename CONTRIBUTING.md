# Contributing

This is a personal knowledge project with a deliberately strict structure. The
rules below exist to keep guarantees intact, not to create ceremony.

**Read first:** [`CLAUDE.md`](CLAUDE.md) for the non-negotiable rules, then
[`docs/SCHEMA.md`](docs/SCHEMA.md) for the data contract.

---

## Setup

```bash
git clone <repo> && cd knowledge_engine
python -m pip install -e ".[dev]"     # Python 3.11+
python -m pytest engine/tests -q      # 107 tests, ~0.6s
python -m ke validate                 # check every Domain Pack
```

Those last two commands are exactly what CI runs. If they pass locally, CI
passes.

---

## The rules that cannot be broken

These are enforced by mechanisms, not by review. Breaking one fails the build.

| Rule | What enforces it |
|---|---|
| Knowledge is never deleted | `ObjectStatus` has no `deleted` member |
| The engine never writes user-owned fields | `with_engine_fields()` raises `PermissionError` |
| Ownership classes never overlap | Import-time `assert`s in `models.py` |
| Feature IDs are unique and permanent | `ke validate` — `ID003`, `REG002` |
| No AI model in the scheduled pipeline | CI step scanning scheduled workflows |
| No full third-party article text | `ke validate` — `COPY001` |

If a change requires breaking one of these, it needs an ADR that supersedes the
existing one — not a workaround.

---

## Milestone workflow

Work proceeds one milestone at a time. **Do not start the next milestone until
the current one is reviewed, merged and explicitly approved.**

1. **Implement** only the approved milestone. Nothing from the next one.
2. **Stop and explain** if a design problem surfaces mid-implementation. Do not
   change the agreed architecture unilaterally — raise it, agree a direction,
   record an ADR.
3. **Deliver**, without being asked:
   - A **PR review summary** written as a Senior Software Architect.
   - A **Developer Playbook** at `docs/playbook/M<n>_<NAME>.md`.
   - A **Learning Guide** at `docs/learning/M<n>_LEARNING_GUIDE.md`.
   - An **interactive code walkthrough**, one file at a time.
   - Updates to `docs/JOURNAL.md`, `docs/ROADMAP.md` and `CHANGELOG.md`.
   - New **ADRs** for any significant decision.
4. **Wait for approval.**

---

## Coding standards

**Python 3.11+.** `StrEnum` and `X | None` syntax are used throughout.

**Stdlib first.** Every dependency is a permanent cost — CI time, supply-chain
surface, upgrade work. Add one only when the alternative is materially worse, and
add it in the milestone that needs it, not in advance.

**Type hints on every public function.** They are documentation that tools can
check.

**Docstrings explain *why*, not *what*.** The code says what it does.

```python
# Not this:
def month_key(self):
    """Return the month key."""

# This:
def month_key(self):
    """The `id-registry.json` bucket for this ID, e.g. `2026-04`.

    Counters are per month so that backfilling an old month mints correctly
    dated IDs without disturbing the current month.
    """
```

**Dependency direction is one-way.** `models.py` imports nothing from `ke`;
`pack.py` imports nothing from `validate.py`. Arrows never point backwards. If
you need a backwards import, something is in the wrong module.

**No pack names in `engine/`.** This must stay true:

```bash
grep -ri "microsoft-fabric" engine/     # must return nothing
```

If you find yourself writing `if pack.name == "...":`, move the decision into
`pack.yml` as data.

**Configuration is data.** Categories, limits, thresholds and rules belong in
`pack.yml`. Changing behaviour should be a text edit, not a release.

**Make invalid states unrepresentable** where you can. It is cheaper than
validating against them.

### Adding a metadata field

Four steps, every time. Skip one and the tests will tell you:

1. Add it to the `KnowledgeObject` dataclass, in the right ownership block.
2. Add it to **exactly one** ownership frozenset in `models.py`.
3. Add it to `to_metadata_dict()` **and** `from_metadata_dict()`.
4. Document it in `docs/SCHEMA.md` §4.

Removing or renaming a field is different: it needs a `SCHEMA_VERSION` bump and a
migration, because files already on disk use the old name.

---

## Testing requirements

**Every behaviour change needs a test.** Every new validation check needs a test
that proves it fires — a guardrail nobody proved fires is not a guardrail.

**Write the failing test first** for validation work. It is the only way to know
the check does anything.

**Assert on codes, not messages:**

```python
assert "OWN001" in codes(findings)          # survives rewording
assert "may only lock" in findings[0].message   # brittle
```

**Test failure, not just success.** The bugs live in the error paths.

**Do not build test inputs with engine code.** `conftest.py` writes YAML directly
with `yaml.safe_dump`. If the writer had a bug, a writer-based test would produce
a matching wrong file and pass. Independent construction is the point — see the
docstring in `engine/tests/conftest.py`.

**Name tests as statements of the rule:**

```python
def test_engine_may_never_write_user_fields():          # good
def test_a_counter_ahead_of_disk_is_allowed():          # good — pins intent
def test_validation():                                   # useless at 2am
```

**Pin deliberate non-behaviour.** If something looks like a bug but is
intentional, write a test saying so. Otherwise someone will "fix" it.

Useful commands:

```bash
python -m pytest engine/tests -q              # all
python -m pytest engine/tests -x              # stop at first failure
python -m pytest engine/tests -k ownership -v # by name
python -m pytest engine/tests --durations=5   # slowest
```

---

## Commit conventions

**Format:**

```
M<n>: short imperative summary

Why this change was made and what it decides. Explain the architectural
reasoning, not the diff — the diff is already in the commit.

Note anything deliberately deferred or deliberately not done.

Co-Authored-By: ...
```

**Rules:**

- **Prefix with the milestone**: `M0:`, `M1:`. Chores may use `chore:`.
- **Imperative summary**, under ~70 characters: "add pack loader", not "added".
- **Small, logical commits.** One decision per commit. The eight M0 commits each
  correspond to one work item.
- **The body explains the decision.** "Adds X" is worthless; "Adds X because Y,
  rejecting Z because W" is what makes `git log` useful in a year.
- **Never mix** a refactor with a behaviour change.
- **Never commit** secrets, `.venv/`, `__pycache__/`, or generated context packs.

Good:

```
M0: add core data models and field ownership registry

The centrepiece is the field ownership registry. Adding user-maintained
learning state to files that an automated weekly job rewrites creates one
dominant risk: the job destroying the user's own work. The registry
partitions every metadata field into three classes, asserts that partition
at import time, and routes every automated write through
with_engine_fields, which raises PermissionError rather than clobbering.
```

---

## Architecture Decision Records

Write an ADR when a decision is **expensive to reverse**, **constrains future
work**, or **would look wrong without its context**.

- Copy the template from [`docs/adr/README.md`](docs/adr/README.md).
- Number sequentially; never reuse a number.
- **ADRs are immutable once accepted.** Changed your mind? Write a new one that
  supersedes it and update the old one's status.
- **Record the rejected alternatives.** They are usually the most useful part.
- Add it to the index table.

---

## Branches and pull requests

**Branch naming:** `claude/<topic>` or `m<n>-<topic>`, e.g. `m1-discovery`.

**Never commit directly to `main`.**

**Before opening a PR:**

```bash
python -m pytest engine/tests -q
python -m ke validate
git log --oneline main..HEAD          # read your own commits
```

**PR description should cover:** what was built, the architectural decisions and
why, limitations, assumptions, and how to test it. Link the ADRs.

---

## Review process

A reviewer is checking, in order:

1. **Does it break a guarantee?** Ownership, ID permanence, deletion, no-AI.
   Everything else is negotiable; these are not.
2. **Are the new rules enforced by mechanisms**, or only by documentation?
3. **Do the tests prove what they claim?** Especially: does each new validation
   check have a test that makes it fire?
4. **Is `engine/` still free of pack-specific knowledge?**
5. **Are the decisions recorded** where a future contributor will find them?
6. **Is the scope right?** Milestone work should not quietly include the next
   milestone.

Reviews prefer a stated concern over a silent fix. If something in the
architecture is wrong, say so and stop — do not work around it.

---

## Domain Packs

Packs are **data**. Contributing to a pack never means changing engine code.

Adding one (after M8):

1. `mkdir -p domain-packs/<name>/state` — only `state/` is created up front;
   `knowledge/`, `indexes/` and `digests/` are created on demand, because Git
   cannot store an empty directory ([ADR-0015](docs/adr/0015-create-object-subdirectories-on-demand.md))
2. Write `pack.yml` with a **permanent** `id_prefix` — changing it later orphans
   every Feature ID minted under it.
3. Initialise `state/id-registry.json` as `{"counters": {}, "paths": {}}`.
4. `python -m ke validate`

If step 4 required an engine change, that is a bug in the engine, not in the
pack.

---

## Questions

Open an issue. For anything touching the architecture, propose an ADR rather than
a patch — the reasoning is the valuable part.
