# M8 — Performance Review

**Milestone:** M8 — the second Domain Pack
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** harvest time, memory usage, index rebuild complexity, and scaling
projections to 10,000 objects, 100,000 objects and 10+ Domain Packs

---

## What this review asks

Not *"is it fast?"* — at 422 objects everything is fast. The question is:

> **Which term wins first, and at what size?**

A system that is linear in one place and quadratic in another is linear right up
until it is not. This review measures each operation separately, identifies the
growth term of each, and says where the curves cross.

Every number below comes from `tools/measure_performance.py`, which is committed
and reproducible:

```
python tools/measure_performance.py          # up to 2,000 objects, 10 packs
python tools/measure_performance.py --big    # adds the 10,000-object shape
```

Network time is excluded throughout. Discovery is dominated by how fast
Microsoft's servers respond, which is not a property of this engine and varies
by an order of magnitude between runs. Everything measured here is local work.

---

## P-0 — A methodological finding, first, because it invalidated the first run

**The first version of the benchmark was wrong by a factor of 4–5, and nothing
about the run looked wrong.**

`measure()` timed every operation with `tracemalloc` running, because doing
timing and memory in one pass looked like one fewer pass. `tracemalloc` hooks
the allocator, and this workload allocates constantly.

| Operation | Reported (tracemalloc on) | Actual (off) | Inflation |
|---|---|---|---|
| Harvest, 100 objects | 17.02 s | 3.19 s | 5.3× |
| Load, 100 objects | 1.090 s | 0.254 s | 4.3× |

The run completed cleanly. The abort guard confirmed all 100 objects were on
disk. The numbers were internally consistent across shapes — consistently
inflated. Had it not been sanity-checked against a plausible per-object cost
(13 ms to parse one small YAML file is not believable), every projection in this
document would have been 4–5× too pessimistic, and the recommendations would
have followed the wrong bottleneck.

This is the same lesson as M6 and M7 in a new costume: **a successful execution
is not a correct output.** It applies to measurements as much as to knowledge.

Timings are now taken with `tracemalloc` off; a separate pass measures memory
and throws its timings away.

---

## Measurements

All figures below are **after** the P-1 fix described later. Pre-fix numbers
appear only where they show what the fix bought.

Single pack, varying size:

| Objects | Packs | Harvest | Load | Search | Index | Coverage | Cross-pack | Peak MB | Repo MB | Files |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 1 | 3.00 s | 0.27 s | 0.28 s | 1.09 s | 0.28 s | 0.27 s | 0.6 | 0.3 | 211 |
| 500 | 1 | 15.19 s | 1.37 s | 1.46 s | 5.71 s | 1.35 s | 1.30 s | 2.5 | 1.5 | 1,011 |
| 1,000 | 1 | 31.52 s | 2.88 s | 3.09 s | 11.51 s | 2.85 s | 2.94 s | 4.8 | 3.0 | 2,011 |
| 2,000 | 1 | 64.60 s | 5.96 s | 5.76 s | 22.73 s | 5.18 s | 5.13 s | 9.5 | 6.0 | 4,011 |

Harvest per object is 30.0, 30.4, 31.5, 32.3 ms across those four shapes — very
nearly flat, with the mild upward drift being the quadratic dedupe term of P-2
starting to show. Load, search, coverage and cross-pack are all flat per object.

Same object count, spread across packs:

| Objects | Packs | Harvest | Index | Note |
|---:|---:|---:|---:|---|
| 2,000 | 1 | 64.60 s | 22.73 s | baseline |
| 1,000 | 4 | 45.09 s | 24.77 s | index exceeds the 2,000-object single-pack figure on **half** the objects |
| 2,000 | 10 | 123.70 s | 81.98 s | **3.6× the single-pack index cost for identical object count** |

Per-object constants, from the 2,000-object single-pack shape:

| Operation | Cost |
|---|---|
| Harvest (discover → dedupe → mint → store → index → digest) | 32.3 ms/object |
| Load all objects | 2.98 ms/object |
| Search (full scan, ADR-0041) | 2.88 ms/object |
| Index rebuild | 11.4 ms/object |
| Peak traced memory | 4.8 KB/object |
| Repository size, synthetic | 3.0 KB/object, 2 files/object |
| Repository size, **real** (fabric 222 obj / azure 200 obj) | **≈14 KB/object** |

