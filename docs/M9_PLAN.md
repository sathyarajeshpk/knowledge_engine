# M9 — Implementation Plan (for approval)

**Status:** **APPROVED 2026-08-07.** Decisions recorded in §0.
**Baseline:** v0.9.0, merged as `8dd189f`. 2 packs, 431 knowledge objects, 671 tests.
**Preceded by:** M8 — the second Domain Pack.

Every number below is labelled **[measured]** or **[estimate]**. Measured figures
come from `docs/reviews/M8_PERFORMANCE_REVIEW.md`, `tools/measure_performance.py`,
or a query run against the merged repository today.

---

## 0. Approved decisions

| # | Decision |
|---|---|
| **D1** | **TD-15/TD-16 approved.** M9-1 benchmarking first, then architectural work. Thresholds stay fixed before implementation; if the hypothesis is disproved, take the fallback rather than forcing the original design. |
| **D2** | **REV002: fix detection before grandfathering.** A temporary rise from ~6 to ~35 is accepted — *"Accuracy is more important than artificially low numbers."* Baseline and `--strict` come only after detection is correct. |
| **D3** | **`ke migrate` is DEFERRED, not descoped-by-omission.** No migration framework will be built for a hypothetical schema. It lands when a real schema evolution requires it. Recorded as deferred scope. |
| **D4** | **Incremental delivery is a requirement.** No single large architectural PR. Each major step must be independently mergeable and validated, with measurable acceptance criteria. |

Work-item numbers are unchanged from the approved plan. **M9-5 is retained as a
row and marked deferred** rather than deleted, so this document still maps onto
what was approved.

---

## 1. Objectives and success criteria

M9 is a **hardening and decision** milestone. It adds little user-facing
capability; it makes the engine safe to extend and safe to trust.

| Objective | Success criterion |
|---|---|
| **O1.** Resolve the O(packs²) index rebuild | An architecture decision is made **on evidence**, implemented, and the multi-pack benchmark shows the agreed improvement — *or* the hypothesis is rejected on evidence and a fallback is adopted |
| **O2.** Make `--strict` enforceable in CI | CI runs `ke validate --strict` and is green, with no historical knowledge rewritten |
| **O3.** ~~Schema migration~~ | **DEFERRED (D3)** — no migration framework for a hypothetical schema |
| **O4.** Recovery is documented | `docs/RUNBOOK.md` covers every **manual recovery** case named in the M7 and M8 readiness reviews |
| **O5.** The engine is provably extractable | `docs/SPLITTING-REPOS.md`, plus a test asserting zero hard-coded pack paths in `engine/` |

**The stated objective, which governs the rest:** M9 is not here to prove the
existing idea is right. Rejecting the TD-15 hypothesis on evidence is a
**successful** outcome, not a failed milestone.

---

## 2. Scope and non-goals

**In scope:** O1–O5 above, plus TD-10 (`ke repair --registry`), TD-12 (rename
`load_existing_objects`), TD-17 (pack rule sanity reporting), TD-18 (TD
renumbering).

**Explicit non-goals:**

- **No third Domain Pack.** M9 is the milestone that makes pack five cheap; adding
  pack three first would bank the cost we are trying to remove.
- **No AI anywhere in the pipeline.** Unchanged (ADR-0040).
- **No incremental index rebuild.** Full rebuild is why indexes cannot drift. Out
  of scope even though it would help performance.
- **No search index.** ADR-0041 stands; at 2.88 ms/object **[measured]** the
  problem does not exist yet.
- **No repository-size work.** The 1.4 GB-at-100,000-objects projection
  **[estimate]** is real and distant; we are at 431 objects.
- **TD-8 (pin Actions to commit SHAs) is scoped in but may not be completable** —
  it needs repository access no session so far has had. Carried openly for a third
  milestone rather than silently.
- **`ke migrate` — DEFERRED (D3).** Building a migration framework with no real
  migration to run means it would be exercised for the first time on live
  knowledge. It lands when a genuine schema change needs it. Deferred scope,
  recorded, not forgotten.

---

## 3. Decision gate A — TD-15 / TD-16, cross-pack detection

This is the milestone's one genuine architecture decision.

### The current design

