# M3 Architecture Review — The update path

**Reviewer:** Senior Software Architect
**Date:** 2026-08-01
**Scope:** regression suite, `revisions.py`, the update path, preservation and idempotency
**Verdict:** **Ready to merge.** The safety property the project was designed
around is now executable and proven against production data.

---

## 1. What was built

M2 could create. **M3 can revisit.**

Regression tests came first, as instructed — one per bug that reached a running
system, each verified to fail against the old behaviour before being committed.

| Module | Job |
|---|---|
| `revisions.py` | Detect real change; append revisions only when material |
| `store.load_object` / `update_object` | Read an object; rewrite only when bytes differ |
| `harvest._update_existing` | Route a known item to update instead of skipping it |
| `test_regressions.py` | 11 tests, one per shipped defect |
| `test_update_path.py` | 21 tests covering the eight stated objectives |

## 2. The eight objectives

| # | Objective | Evidence |
|---|---|---|
| 1 | Detect existing objects | 302 of 344 matched and routed to update |
| 2 | Update only engine-owned fields | `UPDATABLE_FIELDS ⊂ ENGINE_OWNED_FIELDS`, asserted at import |
| 3 | Preserve user-owned fields exactly | **Proven against live data** — see §3 |
| 4 | Detect real revisions | Reflowed whitespace produces none; a retitle produces one |
| 5 | Indexes and registry correct | Registry unchanged on update; indexes reflect new titles |
| 6 | Idempotency across runs | 5 consecutive runs, byte-identical |
| 7 | No permanent ID ever changes | Asserted across 4 successive rewordings |
| 8 | Only meaningful Git diffs | A no-op harvest changes **exactly one file** |

## 3. The proof that matters

A live knowledge object was hand-edited with learning state, notes,
relationships, a locked `difficulty` and a hand-written artifact. Its stored
title and content hash were then corrupted to force a genuine update, and
`ke harvest` was run against the real Microsoft sources.

```
Updated 1 existing object(s): MSF-2025-11-001 (content_hash, title)

engine-owned DID update      title: 'STALE TITLE…' → 'Cosmos DB in Microsoft Fabric (GA)'
                             revisions: 1 → 2  ("Source retitled and rewrote the summary")
                             id UNCHANGED: MSF-2025-11-001

user-owned SURVIVED          learning_status · notes · prerequisites
                             related_topics · overrides · difficulty
                             artifacts/tutorial.md intact
```

This is the property the whole project was designed around, and it now has
evidence rather than an argument.

## 4. Strengths

**Regression tests record the blind spot, not just the bug.** Each names why the
existing suite could not see it — the test clock did not have the clock bug,
storage and validation were never run in sequence, `approve()` was tested with
the key the test itself held. That is what makes them survive a future refactor.

**Every guard was verified by breaking it.** Preservation, paired writes, YAML
anchors, review keys, registry paths, the flip-flop — each was reverted, watched
to fail, and restored. A test that has never failed is a claim, not a guard.

**The allow-list fails safe.** `UPDATABLE_FIELDS` names what may change rather
than what may not, so a new field is frozen by default. Two import-time
assertions make a contradiction impossible to ship.

**The pipeline found its own bug again.** 70 phantom "updates" on an unchanged
run — invisible in tests, obvious in one production run.

## 5. Weaknesses

**W1 — `harvest_pack` now does seven things** (TD-1 escalates from Medium to
**High**). It gained an update stage and is at the point where reading it
requires holding too much at once. Should be decomposed before M4.

**W2 — Two lists to maintain.** A new engine-owned field that *should* refresh
must be added to `UPDATABLE_FIELDS` by hand; forgetting means it silently never
updates. The assertions catch contradictions, not omissions.

**W3 — `provenance` is frozen at first discovery.** An object found via HTML and
later seen only in Markdown still reports HTML provenance. Correct for "how this
came to exist", potentially confusing for "where this is now".

**W4 — No deletion or retirement path.** A feature that vanishes from its source
keeps its object, with nothing marking it. Follows "never delete", but the pack
will accumulate silently-retired items.

**W5 — The 26-item review queue is still undrained**, and M3 added no bulk
triage (TD-8 unchanged).

## 6. Findings

### F1 — Duplicate sightings flipped objects every harvest (HIGH — fixed)

The same feature is legitimately listed by two sources with slightly different
metadata. Both share an identity, both matched the stored object, and **both ran
the update** — writing it twice per harvest and leaving it alternating between
the two renderings forever.

Measured symptom: **70 "updates" on a run that changed nothing**, and a
permanently dirty git diff. A weekly diff that is always dirty is the same as
having no diff at all.

Fixed by handling one sighting per identity, taken from an already-deterministic
order. Pinned by `test_two_sources_reporting_one_feature_do_not_flip_the_object`,
verified to fail without the fix.

### F2 — A guard that could not be reached (MEDIUM — fixed)

`update_object`'s byte-comparison is unreachable from the pipeline:
`detect_changes` short-circuits first. Removing it entirely left every test
green, which means the end-to-end test claiming to cover it was covering
something else.

It now has a direct unit test, and the end-to-end test says plainly which guard
it actually exercises. **Worth generalising: a test that still passes when you
delete the code it names is not testing that code.**

## 7. Technical debt

| # | Item | Severity | When |
|---|---|---|---|
| TD-1 | `harvest_pack` does seven things | **High** (was Medium) | Before M4 |
| TD-2 | Every object re-parsed each run | Low | ~1,000 objects |
| TD-3 | Near-duplicate check is O(n²) | Low | same trigger |
| TD-6 | `models.py` ~1,470 lines | Medium | M5 |
| TD-8 | No bulk queue triage | Medium | M6 |
| TD-9 | `UPDATABLE_FIELDS` maintained by hand | Low | If a field is ever forgotten |
| TD-10 | No retirement path for vanished features | Medium | M5, with supersession |
| TD-11 | `provenance` frozen at first discovery | Low | If it causes real confusion |

## 8. Risks for M4

| Risk | Severity | Mitigation |
|---|---|---|
| **Classification writes engine-*proposed* fields** — a class the update path has never touched | **High** | `with_engine_fields` already honours `overrides`, and `test_a_locked_proposed_field_is_not_overwritten` covers it; but M4 is the first code to write that class in anger |
| `harvest_pack` complexity compounds | High | Decompose first (TD-1) |
| Reclassification churns the weekly diff | Medium | Classification must be deterministic, or every rule tweak rewrites 222 objects |
| Queue still undrained | Medium | Surface the count wherever M4 reports |

## 9. Assessment

M3 did what it was asked and nothing else: it exercised the update path rather
than expanding functionality. The regression tests landed first, every guard was
verified by breaking it, and the one property the project cannot compromise on —
that automation never destroys the user's own work — now has production evidence.

**Recommend merge.**
