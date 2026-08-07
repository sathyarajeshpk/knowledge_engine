# M8 — Operational Readiness Review

**Milestone:** M8 — the second Domain Pack
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** what changes operationally when the weekly run harvests **more than
one pack**

---

## What this review adds to M7's

M7 established the ten failure scenarios and their classification — graceful
degradation, automatic recovery, manual recovery. Those results still hold and
are not repeated here.

M8 changes one thing about all of them: **the run is now a loop.** Every scenario
M7 answered for "the harvest" now has a second question attached:

> When this fails for pack A, what happens to pack B?

That is the whole of this review, plus the new failure modes M8's own machinery
introduced.

| Class | Meaning |
|---|---|
| **Graceful degradation** | The run completes, does less than it wanted to, and says so. No human needed. |
| **Automatic recovery** | The run is damaged, but the *next* run repairs it with nobody involved. |
| **Manual recovery** | A person must act. The engine's obligation is to make that unmissable and to say what to do. |

---

## Failure isolation between packs

### A pack's `pack.yml` is malformed

**Graceful degradation.** `Pack.find_roots` never parses anything — it only looks
for a directory containing `pack.yml`. Callers load each root themselves, so an
unparseable pack becomes a `PACK005` finding and every other pack is still
validated and harvested.

Tests: `test_a_broken_pack_does_not_hide_another_packs_duplicates`,
`test_one_pack_failing_does_not_stop_another`.

This was a deliberate design choice made in M0 for a single-pack world and is the
reason M8 needed no change here. It is the clearest case in the project of a
"fail fast within a unit, continue across units" rule paying off later.

### A pack's source is dead

**Graceful degradation, per pack and per source.** Source failure isolation was
built in M1 and is unchanged: one dead feed marks that source unhealthy in
`source-health.json` and the run continues. A second pack does not widen the blast
radius, because sources are per-pack.

### A pack's ID registry is corrupt

**Manual recovery — and it stops only that pack.** A corrupt registry stops the
harvest rather than guessing (`test_a_corrupt_registry_stops_the_harvest_rather_than_reusing_ids`),
because a wrong guess mints a duplicate Feature ID, which is unrecoverable.

The M8 question is whether that stops the *run*. It stops the pack. Whether the
loop continues to the next pack after an exception in one is the one place where
per-pack isolation depends on the pipeline driver rather than on a tested
property — see **O-2** below.

### A pack's `seen.json` is corrupt

**Automatic recovery, pack-local.** Degrades to re-checking items already seen,
which the three-layer dedupe then catches. `test_a_corrupt_dedup_cache_does_not_stop_the_harvest`.

---

## New in M8

### The cross-pack resolution store is corrupt or missing

**Automatic recovery.** `Resolutions.load` catches `OSError` and
`JSONDecodeError` and returns an empty set of acknowledgements. The worst case is
that a duplicate somebody already dismissed is shown again — annoying, not
harmful.

This is deliberately the *opposite* policy to the ID registry, and the contrast
is the point: guessing about an acknowledgement costs a repeated review item;
guessing about an ID counter costs a permanently duplicated Feature ID. The
failure policy follows the cost of being wrong, not a house style.

Test: `test_a_corrupt_resolution_store_degrades_rather_than_stopping`.

### A pack is added or removed between runs

**No action needed.** `Pack.discover` globs the directory each run, so a new pack
is picked up with no workflow edit and a removed one simply stops being
harvested. Its knowledge remains on disk, which is correct — nothing is deleted.

Cross-pack duplicates involving a removed pack stop being reported, and the
acknowledgements for them stay in `state/cross-pack.json` harmlessly.

### A symlink appears inside a pack

**Prevented, not recovered from.** `SEC001` (ERROR) fails CI on the pull request.
`Pack.find_roots` refuses a symlinked pack root, and `store.object_dir` refuses a
symlinked knowledge tree at write time even where nothing was validated.

### A pack names a non-web source URL

**Prevented.** `SEC002` (ERROR) fails CI. Before this, the failure would have
occurred at 03:00 on Sunday inside the process holding the write token — the one
place it must not first be discovered.

---

## Operational findings

### O-1 (carried from M7, unchanged)