`harvest_pack(pack)` runs an 11-stage pipeline per pack, of which stage 8 is
`rebuild_indexes`. Index rebuild writes `review-queue.md`; the review queue
includes cross-pack duplicates; `cross_pack_tasks(pack)` needs *every* pack to
know what is cross-pack, so it discovers and reads all of them. Once per pack.

The CLI loops packs and calls `harvest_pack` on each. So an N-pack repository
performs **N full-repository reads per run**.

### What we know today **[measured]**

| Fact | Value | Source |
|---|---|---|
| Full-pack reads during index rebuild | exactly `packs²` (was `2×packs²` before the M8 fix) | Instrumented call counting, M8 P-1 |
| Index rebuild, 2,000 objects in 1 pack | 22.73 s | M8 benchmark |
| Index rebuild, same 2,000 objects across 10 packs | 81.98 s — **3.6×** for identical knowledge | M8 benchmark |
| Index rebuild, 8 packs × 100 objects | 26.92 s (128 reads → 64 after the M8 fix) | M8 benchmark |
| Index rebuild per object, single pack | 11.4 ms | M8 benchmark |
| Detection is order-independent for a given on-disk state | asserted by 5 tests | `test_crosspack.py` |

We also know **[measured, by reading the code]** that each pack indexes before the
later packs have harvested, so every pack but the last computes its cross-pack
list against last week's version of the others — TD-16.

### What we believe — **hypothesis, not measured**

> **H-A:** Computing the cross-pack duplicate set **once per run**, after all packs
> have harvested, and passing it into each pack's index rebuild reduces index
> rebuild from O(packs²) to O(packs) in full-pack reads, and simultaneously
> eliminates the one-run staleness.

The reasoning is that `find_duplicates` is a pure function of on-disk state, so
computing it N times per run is N−1 redundant computations. **This has not been
measured.** The M8 lesson applies directly: the first benchmark ran clean, passed
its own abort guard, and was wrong by 4–5×.

### Evidence required — thresholds fixed now, before any data exists

Benchmark shapes: 1, 2, 4, 8, 10 packs × 200 objects, plus 10 packs × 1,000
objects. Measure full-pack read counts (deterministic, not timing-dependent) **and**
wall-clock index rebuild.

| Result | Verdict |
|---|---|
| Read count becomes linear in packs (≤ `2×packs`) **and** 10-pack index rebuild improves **≥ 50%** (81.98 s → ≤ 41 s) | **PROCEED** |
| Read count linear but wall-clock improves **< 25%** | **REVISE** — the reads were not the cost; profile before continuing |
| Read count not linear, or any correctness/order-independence test fails | **ABANDON** — go to fallback |

The 50% threshold is set now so it cannot move after the numbers arrive. Read
count is the primary signal because it is deterministic; wall-clock is the
confirmation that it mattered.

### The abandon branch — a real fallback, not a courtesy

**Fallback F1: run-scoped memoisation.** Cache `find_duplicates` on a run-scoped
object passed through the pipeline context, without moving when detection runs.
Reduces reads from `packs²` to `packs` **[hypothesis]** while leaving stage order
untouched — so it fixes TD-15 and **not** TD-16. Smaller blast radius, keeps the
existing pipeline shape, and TD-16 stays open as a known limitation.

**Fallback F2: drop cross-pack items from index rebuild entirely.** `review-queue.md`
stops listing cross-pack duplicates; they surface only through `ke review --kind
cross-pack`, which computes on demand. Takes per-run reads to **zero**, at the cost
of the weekly digest no longer surfacing cross-pack items — a product regression
that needs your call, which is why it is the second fallback and not the first.

Both are smaller than the proposed change. If H-A is rejected, F1 is the default.

### Impact if H-A proceeds

- **Correctness:** neutral-to-better. Detection stays order-independent (same pure
  function, same sorted inputs); TD-16 staleness is removed. Every existing
  `test_crosspack.py` assertion must still pass unchanged — that is the guard.
- **Performance:** the point. See thresholds.
- **Complexity:** slightly worse. Cross-pack results must be threaded from the
  harvest driver into per-pack index rebuild, adding a parameter to a path that is
  currently self-contained. Honest cost.
- **Future pack scalability:** the whole justification. Cost per additional pack
  becomes constant rather than growing.
