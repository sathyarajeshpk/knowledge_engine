# ADR-0009: Update in place with a revision history

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

Sources change. Microsoft retitles an announcement, expands an article, or
corrects an error. The weekly run will encounter an item it has already stored,
with different content.

`CLAUDE.md` says knowledge is never deleted and chronological order is preserved.
Neither rule says what to do when a *source* changes, and the two obvious answers
have very different consequences:

- **Update the existing object.** One ID per concept, learning state carries
  forward — but the old content is gone unless something records it.
- **Mint a new object and mark the old one replaced.** Perfect audit trail — but
  one concept fragments across many IDs, and learning state does not follow.

## Decision

**Update the existing object in place, touching only engine-owned fields, and
append a numbered revision entry.**

```yaml
revisions:
  - revision: 1
    date: 2026-08-03
    changed_fields: []
    summary: Initial ingestion
  - revision: 2
    date: 2026-09-14
    changed_fields: [title, content_hash]
    summary: Source article retitled and expanded
```

Rules:

- The Feature ID represents the **concept**, not the article revision. It does
  not change.
- Only engine-owned fields may change (ADR-0008). User-owned fields survive
  byte-for-byte.
- A revision is appended **only when an engine-owned field actually changed**. A
  run that finds nothing new writes nothing.
- Revisions are append-only and never rewritten.
- Generated artifacts whose `generated_from_revision` is now behind the current
  revision become **`stale`** — marked, never deleted.
- A **new Feature ID is minted only when the source introduces a genuinely new
  feature or concept.** When a new object supersedes an old one, `ke supersede`
  sets `status: replaced` and links both directions. The old object stays.

## Consequences

### Positive
- **One ID per concept.** References, relationships and anything the user wrote
  down stay valid across source edits.
- **Learning state carries forward.** Marking something `learned` does not get
  undone because Microsoft fixed a typo.
- **Change is legible.** `changed_fields` and `summary` say what moved and why,
  which is more useful than a Git diff alone, because it distinguishes "the
  source changed" from "the engine recomputed a hash".
- **Staleness becomes computable.** `generated_from_revision < current_revision`
  is a deterministic comparison, so the pipeline can flag out-of-date tutorials
  without calling a model (ADR-0004).
- **Nothing is lost.** Old content lives in Git history; the fact that it changed
  lives in the file itself.

### Negative
- **The previous summary text is not retained in the file**, only in Git history.
  A revision records *that* fields changed, not their old values. Storing old
  values would grow files without bound; Git already holds them.
- **Deciding "genuinely new feature" versus "updated article" is a judgement
  call** that no rule fully automates. M5 will use `content_hash` divergence as a
  heuristic and flag ambiguous cases with `needs_review` rather than guess.
- **Revision numbers grow forever** on frequently-edited sources. Small.
- **The update path is more complex than a create path**, because it must merge
  engine fields while preserving user fields exactly. This is why M5 exists as
  its own milestone and lands *before* the weekly cron in M6 — the second run and
  every run after it is an update run.

### Neutral
- `revisions` is engine-owned, so the user cannot annotate a revision. If that
  proves desirable, `notes` is available and user-owned.

## Alternatives considered

**Mint a new ID for every meaningful source change**, marking the old
`replaced`. Strongest possible audit trail. Rejected: one concept fragments
across many IDs, so relationships and references need constant updating, learning
state does not carry forward, and the pack fills with near-identical objects.
The audit benefit is already provided by Git plus the revision list.

**Update in place with no revision history.** Simplest. Rejected: you lose the
ability to tell *why* an artifact went stale, and "the source changed on this
date" is genuinely useful information that Git history makes available but not
legible.

**Store full previous versions inside the object** (a `history/` directory).
Rejected: unbounded growth for information Git already stores perfectly, and it
would collide with ADR-0003's decision to keep only short summaries.

**Never update — treat the first capture as canonical.** Rejected: the knowledge
base would slowly fill with statements that are no longer true, which defeats the
purpose.
