# M2 Architecture Review — The end-to-end pipeline

**Reviewer:** Senior Software Architect
**Date:** 2026-08-01
**Scope:** minting, deduplication, storage, review queue, indexes, `ke harvest`
**Verdict:** **Ready to merge.** The pipeline runs end to end against production
and writes 222 validated knowledge objects. The residual work is scoped, and
none of it blocks M3.

---

## 1. What was built

M1 could look at the internet. **M2 keeps what it finds.**

```
ke harvest
  discover ─▶ dedupe ─▶ gate ─┬─▶ mint ─▶ store ─▶ index ─▶ run log
                              └─▶ queue ─▶ ke review ─▶ (next harvest)
```

| Module | Job |
|---|---|
| `ids.py` | Date-based Feature IDs, per-month counters, consistency-checked registry |
| `dedupe.py` | Three layers: identity, content fingerprint, near-duplicate |
| `store.py` | Object directories, atomic paired writes, deterministic bytes |
| `review.py` | The queue, and the approve/archive path out of it |
| `indexer.py` | Four indexes, fully rebuilt every run |
| `harvest.py` | The pipeline, and the ordering decisions that make it safe |

New commands: `ke harvest`, `ke review`, `ke index`.

## 2. Production results

Run against the live Microsoft sources:

| | |
|---|---|
| Discovered | 344 |
| Minted | **222** |
| Queued for review | 26 |
| Already known (within-run duplicates) | 96 |
| Near-duplicates flagged | 1 |
| Files produced | 452 |
| `ke validate --strict` | **clean** |
| Second harvest | **0 minted, registry byte-identical** |

Every object is a `feature.md` + `metadata.yaml` pair under
`knowledge/YYYY/MM/<FeatureID>-<slug>/`, with four rebuilt indexes and an
appended run log.

## 3. Strengths

**The ordering decisions are the architecture.** Dedupe before minting, because
minting is irreversible and dedupe is the last free check. Registry saved *after*
objects are on disk, so a crash leaves an ID gap — recoverable — rather than a
recorded ID pointing at nothing. Both are documented at the point of decision.

**Failures degrade in the right direction, and the asymmetry is deliberate.** A
damaged `seen.json` is tolerated (re-examining known items is cheap); a damaged
`id-registry.json` or `review-queue.json` is fatal (one reuses IDs, the other
loses human decisions and original discovery dates). Three storage files, three
different failure policies, each chosen rather than inherited.

**Running it found what reading it did not.** Three real bugs surfaced on the
first execution — a half-written object, a registry path mismatch, an unusable
review key. All three were invisible to code review and obvious within one run.
The product-first instruction paid for itself immediately.

**Idempotency is proven, not asserted.** Second harvest: zero minted, registry
byte-identical, file count unchanged. That is the property that makes a weekly
cron safe to leave unattended.

**The queue is visible without running anything.** `indexes/review-queue.md`
renders the 26 held-back items with the reason each was held, in the GitHub UI.
A queue only gets drained if somebody can see it.

## 4. Weaknesses

**W1 — `harvest_pack` is 110 lines and does six things.** It is readable now, but
M3 adds classification and M5 adds the update path. It should be decomposed
before then, not after. *Not blocking; recorded as debt.*

**W2 — `load_existing_objects` reads and parses every object on every run.** At
222 objects that is milliseconds. At 5,000 it is not, and it happens twice
(indexing and validation). The fix is an index cache keyed on file mtime, which
is not worth building until there is a measurable problem.

**W3 — A failed write leaves a gap in the ID sequence.** `mint()` advances the
counter before `write_object` succeeds, so a mid-run failure skips numbers. This
is the *correct* direction (gaps are safe, reuse is not), but it is currently
implicit rather than stated in the ID contract.

**W4 — Near-duplicate detection compares against every known title.** O(n²) over
the run. At 344 items it is invisible; it will not stay invisible.