- **Migration/compatibility:** none. No schema change, no stored-file change, no
  Feature ID change, no CLI contract change. Output must be byte-identical
  (ADR-0022) — which is itself a test.
- **Risks and rollback:** the change is confined to the harvest driver, `reviewq`
  and `indexer`. Rollback is a revert; nothing on disk changes format, so a revert
  needs no data repair. This is the main reason to do it before more packs exist.

### Why now, before more packs

At 2 packs the cost is 4 reads and immeasurable — **doing this now is not urgent
for today's performance.** It is urgent because the change is *cheap now and
expensive later*: it is a revert-safe refactor at 2 packs, and a refactor across
nine independently-owned pack configurations at 9. The roadmap has nine packs.

---

## 4. Decision gate B — REV002 and `--strict`

**A finding from today that changes this item, and it is the reason `--strict`
should not simply be switched on.**

### What we know today **[measured, run against merged `main`]**

| Query | Result |
|---|---|
| REV002 warnings at v0.8.0 (pre-merge branch) | **35** |
| REV002 warnings today, same check, after one weekly harvest | **6** |
| Objects with a run of ≥3 identical consecutive revisions anywhere in the chain | **35** |
| Longest identical run observed | **10** |

Nothing was fixed. **29 warnings disappeared because the check stopped seeing
them.** REV002 requires the *entire* post-initial revision chain to be uniform
(`len(set(changes)) == 1`). `MSF-2026-05-002` carries ten identical
`(date_confidence, date_precision, published_date)` revisions followed by one
genuine `(content_hash, title)` edit from the 2026-08-02 harvest — and now reports
clean. The pathology is untouched in the file.

Two consequences:

1. **REV002 under-detects.** One genuine edit after a flip-flop run masks it
   permanently. A *future* flip-flop would be hidden the same way — this is not
   only a historical-data issue.
2. **A grandfather list would rot.** Keyed on object ID, it would have been 83%
   stale within one week.

### Hypothesis

> **H-B:** Changing REV002 to detect a *run* of ≥3 identical consecutive revisions
> anywhere in the chain, rather than whole-chain uniformity, restores detection to
> 35 objects **[measured: the sliding-window query returns exactly 35]** and makes
> the count stable against later genuine edits.

The 35 figure is measured. That the new definition is *stable* over time is
**[estimate]** — it follows from the definition being append-insensitive, but no
future harvest has tested it.

### Recommended sequence — detection first, then grandfathering

Grandfathering 6 warnings and enabling `--strict` today would lock in a check that
is silently missing 29 objects with the identical defect. So:

1. **Fix the detection** (H-B). Expect the count to *rise* from 6 to ~35. A rising
   warning count is the correct outcome here.
2. **Then grandfather**, using a **content-derived baseline, not an ID list**:
   record the flip-flop runs that exist as of a named commit in
   `state/known-history-defects.json`, keyed on `(feature_id, revision_range,
   changed_fields)`. A run matching a baseline entry is downgraded to INFO; any
   *new* run is a WARNING and fails `--strict`.
3. **Then enable `--strict`** in CI.

This satisfies "no history rewritten" (CLAUDE.md): nothing in any `metadata.yaml`
is edited. The baseline is a separate, additive state file recording a decision —
the same pattern as `state/cross-pack.json` (ADR-0044).

### Alternatives rejected

- **Rewrite the 35 objects' revision histories.** Forbidden by CLAUDE.md, and it
  would destroy the evidence that the M3 bug happened.
- **Downgrade REV002 to INFO permanently.** Removes the check's ability to catch a
  future regression — the M3 bug is exactly what it exists to detect.
- **Grandfather by object ID.** Measured to rot: 83% stale in one week.
- **Enable `--strict` now against the 6.** Cheapest today, and it would enshrine a
  check we now know is under-detecting.

### Decision gate B

| Result | Verdict |
|---|---|
| Sliding-window detection finds ≥ 35 and all existing REV002 tests still pass | **PROCEED** to baseline + `--strict` |
| It finds substantially more than 35 (i.e. it flags healthy objects) | **REVISE** the window definition before baselining |
| No stable definition can separate historical residue from live regressions | **ABANDON** — keep REV002 at WARNING, enable `--strict` with REV002 explicitly excluded, and record why |

---

## 5. Milestone breakdown, order and dependencies