The real-repository figure is 4.6× the synthetic one — real summaries, revision
histories and provenance are larger than generated fixtures. **Synthetic numbers
are used for CPU projections and the real number for storage projections.** Using
the synthetic size for storage would understate the repository by a factor of
five.

---

## P-1 — Index rebuild is O(packs²). This is the first thing that breaks.

**Severity: high at 10+ packs. Measured, not projected.**

Look again at the multi-pack rows: 2,000 objects in one pack rebuild indexes in
22.73 s. The *same 2,000 objects* across ten packs take 81.98 s. Object count is
identical; only pack count changed. (Before the fix below: 34.21 s and 146.91 s.)

The mechanism, confirmed by instrumenting `load_objects_with_dirs` and counting
calls rather than by reading the code:

| Packs | Full-pack reads during index rebuild | Index time (100 obj/pack) |
|---:|---:|---:|
| 1 | 0 | 1.03 s |
| 2 | 4 | 3.52 s |
| 4 | 16 | 8.21 s |
| 8 | 64 | 26.92 s |

`packs²` exactly. The cause is a chain that is individually reasonable at every
link: index rebuild writes `review-queue.md` → the review queue includes
cross-pack duplicates → `cross_pack_tasks(pack)` needs *all* packs to know what
is cross-pack → so it discovers and reads every pack. Once per pack. So an
N-pack repository performs N full-repository reads per harvest.

Nothing here is wrong. Every component does the least work it can given what it
is asked for. The cost is structural: **a per-pack operation that depends on
global state is quadratic in packs, and cross-pack duplicate detection is
inherently global.**

### What was fixed in M8

The measured constant was **2 × packs²**, not `packs²`. `render_report` needed
both the task list and a tally of it, and obtained the tally by calling
`collect()` a second time — re-running every provider, including the cross-pack
one. That factor of two was pure waste with no design implication and is fixed
(`counts()` now accepts the tasks it should count).

| Shape | Before | After |
|---|---:|---:|
| 8 packs × 100 objects, index rebuild | 47.11 s | **26.92 s** |
| 8 packs, full-pack reads during index rebuild | 128 | **64** |
| 10 packs × 200 objects, index rebuild | 146.91 s | **81.98 s** |
| 1 pack × 2,000 objects, index rebuild | 34.21 s | **22.73 s** |

The single-pack row improves too, which was not the goal but follows: `collect()`
was running *every* provider twice, not only the cross-pack one.

### What was deliberately not fixed

The remaining `packs²`. The fix is real and not difficult — compute the
cross-pack duplicate set **once per run** and pass it into each pack's index
rebuild, taking the term from O(packs²) to O(packs) — but it changes *when*
detection runs relative to harvest, and that is an architecture decision rather
than a cleanup. CLAUDE.md's development workflow says to stop and explain rather
than change the agreed architecture mid-implementation.

It also surfaces a question that must be answered first, and that the current
code answers only by accident:

> The pipeline harvests and indexes each pack in turn. When pack 1 rebuilds its
> index, packs 2..N have **not yet harvested this week**. So pack 1's cross-pack
> duplicate list is computed against last week's version of every other pack.

Today's results are therefore one run stale for every pack but the last. This is
not a correctness violation — nothing is lost, nothing is wrongly merged, and
detection remains order-independent for a *given* on-disk state (ADR-0044) — but
it means a duplicate introduced this week is reported next week. Hoisting the
scan to run once, after all packs have harvested, fixes the performance and the
staleness together. **Recommended for M9, as an explicit decision.**

---

## P-2 — Near-duplicate detection is O(new × known)

**Severity: low in steady state. High for a first harvest or backfill.**

`dedupe.classify` layer 3 compares each new item's normalised title against every
known title by Jaccard similarity. Measured in isolation:

