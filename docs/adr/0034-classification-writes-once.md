# ADR-0034: Classification proposes once and never churns

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M4
**Relates to:** ADR-0008 (field ownership), ADR-0010 (three classification axes), ADR-0004 (no AI)

## Context

Classification is the first code to write the **engine-proposed** field class.
It is also the first stage capable of rewriting every object in the pack at
once: 222 objects, one rule tweak.

Two failure modes had to be closed before it could run:

* **Churn.** If classification re-ran and re-decided every harvest, the weekly
  git diff would fill with reclassifications and a genuine knowledge change
  would be invisible in the noise.
* **Feedback.** Classification writes `category` and `tags`, which are text on
  the object. If it also *reads* them, the result depends on whether it has run
  before — measured, a second harvest reclassified four more objects than the
  first.

## Decision

**Rules live in `pack.yml` as data**, never in engine code. A second Domain Pack
needs no engine change, and tuning the vocabulary needs no release.

**First matching rule wins**, in the order written. Order is the pack author's
priority statement. Scoring or best-match would make the outcome depend on the
whole rule set, so adding an unrelated rule could silently reclassify existing
objects.

**Write once, when the field is unset.** Engine-proposed means the engine may
offer a value where the user has not expressed one (ADR-0008). Re-running with
changed rules does **not** rewrite a classification that already landed.

**"Unset" means "still at the model default", not "falsy."** `tier` defaults to
`AWARENESS` and `difficulty` to `INTERMEDIATE` — both truthy — so a falsiness
check made every enum-valued field look already-decided and classification wrote
nothing at all while reporting success.

**Classification never reads its own output.** `category` and `tags` are
excluded from the text rules match against, so the result is a pure function of
the knowledge.

**Never a silent guess.** An object no `tier` or `category` rule can place is
flagged `needs_review` rather than given a plausible default (ADR-0010).

**Every proposal records which rule produced it**, so disagreeing with a
classification means editing a rule rather than reading engine code.

## Consequences

### Positive
- A harvest that changes no knowledge changes no classification.
- Rule changes are safe to make: they affect future objects, not the archive.
- Classification is explainable from data, and adjustable without a release.
- Deterministic and offline, consistent with ADR-0004.

### Negative
- **Rule changes do not apply retroactively.** Improving a rule leaves the
  existing 222 objects as they are. There is deliberately no `ke reclassify`
  yet, because a command that rewrites the whole pack needs a dry-run, a diff
  preview and a way to exclude locked fields — worth building properly rather
  than as a flag.
- **Substring matching is blunt.** "sql" matches "sql database" and
  "nosql". Rules are ordered and exclusions exist, but a pack author can still
  write a rule that over-matches, and nothing warns them.
- **A field the user deliberately set to the default value is indistinguishable
  from an unset one**, so classification may overwrite it. The remedy is
  `overrides`, which is the documented way to express a preference.
- The 45 objects flagged `needs_review` have no triage path beyond editing them
  by hand (TD-8 territory).

## Alternatives considered

**Reclassify every object every run.** Guarantees rules and objects agree, and
churns the diff on every rule edit. Rejected: the weekly diff is the product's
main signal.

**Score rules and take the best match.** More expressive, and makes every
classification depend on the entire rule set — so adding one rule can change
unrelated objects. Rejected for the same reason ordering was chosen.

**Regex rules instead of substrings.** More powerful; requires pack authors to
know regex escaping, and a malformed pattern could break a harvest. Substrings
plus an exclusion list cover the observed cases.

**Ask a model to classify.** Forbidden by ADR-0004, and non-deterministic
classification would churn the diff by construction.