| # | Work | Depends on | Gate |
|---|---|---|---|
| **M9-1** | **Benchmark harness for the cross-pack decision.** Extend `tools/measure_performance.py` to count full-pack reads and cover 1/2/4/8/10-pack shapes. Establish the pre-change baseline. | — | — |
| **M9-2** | Implement H-A (hoist detection) **or** fallback F1 | M9-1 | **Gate A** |
| **M9-3** | REV002 sliding-window detection | — | **Gate B** |
| **M9-4** | Baseline file + INFO downgrade; enable `ke validate --strict` in CI | M9-3 | — |
| ~~**M9-5**~~ | ~~`ke migrate`~~ — **DEFERRED (D3).** Lands when a real schema evolution requires it | — | — |
| **M9-6** | TD-10 `ke repair --registry`; TD-12 rename; TD-17 first-harvest rule sanity report | — | — |
| **M9-7** | `docs/RUNBOOK.md`, `docs/SPLITTING-REPOS.md`, extraction test | M9-2 | — |
| **M9-8** | Milestone deliverables: reviews, ADRs, release notes v0.10.0, TD renumbering (TD-18) | all | — |

**M9-1 is deliberately first.** It is the benchmarking task you asked to be the
first implementation work, and it establishes the baseline *before* any
architectural code exists to bias it.

**M9-3 is independent of the gate-A outcome** and can proceed if M9-2 stalls.

### Delivery shape (D4)

Every row above ships as its **own PR**, independently mergeable and green on its
own, with its own acceptance criteria. Explicitly: M9-1 (measurement only, no
behaviour change) merges before any architectural code exists; M9-3 (detection)
merges before M9-4 (baseline + `--strict`), so the warning count rises in a
reviewable step of its own rather than buried inside a larger change.

---

## 6. Risks and mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| **The benchmark is wrong again** | The architecture decision rests on a false number | M9-1 counts **full-pack reads** (deterministic) as the primary signal, with wall-clock only as confirmation. M8's failure was timing-specific |
| **H-A is confirmed by a benchmark that measures the wrong thing** | Proceeding on a real-looking but irrelevant improvement | Falsification threshold covers both: linear reads *and* ≥50% wall-clock. Linear reads with flat wall-clock triggers REVISE, not PROCEED |
| ~~`ke migrate` designed against a hypothetical schema~~ | — | **Retired by D3:** deferred rather than built speculatively |
| **Sliding-window REV002 over-fires** | `--strict` blocked, or the check is disabled to unblock CI | Gate B's REVISE branch exists exactly for this; the count is checkable against the 35 before anything is enforced |
| **Hoisting breaks order-independence** | Silent violation of ADR-0044 | All 23 `test_crosspack.py` tests must pass unchanged; byte-identical output is itself asserted (ADR-0022) |
| **TD-8 blocked a third time** | Unpinned Actions remain a supply-chain surface | Named in scope; if still blocked, it is reported as blocked with the specific access needed, not carried silently |
| **Scope creep** | M9 sprawls | O1 and O2 are the milestone. M9-6 and M9-7 are droppable to M10 if the gates consume the budget |

---

## 7. Acceptance criteria and validation plan

**Acceptance:**

1. Gate A resolved — either H-A implemented and thresholds met, or a fallback
   adopted with the rejecting evidence recorded in an ADR.
2. `ke validate --strict` green in CI, with **zero** `metadata.yaml` files
   modified to achieve it (verifiable: `git diff` over `domain-packs/**/metadata.yaml`
   across the milestone shows no history edits).
4. `docs/RUNBOOK.md` covers every manual-recovery row in the M7/M8 readiness
   reviews.
5. Test count up, no test deleted to make something pass.

**Validation, beyond "tests pass":**

- **Mutation-verify every new guard.** It corrected four claims in M8.
- **Installation-level tests** for any new user-facing surface (`ke repair`) — the
  M8 lesson that a guard never invoked and a guard that does not exist are the
  same guard.
- **Read the produced knowledge**, not the exit code. After any change that
  rewrites objects, diff a sample by hand and confirm user-owned fields are
  byte-identical.
- **Re-run the M8 benchmark** at the end and publish before/after in the M9
  performance review, whichever way gate A went.

---

## 8. Status

Approved 2026-08-07 (§0). Proceeding with M9-1.