| New | Known | Seconds | Comparisons |
|---:|---:|---:|---:|
| 200 | 200 | 0.05 | 40,000 |
| 200 | 1,000 | 0.33 | 200,000 |
| 200 | 5,000 | 1.29 | 1,000,000 |
| 1,000 | 1,000 | 1.33 | 1,000,000 |
| 2,000 | 2,000 | 5.06 | 4,000,000 |
| 20 | 10,000 | 0.23 | 200,000 |
| 20 | 100,000 | 2.32 | 2,000,000 |

≈1.27 µs per comparison.

The distinction that matters:

* **Weekly steady state** is *few new × many known*. Twenty new items against
  100,000 stored is 2.3 seconds — effectively linear in the corpus, and
  irrelevant next to the 37 ms/object write cost of the twenty items.
* **First harvest or backfill** is *many new × many new*. 10,000 items ingested
  at once is ~50 M comparisons ≈ 63 s; 100,000 at once is ~5 × 10⁹ ≈ 1.8 hours.

So the quadratic is real but sits on the path nobody walks weekly. It bites
exactly once per pack, when the pack is seeded — which is survivable, and which
`ke backfill` can chunk by month in any case.

If it ever needs fixing, there is a **provably equivalent** optimisation
available rather than an approximation: Jaccard ≥ threshold > 0 implies at least
one shared token, so an inverted token index restricts the candidate set without
changing a single result. Worth noting that the synthetic titles used here are
near-identical, which is the worst case for candidate-set size — real
announcement titles would prefilter far more aggressively than this benchmark
suggests.

---

## P-3 — The per-object constant is YAML parsing and two-file writes

32.3 ms/object for harvest and 2.98 ms/object to load decompose into PyYAML's
pure-Python `SafeLoader`, `SafeDumper`, and two atomic writes (temp file +
`os.replace`) per object. There is no algorithmic problem here; it is simply what
these operations cost.

Two observations rather than recommendations:

* PyYAML's C loader (`CSafeLoader`, via libyaml) is typically 5–10× faster, but
  it is a **binary dependency**. The engine's entire dependency posture — two
  pure-Python packages, hash-pinned, installable anywhere — is worth more than
  the milliseconds. Not recommended.
* Two files per object is a deliberate schema decision (canonical knowledge
  separate from structured metadata) whose cost is one extra write and one extra
  read per object. It is visible in these numbers and it is paying for something.

---

## Memory

Peak traced allocation is **4.8 KB/object**, flat across every shape measured —
0.6 MB at 100 objects, 9.5 MB at 2,000. The engine loads all of a pack's objects
into memory at once (`load_objects_with_dirs` returns a list, not an iterator),
so this scales linearly with the largest single pack, not with the repository.

Multi-pack runs do **not** multiply it: the 10-pack row peaks at 8.8 MB for 2,000
objects, slightly below the 9.5 MB single-pack figure, because each pack is
loaded and released in turn.