**W5 — No digest yet.** The run log is a table of counts. A human wanting to know
*what* arrived this week must read the git diff. M6 owns this.

**W6 — 96 of 344 discovered items are within-run duplicates.** That is the source
listing the same feature in several sections, working exactly as intended — but
it means a third of every harvest is wasted parse work. Harmless, worth knowing.

## 5. Findings

### F1 — Half-written objects were possible (HIGH — fixed)

`write_object` wrote `feature.md`, then `metadata.yaml`. A serialisation failure
between them left a directory containing half an object at a permanent-looking
path. **This was not hypothetical: the first real run produced 222 orphaned
`feature.md` files.**

Fixed by rendering both documents before writing either, plus cleanup on a
disk-level failure between the two writes. Pinned by
`test_writing_never_leaves_half_an_object`.

The general lesson, now in the release notes: **an object that is a pair of files
must be written as a unit, or it is not an object.**

### F2 — Registry paths disagreed with the validator (MEDIUM — fixed)

The registry recorded paths relative to the pack root; `ke validate` expects them
relative to `knowledge/`. All 222 objects failed validation. Fixed by using
`KnowledgeObject.knowledge_subpath` — the canonical form the validator itself
checks against.

Cause worth naming: **two independent computations of the same path.** The fix is
not "be careful", it is "have one source of truth".

### F3 — The documented review workflow was impossible (MEDIUM — fixed)

`review-queue.md` prints identity keys with the `sha256:` prefix stripped;
`ke review approve` matched against the full key. Pasting the printed key
failed. Fixed by matching digests without the algorithm prefix.

Found by following the documented workflow rather than by testing the function —
which is the only way this class of bug surfaces.

## 6. Technical debt

| # | Item | Severity | When |
|---|---|---|---|
| TD-1 | Decompose `harvest_pack` | Medium | Before M3 adds classification |
| TD-2 | Cache parsed objects instead of re-reading | Low | When a pack passes ~1,000 objects |
| TD-3 | Near-duplicate check is O(n²) | Low | Same trigger as TD-2 |
| TD-4 | ID sequence gaps are undocumented | Low | Fold into M3's ADR pass |
| TD-5 | `TITLE_NOISE` is vendor-specific and lives in code | Low | When a second vendor arrives (ADR-0030) |
| TD-6 | `models.py` is ~1,470 lines | Medium | When M5 adds the revision path |
| TD-7 | Lifecycle `superseded` vs `status: replaced` naming collision | Low | M5, when supersession is implemented |
| TD-8 | 26 queued items have no triage workflow beyond one-at-a-time approval | Medium | M3 or M6 — bulk operations |

## 7. Risks for M3

| Risk | Severity | Mitigation |
|---|---|---|
| **Classification rewrites 222 existing objects** | High | M3 must use `with_engine_fields`; the ownership registry already refuses user-owned writes, but the *update* path is untested because M2 only ever creates |
| **The 26-item queue is never drained** | Medium | It is visible in `review-queue.md`; M3 should surface the count in any summary it produces |
| **Re-harvest after a source reword** | Medium | Identity holds, but the **update** path does not exist yet — M2 only creates. A changed title today produces nothing, silently. M5 owns this; until then a reworded item is simply not reflected |
| **Pack growth** | Low | 452 files for one pack. Eight packs at this rate is ~3,600 files — fine for Git, worth watching |
| **Inferred dates cluster IDs** | Low | 107 objects landed in `2026-08` because their sources carry no dates. Correct per ADR-0005, but it makes the month segment less informative than it looks |

## 8. Assessment

M2 did what it was asked: it delivered a working product rather than a better
architecture. The pipeline runs, the output validates, the second run is a no-op,
and the knowledge is in the repository as ordinary Markdown that outlives this
engine.

The three bugs it surfaced are the argument for the product-first shift. None
would have been found by more design; all three were found within minutes of
running the thing against real data.

**Recommend merge.**
