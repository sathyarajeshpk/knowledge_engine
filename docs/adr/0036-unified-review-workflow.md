# ADR-0036: One review workflow over many kinds of pending decision

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M5

## Context

By the end of M4 the engine had produced two independent backlogs:

* **26 queued items** — discovered but not minted, because identity was not
  trusted enough (ADR-0028).
* **45 flagged objects** — minted, but no classification rule could place them
  (ADR-0010).

Each had its own storage, its own semantics, and — critically — its own way of
being worked, one item at a time. M5 was about to add a third for revisions.

Three backlogs growing in parallel is how a review queue becomes permanent. Each
one is somebody else's problem, none of them is ever obviously the most urgent,
and the total is never visible in one place.

## Decision

**Unify the workflow. Do not unify the storage.**

One command, one listing, one ordering, one set of actions:

```
ke review list      everything pending, most urgent first
ke review next      the single most urgent item
ke review show      full context for one decision
ke review approve | archive | resolve
ke review resolve --all --kind unclassified     bulk
```

Storage deliberately stays where each kind belongs:

| Kind | Lives in | Why it cannot move |
|---|---|---|
| queued | `state/review-queue.json` | Has no Feature ID and no object — there is nowhere else |
| unclassified | the object's own `metadata.yaml` | A fact about an object belongs to the object (ADR-0002) |
| revision | derived from the object's revision history | Already recorded; a second copy would drift |

A `ReviewTask` is a **view**, assembled on demand and never persisted. A stored
view of state that lives elsewhere is a second copy waiting to disagree.

**Adding a kind is one provider function plus one entry in `PROVIDERS`.**
Listing, filtering, ordering, bulk actions and `next` come for free — which is
the property that makes it cheap to route M6's and M7's review needs here
instead of building a fourth queue.

**Ordering is by urgency of kind, then age.** Queued items sort first because
the knowledge is not in the pack at all yet; unclassified objects sort last
because the knowledge is safely stored and only its metadata is incomplete.

## Consequences

### Positive
- The total backlog is one number, visible in `indexes/review-queue.md` without
  running anything.
- Bulk actions exist: 45 unclassified objects can be cleared in one command
  rather than 45.
- A new review kind cannot accidentally create a fourth silo — the cheap path is
  now the correct one.
- Each kind keeps the storage that suits it, so nothing had to be migrated.

### Negative
- **`collect()` reads every object on every call**, because unclassified and
  revision tasks are derived from stored objects. At 222 objects this is
  milliseconds; it shares the fate of TD-2.
- **A provider that raises is skipped silently** so one broken kind cannot hide
  the others. That is the right trade for a review listing, but it means a
  consistently failing provider shows as an empty category rather than an error.
- **Bulk actions are powerful.** `resolve --all` clears 45 flags in one command
  with no dry-run. Acceptable because resolving is reversible — the flag can be
  set again — but it would not be acceptable for an action that minted or
  deleted.

## Alternatives considered

**One unified queue file.** Simplest to reason about, and it would have required
moving `needs_review` off the objects that own it, breaking "the repository is
the source of truth". Rejected.

**Leave them separate and add a summary command.** Cheaper, and it addresses the
visibility problem without addressing the workflow one: you would still work
each backlog with a different command and different arguments.

**A UI.** Explicitly out of scope — GitHub Pages is unavailable for private repos
on the Free plan, and a CLI is sufficient for a single-user workflow.
