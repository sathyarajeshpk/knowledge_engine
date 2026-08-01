# ADR-0039: A lock file around minting

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M6

## Context

Feature IDs are permanent (ADR-0005). They are allocated from a per-month
counter in `state/id-registry.json`, which is read, incremented and written back
during a harvest.

Two harvests overlapping would both read the same counter and both allocate the
same number. The result is a duplicate Feature ID — which CLAUDE.md forbids
outright, and which cannot be repaired, because IDs are never renumbered or
reused. Of everything the engine can get wrong, this is the only failure that is
genuinely permanent.

Before M6 the risk was theoretical: harvests were run by hand, one at a time, by
one person. A cron changes that. The scheduled run can now overlap with a manual
`ke harvest`, a `workflow_dispatch`, a re-run of a failed job, or a second
scheduled run that started while the first was still going.

GitHub's `concurrency` group solves the part GitHub can see. It does not know
about a `ke harvest` running on a laptop against the same checkout.

## Decision

**Take an exclusive lock on the pack's state directory for the duration of a
harvest**, using `O_CREAT | O_EXCL` — a single atomic syscall that either
creates the file or fails, with no window between checking and creating.

```
state/.harvest.lock
```

Three properties:

* **Released in `finally`.** A crashed harvest must not hold the lock.
* **Stale locks are reclaimed after an hour** (`STALE_AFTER_SECONDS`). A process
  killed hard — an OOM, a cancelled runner, a closed laptop — leaves the file
  behind, and a lock that can wedge the pack forever is worse than no lock at
  all. A harvest takes minutes; an hour is far outside the normal range.
* **An unreadable or corrupt lock file is reclaimed, not obeyed.** A lock whose
  contents cannot be parsed carries no information about who holds it, and
  treating "I cannot read this" as "somebody is working" is how a system
  deadlocks on its own garbage.

The lock is taken in the CLI, around `harvest_pack`, not inside the pipeline.
Locking is a property of *running a harvest*, not of the algorithm, and a
pipeline that grabs OS resources cannot be tested as a pure staged transform.

## Consequences

**Two lines of defence, covering different things.** The workflow's
`concurrency: weekly-harvest` group prevents two scheduled runs; the lock
prevents everything else, including the case GitHub cannot see. Neither
subsumes the other.

**The lock is advisory and local.** It protects one filesystem. It does not
protect two runners in different containers pushing to the same repository —
that case is handled by the push step's rebase, and by the fact that a duplicate
ID would be caught by `ke validate` before the push. This ADR does not claim
otherwise.

**A concurrent run fails rather than waits.** `ke harvest` reports the lock and
exits 2. Queueing would be friendlier and is deliberately not implemented: the
scheduled path already queues via `cancel-in-progress: false`, and a CLI that
silently blocks for an unknown period is a worse experience than one that says
what is happening.

**Stale reclamation is a real trade-off.** An hour-old lock is assumed dead. If
a harvest genuinely ran for over an hour and a second one started, both could
mint. Accepted: a 20-minute workflow timeout makes that impossible on the
scheduled path, and the alternative — never reclaiming — turns one crashed run
into a permanently broken pack.

## Alternatives considered

**`fcntl.flock`.** Cleaner semantics, automatic release on process death, no
staleness problem. Rejected for portability: it behaves differently on network
filesystems and is not available on Windows, and this engine is meant to be
runnable anywhere its owner happens to be.

**Rely on the workflow concurrency group alone.** Rejected: it is blind to
anything not started by Actions, and the failure it fails to prevent is
permanent.

**Detect duplicate IDs after the fact in `ke validate`.** Already done, and
retained — but detection is not prevention. By the time a validator sees a
duplicate ID, two knowledge objects exist that both claim it, and the engine
cannot decide which one is entitled to keep it.