Minting persists the registry before the object is written, so a crash between
the two leaves an ID gap rather than a dangling ID — the safe direction, and
tested (`test_a_crash_mid_harvest_leaves_an_id_gap_not_a_dangling_id`). The
`ke repair --registry` command to reconcile a gap is still M9 work (TD-10).

### O-2 — One pack could take the whole run down · **new** · **fixed**

Failure isolation was well tested at the level of validation (`find_roots` +
per-root loading) and within a pack (sources, state files). The harvest *driver*
had no test: **if `harvest_pack(A)` raises, does the run still harvest B?**

Writing that test first meant looking at the loop, and the answer was **no**.

```python
except LockError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 2                      # ← the remaining packs are never touched
```

`LockError` was the only exception caught, and it returned immediately. Anything
else `harvest_pack` raised propagated and killed the run outright. So:

* A stuck lock on `microsoft-fabric` silently cost `azure` its entire week.
* A corrupt ID registry in the first pack — which correctly stops *that* pack, by
  design, because guessing mints a duplicate ID — stopped every other pack too.

The irony worth recording: the one failure mode the author had explicitly thought
about was also the one that cost every other pack its run.

**Invisible with one pack**, where "abort the pack" and "abort the run" are the
same thing. This is the clearest example in the milestone of a defect that
existed for eight milestones and could not be seen until there were two of
something.

**Fixed.** The loop now catches `Exception` per pack, prints which pack failed and
why, records it, continues, and restates the failed packs on stderr at the end —
because the per-pack error scrolls past several screens of successful output, and
a run that harvested three packs and dropped one must not read as clean to
somebody skimming a workflow log. The exit code is still non-zero, so a failure is
never reported as success; it just no longer costs the packs that were fine.

`Exception`, not `BaseException`: Ctrl-C and `SystemExit` must still stop
everything, and a test pins that.

Four tests, all mutation-verified: reverting to the original `except LockError:
return 2` fails three of them, and the fourth (KeyboardInterrupt) is the one that
must keep passing.

### O-3 — Cross-pack detection sees the other packs one run stale · **new**

The pipeline harvests and indexes each pack in turn. When pack 1 rebuilds its
index, packs 2..N have not yet harvested this week, so pack 1's cross-pack
duplicate list is computed against last week's version of every other pack.

**Consequence:** a duplicate introduced this week is reported next week, for every
pack except the last one processed.

This is not a correctness violation — nothing is lost, nothing is wrongly merged,
and detection remains order-independent for a given on-disk state — but it makes
the review queue lag by one run. Hoisting the scan to run once, after all packs
have harvested, fixes this and the O(packs²) performance finding together. That
is an architecture decision and is recommended for M9 rather than made here.

### O-4 — Actions minutes at multiple packs · informational

Measured: 2,000 objects across 10 packs take ~124 s to harvest and ~82 s to index.
The real repository (422 objects, 2 packs) is far below that. Nine packs at
current sizes remain a small fraction of the 2,000 min/month budget; the concern
is the growth *rate* with pack count (O(packs²) index rebuild), not today's cost.

---

## Recovery quick reference

| Situation | Class | Action |
|---|---|---|
| One pack's `pack.yml` unparseable | Graceful | Fix the YAML; other packs unaffected |
| One pack's source dead | Graceful | None; watch `source-health.json` for repeats |
| One pack's `seen.json` corrupt | Automatic | None |
| One pack's ID registry corrupt | **Manual** | Restore from git history; do not hand-edit counters |
| `state/cross-pack.json` corrupt | Automatic | None; previously-dismissed duplicates reappear once |
| Symlink in a pack | Prevented | CI red on SEC001; remove the link |
| Non-web source URL | Prevented | CI red on SEC002; use https |
| Duplicate reported that is not one | By design | `ke review resolve <id>+<id>` |

---

## Assessment

Multi-pack operation did not degrade operational readiness, and in two places
improved it — SEC001 and SEC002 moved failures that would have happened at 03:00
on Sunday into a red build on a Tuesday afternoon.

The open item worth carrying is **O-2**: per-pack isolation in the harvest loop is
believed-good rather than demonstrated. M8's recurring lesson applies to it
directly — the guard being real and the path to it being real are separate facts,
and only one of them has been checked here.
