# M4 Architecture Review — Orchestration and classification

**Reviewer:** Senior Software Architect
**Date:** 2026-08-01
**Scope:** pipeline refactor (TD-1), `classify.py`, engine-proposed fields, overrides
**Verdict:** **Ready to merge.** TD-1 is paid down, classification is
deterministic and idempotent against production, and the two bugs it produced
are fixed with regression tests verified to bite.

---

## 1. What was built

TD-1 first, as instructed, then classification on top of the result.

| Module | Job |
|---|---|
| `pipeline.py` | The harvest as an ordered tuple of stages |
| `report.py` | `HarvestReport`, extracted so stages and CLI need not depend on each other |
| `harvest.py` | An 89-line facade preserving the public surface |
| `classify.py` | Deterministic proposals from `pack.yml` rules |
| `pack.yml` | 51 rules across six axes — data, not code |

## 2. The seven priorities

| # | Priority | Result |
|---|---|---|
| 1 | Refactor orchestration | `harvest_pack` 130 → 89 lines; 9 named stages |
| 2 | Deterministic classification | Same object + rules → same result, asserted |
| 3 | Engine-proposed fields | 222 classified; tier spread 37/145/40 |
| 4 | Override handling | Locked fields never proposed over |
| 5 | Index rebuilding | Runs after classification, reflects it |
| 6 | Classification idempotency | 222 on run 1, **0** on runs 2–4 |
| 7 | Regression tests before fixes | Both M4 bugs pinned and verified |

## 3. The refactor

```python
STAGES = (discover, load_state, deduplicate, update_existing,
          gate_and_mint, classify_objects, persist_state,
          rebuild_indexes, append_run_log)
```

The pipeline's shape is now readable in one screen, and adding a stage is one
entry plus one function. Two rules hold it together: a stage **mutates the
context** rather than returning a new one (so no caller can drop a result), and a
stage **raising is a bug rather than an expected failure** (per-item errors are
caught inside the stage that owns them, so an escaping exception means the
context is half-built and continuing is worse than stopping).

Crucially, each stage carries its own ordering constraint in its docstring —
why updates precede minting, why state persists after objects — rather than
those reasons living in a comment far from the code they govern.

**Classification slotted in as one line.** That was the point of the refactor,
and it was validated immediately by the thing it was built for.

## 4. Strengths

**The refactor paid for itself within the same milestone.** Inserting
`classify_objects` required one entry in `STAGES` and one function. Under the old
`harvest_pack` it would have been an eighth responsibility in a 130-line
procedure.

**Classification cannot churn.** Write-once semantics mean a rule tweak affects
future objects, not the archive. Verified: 222 classified, then zero, three runs
running.

**Both M4 bugs looked like success.** The default-value bug reported "222
classified" while writing nothing meaningful; the feedback bug produced a
plausible small delta. Neither would have been caught by reading the code, and
neither showed as an error.

**Rules are genuinely data.** 51 rules in `pack.yml`, zero classification
vocabulary in the engine. A second pack needs no engine change — the property M8
is meant to prove.

## 5. Weaknesses

**W1 — Substring matching is blunt.** `"sql"` matches `"nosql"`; `"api"` matches
`"rapid"`. Ordering and exclusions mitigate it, but a pack author can write an
over-matching rule and nothing warns them. A `ke classify --explain` preview
would help (TD-13).

**W2 — No retroactive reclassification.** Improving a rule leaves existing
objects untouched. Correct default, but there is no supported way to opt in. A
`ke reclassify` needs a dry-run, a diff preview and locked-field exclusion —
worth building properly (TD-12).

**W3 — 45 objects flagged `needs_review` have no triage path.** They join the 26
queued items in a growing set of things needing human attention with only
one-at-a-time handling (TD-8 unchanged).

**W4 — `classify_objects` re-reads every object.** It calls
`load_existing_objects`, as does `rebuild_indexes` — twice per harvest now
(TD-2 unchanged, but the cost doubled).

**W5 — A field deliberately set to its default value is indistinguishable from
an unset one**, so classification may overwrite it. The remedy is `overrides`,
which is documented, but the failure is silent.

## 6. Findings

### F1 — Classification wrote nothing while reporting success (HIGH — fixed)

`applicable` tested falsiness to decide whether a field was already set. `tier`
defaults to `AWARENESS` (3) and `difficulty` to `INTERMEDIATE` — **both truthy**
— so every enum-valued field looked already-decided.

Result: **all 222 objects came back `tier: 3`**, which was the default rather
than a decision. The pipeline reported "222 classified" because `reading_time`
had genuinely changed, so the counts looked healthy and the distribution looked
like a real (if poor) classifier.

Fixed by comparing against the dataclass default — the honest question is "has
anything ever set this?", not "is this falsy?".

### F2 — Classification fed on its own output (MEDIUM — fixed)

`_haystack` included `category` and `tags`, which classification writes. A second
harvest matched rules the first could not, reclassifying four more objects.

Fixed by excluding them. Classification is now a pure function of the knowledge.

### F3 — A regression test that could not fail (process finding)

The first version of the F2 regression test passed with **and** without the fix,
because the fixture's rules did not actually create a feedback loop. Rewritten
so a category value feeds a workload rule, then verified to fail.

This is the second milestone running where a test claimed coverage it did not
have. **Verifying that a test fails against the old behaviour is now the only
thing that distinguishes a guard from a decoration**, and it has caught a
worthless test twice.

## 7. Technical debt

| # | Item | Severity | When |
|---|---|---|---|
| TD-2 | Objects re-parsed twice per harvest | Medium (was Low) | ~1,000 objects |
| TD-3 | Near-duplicate check is O(n²) | Low | same trigger |
| TD-6 | `models.py` ~1,480 lines | Medium | M5 |
| TD-8 | No bulk triage — now 26 queued + 45 flagged | **High** (was Medium) | M5/M6 |
| TD-10 | No retirement path for vanished features | Medium | M5 |
| TD-12 | No `ke reclassify` for retroactive rule changes | Medium | M6 |
| TD-13 | No rule preview / over-match warning | Low | M6 |
| TD-1 | ~~`harvest_pack` monolith~~ | **Closed** | — |

## 8. Risks for M5

| Risk | Severity | Mitigation |
|---|---|---|
| **Supersession collides with lifecycle naming** | Medium | ADR-0029 already flags `Lifecycle.SUPERSEDED` vs `status: replaced` as the softest part of the design; M5 is where it must be resolved or one of them dropped |
| **Human-attention backlog is now 71 items** | **High** | 26 queued + 45 flagged, both one-at-a-time. M5 should not add a third category without a triage path |
| **`models.py` growth under revisions work** | Medium | Split before adding the supersession fields |
| **Revision history has no reader** | Medium | M5 writes snapshots; nothing yet reads them back. The Time Machine is unexercised, so its data model is unvalidated |

## 9. Assessment

M4 did the boring thing first and it paid off inside the same milestone:
classification landed as one line in a stage list rather than as surgery on a
growing procedure.

The two bugs are worth remembering together — both **reported success while
being wrong**. Counts looked healthy, distributions looked plausible, and only
looking at the actual values revealed that one wrote nothing and the other wrote
too much. Running the pipeline is necessary but not sufficient; you have to read
what it produced.

**Recommend merge.**