Traced Python allocation understates process RSS (interpreter, imported modules,
and PyYAML's parser state add a roughly constant ~30–40 MB). Projections below
report both.

---

## Scaling projections

Extrapolated from the measured per-object constants. Linear terms scale linearly;
the O(packs²) term is stated separately because it does not.

### 10,000 knowledge objects (one pack)

| Metric | Projection |
|---|---|
| Weekly harvest (≈20 new items) | **~1 s** — 20 × 32 ms of writes, plus a 0.25 s dedupe scan |
| Full re-harvest / seeding from scratch | ~323 s writes + ~63 s dedupe ≈ **6.5 minutes** |
| Index rebuild (every run) | **~114 s** |
| `ke search` | ~29 s |
| Peak traced memory | ~48 MB (~85 MB RSS) |
| Repository size | ~140 MB, 20,000 files |

**Verdict: comfortable.** The weekly run is dominated by the index rebuild, at
~2 minutes against a 2,000 min/month Actions budget (~0.4%).

### 100,000 knowledge objects (one pack)

| Metric | Projection |
|---|---|
| Weekly harvest (≈20 new items) | **~3 s** — new-item write cost does not depend on corpus size; the 2.3 s dedupe scan does |
| Full re-harvest / seeding from scratch | ~54 min writes + ~1.8 h dedupe ≈ **2.7 hours** |
| Index rebuild (every run) | **~19 minutes** |
| `ke search` | ~5 minutes |
| Peak traced memory | ~480 MB (~520 MB RSS) |
| Repository size | **~1.4 GB, 200,000 files** |

**Verdict: the repository breaks before the engine does.** 1.4 GB and 200,000
files is past the point where `git clone` is pleasant and well past GitHub's
recommended repository size. A 19-minute index rebuild every week is 76
min/month — still inside the Actions budget, but it is the whole weekly run.

The binding constraint at this scale is **storage and clone time, not CPU**, and
the answer is not optimisation: it is that a single pack should not hold 100,000
objects. Splitting by domain is the mechanism the architecture already has.

At this size, three things become worth doing, in this order: rebuild indexes
incrementally rather than wholly (currently a deliberate correctness choice —
full rebuild is why indexes cannot drift); make `load_objects_with_dirs` an
iterator so memory stays flat; and only then consider a search index.

### 10+ Domain Packs

This is the projection that changes a recommendation rather than confirming one.

Assume 10 packs × 1,000 objects = 10,000 objects total.

| Metric | Linear expectation | With O(packs²) index rebuild |
|---|---|---|
| Index rebuild | ~114 s | **~410 s (7 min)**, at the measured 10-pack rate |
| Weekly harvest | ~50 s | ~50 s (unaffected) |

Measured directly: 2,000 objects across 10 packs rebuild in 81.98 s versus
22.73 s in one pack — 3.6× for the same knowledge. The penalty grows with pack
count, not with knowledge.

Extending to the roadmap's nine planned packs at 1,000 objects each, index
rebuild becomes the dominant cost of the weekly run — while the actual knowledge
harvested stays trivial. **The engine's weekly cost would be driven by how many
packs exist rather than by how much was learned**, which is the wrong thing for
it to be driven by.

This does not block M8. Two packs is 4 full-pack reads per run and immeasurable.
It is the reason P-1's fix is recommended before the pack count reaches roughly
five.

---

## What is fine and should be left alone

* **Search scans rather than indexes** (ADR-0041). At 2.88 ms/object, a
  10,000-object pack searches in 29 s and a realistic one in under a second. An
  index would be a second source of truth that can drift, bought with complexity,
  to fix a problem that does not exist yet.
* **Full index rebuild every run.** Incremental rebuilds are faster and can
  diverge from the objects they describe. The current cost is 11.4 ms/object and
  the current benefit is that divergence is impossible.
* **Deterministic byte-identical output** (ADR-0022). Sorting costs
  O(n log n) on data that is already small relative to the I/O around it, and it
  is what makes "no diff" mean "no change".
* **Pure-Python dependencies.** Faster alternatives exist for YAML and hashing.
  None is worth a binary dependency in a project whose defining constraint is
  that it runs anywhere for free.

---

## Findings summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| P-0 | Benchmark timings inflated 4–5× by `tracemalloc` | — (method) | **Fixed**; timing and memory now measured separately |
| P-1 | Index rebuild is O(packs²) in full-pack reads | High at 10+ packs | **Halved** (2N² → N²); the N² term written up for an M9 decision |
| P-2 | Near-duplicate detection is O(new × known) | Low steady-state, high on backfill | Accepted; provably-equivalent prefilter documented if needed |
| P-3 | Per-object constant is PyYAML + two-file writes | Low | Accepted deliberately |
| P-4 | Repository reaches ~1.4 GB at 100,000 objects | High, at that scale only | Accepted; splitting packs is the architectural answer |

---

## Recommendations for M9, in priority order

1. **Hoist cross-pack duplicate detection out of per-pack index rebuild.** Fixes
   O(packs²) and the one-run staleness together. Needs an explicit decision about
   pipeline ordering, which is why it was not done unilaterally in M8.
2. **Make `load_objects_with_dirs` an iterator** where callers stream. Cheap,
   and it is what keeps memory flat past 10,000 objects.
3. **Nothing else.** Every other cost measured here is either linear, small, or
   paying for a property worth more than the milliseconds.
