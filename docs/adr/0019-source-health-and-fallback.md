# ADR-0019: Source health, fallback chains, and never failing silently

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1 (model) · M2, M6 (behaviours)

## Context

Source validation produced an uncomfortable result: of 22 candidate endpoints,
the two "official" feeds return 403, four more return 404, and the highest-signal
source is an HTML page whose markup Microsoft controls and can change at any time.

This is not a robust foundation, and no amount of care in choosing sources makes
it one. The engine must therefore be built to *expect* source failure.

The dangerous failure is not a source returning an error — that is loud and
obvious. It is a source returning **HTTP 200 and zero items**, which is
indistinguishable from a genuinely quiet week. A parser that broke in March
produces a pipeline that has silently collected nothing since, and nobody
notices until they go looking for something that should be there.

## Decision

Design for source failure as a normal condition, not an exception.

**Four health states:** `healthy`, `degraded`, `failed`, `disabled`. `degraded`
carries the weight: reachable and returning items, but either falling back to a
secondary source or returning far fewer items than its historical median.

**A failed source never fails the run.** `RunReport.succeeded_overall` depends
only on whether the run completed. Failing the run would discard successful
harvesting from healthy sources *and* skip the run-log commit that keeps the
weekly cron alive — turning one broken feed into a dead pipeline.

**Parser-break detection.** Median of recent successful item counts; a run below
34% of that baseline is flagged as a suspected parser break rather than accepted
as "no news". At least three observations are required before the baseline means
anything.

**Fallback chains.** Primary → secondary → manual review. If every link fails, a
review item is raised. An empty result and a broken source must never look the
same.

**Escalation.** Three consecutive failures opens a `Source Health Alert - <name>`
GitHub Issue via the ADR-0013 notifier. `open_alert_issue` prevents duplicates
while one remains open.

**Bounded history.** 26 successful runs, roughly six months, so trends are
analysable without the file growing without limit.

Full design: [`docs/design/SOURCE_HEALTH.md`](../design/SOURCE_HEALTH.md).

## Consequences

### Positive
- **Silent failure becomes structurally difficult.** Every failure lands in the
  health file, the run log, the digest and eventually an Issue.
- **One bad source cannot stop the pipeline**, which matters when most candidate
  sources are demonstrably fragile.
- **Degraded is distinguishable from healthy**, so gradual decay is visible
  before it becomes total.
- **Fallbacks preserve knowledge** that a single-source design would lose.
- **Alerts are actionable and non-repeating**, so they stay worth reading.
- No database: JSON state files, consistent with ADR-0003.

### Negative
- **Real complexity added to the pipeline** — chains, states, baselines,
  escalation. Justified by the evidence that sources fail often, but it is
  genuine weight against CLAUDE.md's "prefer simplicity".
- **The threshold is a guess.** 34% and three observations are starting points
  with no data behind them yet; they will need tuning once real baselines exist.
  They are constants in `models.py`, not scattered literals, precisely so tuning
  is a one-line change.
- **False parser-break warnings will happen** during genuinely quiet periods. The
  asymmetry is deliberate: a false warning costs a glance, a missed break costs
  months.
- **`source-health.json` is committed every run**, adding a small weekly diff.
  That is also what makes the history auditable.
- Fallback chains mean an item's origin depends on runtime conditions —
  mitigated by recording `source_role` in provenance.

### Neutral
- Health lives outside knowledge objects: it describes the source, not the
  knowledge.
- `disabled` is only ever set by a human. The engine escalates; it does not
  give up on a source by itself.

## Alternatives considered

**Fail the run on any source failure.** Simple and loud. Rejected: it discards
good work and kills the cron, converting a cosmetic problem into an outage.

**Treat zero items as "no news".** The naive default. Rejected — this *is* the
failure being designed against.

**Alert on the first failure.** Rejected: transient network errors are common and
an alert channel that cries wolf gets muted, which is worse than no alerting.

**Health as a derived view over run logs, with no state file.** Appealingly
simple. Rejected: computing a baseline would mean parsing the entire run-log
history every run, and `open_alert_issue` needs somewhere durable to live.

**Mean instead of median for the baseline.** Rejected: one anomalous week would
move the mean enough to mask a genuine break the following week.
