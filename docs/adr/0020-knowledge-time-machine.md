# ADR-0020: Capture history for a Knowledge Time Machine

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1 (model) · M2, M5, M7 (queries)

## Context

The intended long-term capability is not only "what does the pack know?" but
"what changed, and when?" — questions like *what changed in Fabric during July
2026*, *show everything since I last studied*, and *how has Direct Lake evolved
over two years*.

ADR-0009 recorded *that* an object changed — revision number, changed field
names, a one-line summary — and deliberately kept no old values, reasoning that
Git already stores them.

That reasoning is true and insufficient. Git holds the bytes, but reconstructing
history from it means running `git log -p` per object and parsing diffs: slow,
non-deterministic in output, unavailable through the GitHub UI, and lost the
first time the repository is squashed, re-imported or migrated. **Git is a
transport for history, not a queryable model of it.**

The timing matters more than the design. Query commands are cheap to add later;
**the data they need is impossible to add later**, because history not captured
at the time is simply gone.

## Decision

Capture history in two structures, chosen because they answer different questions.

**1. Revision snapshots — "how did *this* evolve?"** Each `Revision` gains
`content_hash`, `title_snapshot`, `summary_snapshot` and `run_id`. The object
becomes self-describing over time: its whole history is readable from one file,
in order.

Affordable because ADR-0003 already caps stored summaries at a short original
paragraph — this keeps a bounded amount of text we were already permitted to
store.

**2. An append-only event log — "what happened across the pack?"**
`state/events.jsonl`, one time-ordered JSON object per line, with types
`discovered`, `revised`, `reclassified`, `replaced`, `deprecated`,
`artifact-generated`, `artifact-stale`.

Range queries over the whole pack are the common case, and walking every object
to answer them costs time proportional to the pack rather than to the answer.

**Supporting decisions:** `run_id` appears on provenance, revisions, events and
the run log, so any run is fully reconstructable. All timestamps are UTC ISO-8601,
with naive values read as UTC. `published_date` (when Microsoft shipped it) and
`occurred_at` (when we learned of it) are kept as distinct clocks.

Full design: [`docs/design/TIME_MACHINE.md`](../design/TIME_MACHINE.md).

## Consequences

### Positive
- **All five target queries become deterministic file scans** — no AI, no index,
  no database.
- **History survives repository surgery.** Squash, re-import or migrate to
  another host and the history is still there, because it is in the files.
- **Objects are self-describing**, readable in the GitHub UI by a human with no
  tooling.
- **`content_hash` per revision forms a verifiable chain**, so corruption or an
  out-of-band edit is detectable.
- **Two clocks kept separate**, so "what shipped in July" and "what I learned of
  in July" cannot be silently confused.

### Negative
- **`metadata.yaml` grows with every revision.** Bounded by the summary word
  limit, but an object revised fifty times will carry fifty snapshots. If that
  becomes a problem, older snapshots can be truncated with the hashes retained —
  but not before there is evidence it is one.
- **`events.jsonl` grows forever.** By design: it is the history. Roughly 200
  bytes per event, so tens of thousands of events is a few megabytes.
- **Two places record that something changed**, so a writer bug could desynchronise
  them. `ke validate` should gain a consistency check when M2 starts writing both.
- **Snapshots duplicate the current summary** at the latest revision. Accepted for
  uniformity: special-casing the newest revision would complicate every reader.

### Neutral
- JSON Lines rather than one JSON array: appends never rewrite earlier bytes, so
  diffs stay minimal and concurrent runs cannot conflict over the same line.
- Amends ADR-0009's "Git already has them" position, without changing its core
  decision that corrections are revisions rather than rewrites.

## Alternatives considered

**Rely on Git history.** The original position. Rejected for the reasons above —
it is a transport, not a model, and it does not survive repository surgery.

**Store full previous article text.** Would answer more questions. Rejected:
ADR-0003 forbids storing third-party article text, and repository size would grow
without bound.

**Event log only, no revision snapshots.** Would answer the cross-pack queries
but not "how did this object evolve" without reassembling state from the log.
Rejected: reading one file must remain sufficient to understand one object.

**Revision snapshots only, no event log.** Rejected: makes every time-range query
a full pack walk, which scales with the pack rather than with the answer.

**Precomputed monthly rollups.** Rejected as premature: cheap to compute from the
log, expensive to keep correct.
