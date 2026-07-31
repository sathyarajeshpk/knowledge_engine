# ADR-0012: Validation returns findings rather than raising

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

`ke validate` performs 25 distinct checks across pack structure, metadata schema,
Feature ID integrity, field ownership, file consistency and the ID registry.

A validator can report problems two ways: raise on the first one, or collect them
all and return a list. The choice shapes how usable the tool is, how testable it
is, and whether severity can be distinguished at all.

Raising is the Python default and is right for parsing, where continuing past a
malformed input is meaningless. Validation is different: finding problems is the
function's entire purpose, not an exception to it.

## Decision

Check functions **return `list[Finding]`**. Nothing in `validate.py` raises for a
validation problem, and nothing prints.

```python
@dataclass(frozen=True)
class Finding:
    level: Level      # ERROR or WARNING
    code: str         # stable, e.g. "ID003"
    location: str     # repo-relative path
    message: str      # human-readable
```

Codes are stable and grouped by family (`PACK*`, `OBJ*`, `SCHEMA*`, `ID*`,
`OWN*`, `CONS*`, `COPY*`, `REG*`). Severity is decided by the caller:
`has_errors(findings, strict=False)` fails on errors only; `strict=True` fails on
warnings too.

Within a single object, checking **does** stop early once the object is
unparseable — `_check_object` returns `(findings, None)` and the object is
excluded from cross-object checks. Fail fast within a unit, continue across
units.

## Consequences

### Positive
- **One run reports every problem.** Fixing twelve issues one CI run at a time is
  miserable and slow.
- **Severity is a caller decision.** The same findings drive a lenient local run
  and a strict CI run without duplicating logic.
- **Tests assert on codes, not messages.** `assert "OWN001" in codes(findings)`
  keeps passing when the wording improves. All 42 validate tests do this.
- **Codes are greppable** in CI logs and in the codebase, and documented in
  `docs/SCHEMA.md` §9.
- **Output formatting is separable.** `_report()` in `__main__.py` handles
  presentation; the checks are pure functions returning data, so they are
  callable from anything.
- **Warnings become possible at all.** A missing `images/` directory is worth
  mentioning but not worth failing a build over. With exceptions, everything is
  fatal or invisible.

### Negative
- **More plumbing than `raise ValueError`.** Every check builds and returns a
  list; callers must remember to extend rather than discard.
- **Nothing forces the caller to look.** A caller can ignore the returned list,
  where an exception would be impossible to miss. Mitigated by `main()` being the
  only caller and by `test_cli.py` pinning the exit codes.
- **Cascading findings are possible** if the early-return discipline is not kept.
  One malformed object could otherwise produce fifteen confusing registry errors.
- **Code allocation needs discipline** so families stay coherent and numbers are
  never reused.

### Neutral
- Genuine failures — a missing or unparseable `pack.yml` — still raise
  `PackError`, because at that point there is nothing to validate. The CLI
  catches it and exits `2`.
- `Finding` is frozen, so findings can be safely collected, sorted and compared.

## Alternatives considered

**Raise on the first problem.** Simple, idiomatic, impossible to ignore.
Rejected: a validator that stops at the first error turns a ten-minute fix into
ten CI runs, and it cannot express "this is suspicious but not fatal".

**Collect exceptions into an `ExceptionGroup`** (Python 3.11+). Genuinely
interesting and closer to idiomatic modern Python. Rejected: exceptions carry
tracebacks and construction cost that a data record does not need, severity is
awkward to model, and asserting on exception contents in tests is clumsier than
asserting on a code.

**Print findings directly and return a count.** Rejected: makes the checks
untestable without capturing stdout, and unusable from any other code.

**A logging framework.** Rejected: adds configuration, makes tests depend on
handler setup, and log output is not a return value the CLI can reason about.

**Boolean return plus a message string.** Rejected: cannot express multiple
problems, severity, or location.
