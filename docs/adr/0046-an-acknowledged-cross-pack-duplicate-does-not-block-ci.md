# ADR-0046 — An acknowledged cross-pack duplicate is reported but does not block CI

**Status:** Accepted (amends ADR-0044)
**Date:** 2026-08-08
**Relates to:** ADR-0044 (cross-pack duplicates are reported, never resolved)

## Context

ADR-0044 established two things about a cross-pack duplicate:

> **`ke validate` warns, never errors** (XPK001). Failing CI over a cross-pack
> duplicate would make a judgement the engine is not entitled to make.

and

> A resolution is recorded so the same duplicate is not repeatedly surfaced.

M9 found that the second promise was only half kept, and that the first was
about to be broken by a flag.

**[measured]** `ke review` resolves duplicates through `outstanding()`, which
filters out acknowledged pairs. `ke validate` reported them through
`find_duplicates()`, which does not. So acknowledging a duplicate cleared the
review queue and left the validation warning standing:

```
duplicates found                : 1
XPK001 before acknowledging     : 1

after `ke review resolve`:
  outstanding()  [ke review]    : 0
  XPK001         [ke validate]  : 1     ← still reported
```

Two commands disagreed about the same fact, and the one that disagreed was the
one CI runs.

That became a blocker when M9 approached enabling `ke validate --strict`, which
fails on **any** warning. Under `--strict`:

* a legitimate cross-pack duplicate — two packs filing one announcement under two
  taxonomies, which ADR-0044 explicitly calls *often correct* — would fail CI;
* and the acknowledgement mechanism built for exactly that case would have **no
  effect on the check that failed**.

The only remedies would have been editing the packs to remove real knowledge, or
disabling `--strict`. Both are worse than the problem.

## Decision

**`ke validate` honours the resolution store, and reports an acknowledged
duplicate as `INFO`.**

| State | Level | Blocks `--strict` |
|---|---|---|
| Unreviewed duplicate | `WARNING` | **Yes** |
| Acknowledged duplicate | `INFO` | No |

Three things this does **not** change, all load-bearing:

1. **The engine still does not resolve the duplicate.** Both objects are kept,
   neither is modified, nothing is merged, dropped or suppressed at mint time.
   ADR-0044's central rule is untouched.
2. **The engine still does not choose a winner.** Acknowledging records that a
   *human* looked; it does not record which object is right, because the engine
   has no basis for that and never will.
3. **The finding stays visible.** `INFO` is reported, not hidden. An acknowledged
   duplicate remains in `ke validate` output and remains reviewable.

`INFO` rather than removal is the same choice made for baselined REV002 findings
(M9 Gate D): a suppression nobody can see is a suppression nobody can audit.

## Consequences

**ADR-0044's promise now holds end to end.** "A resolution is recorded so the
same duplicate is not repeatedly surfaced" is true of both commands rather than
one.

**`--strict` and ADR-0044 stop contradicting each other.** An *unreviewed*
duplicate blocks — which is right, because nobody has looked at it. A *reviewed*
one does not — which is right, because somebody has. The engine's judgement is
never what decides; a human's acknowledgement is.

**A new duplicate still blocks.** Acknowledgement is keyed on the canonical
sorted Feature ID pair, so a *different* pair is a different key and is not
covered by an existing acknowledgement. The same property that makes the REV002
baseline safe applies here.

**One asymmetry worth naming.** Acknowledging is cheap — one command — and
un-acknowledging is a manual edit of `state/cross-pack.json`. That is the right
way round: the expensive direction should be the one that hides things.

## Alternatives rejected

**Leave `ke validate` reporting every duplicate and enable `--strict` with
XPK001 excluded.** Weaker on two counts: it leaves two commands disagreeing
about the same fact, and it removes the check's ability to block an *unreviewed*
duplicate — which is the case actually worth blocking.

**Filter acknowledged duplicates out entirely.** Makes them invisible. The
project has now twice found that an invisible finding is a finding nobody can
audit — the whole-chain REV002 detector, and this.

**Make XPK001 an error.** Directly contradicts ADR-0044.

**Leave it alone and never enable `--strict`.** Defensible, and it was the
status quo. Rejected because the underlying inconsistency is a defect
independent of `--strict`: acknowledging something and having it still reported
is wrong whether or not CI is strict.
