# M5 Architecture Review — Review workflow, revisions and supersession

**Reviewer:** Senior Software Architect
**Date:** 2026-08-01
**Scope:** unified review, revision-chain validation, supersession, time travel
**Verdict:** **Ready to merge**, with one data-integrity decision for the
maintainer (§6, F1) that is deliberately not taken here.

---

## 1. What was built

| Module | Job |
|---|---|
| `reviewq.py` | One workflow over three kinds of pending decision |
| `history.py` | Time travel, chain verification, supersession |
| `validate.py` | REV001/REV002 — history correctness and flip-flop detection |
| `models.py` | `Lifecycle.SUPERSEDED` removed (ADR-0035) |

New commands: `ke review list|next|show|approve|archive|resolve`,
`ke history <id> [--at N]`, `ke supersede <old> --by <new>`.

## 2. The six priorities

| # | Priority | Result |
|---|---|---|
| 1 | Unified review foundation | 3 kinds, one lens; adding a fourth is one provider |
| 2 | Revision detection | Already in M3; now surfaced as review tasks |
| 3 | Revision history validation | 5 chain checks + flip-flop detection |
| 4 | Supersession lifecycle | Both directions linked; naming collision resolved |
| 5 | Time-travel validation | `ke history` reads snapshots; 26 tests |
| 6 | Regression tests before fixes | REV002 pins the M3 flip-flop signature |

## 3. Strengths

**Unifying the workflow without unifying the storage was the right cut.** Each
kind keeps the storage that suits it — a queued item has no object to live in, a
flagged object's `needs_review` belongs to the object — so nothing had to be
migrated and no source of truth moved. What was missing was a *lens*, and a
`ReviewTask` is exactly that: assembled on demand, never persisted, so it cannot
drift from what it describes.

**Adding a review kind is now cheaper than building a silo.** One provider
function plus one entry in `PROVIDERS`. That is the property that actually
prevents a fourth backlog, rather than a note asking future work not to create
one.

**Bulk actions close TD-8.** 45 unclassified objects clear in one command; the
backlog went 72 → 27 in the demonstration.

**ADR-0029's recorded weakness was acted on rather than inherited.** The
`SUPERSEDED` / `replaced` collision was written down as a known problem in M3 and
resolved at the first milestone that touched supersession. Acting on a recorded
weakness at the first opportunity is what stops it becoming permanent.

**Reading the Time Machine back immediately validated it — by finding it
polluted.** A data model written for three milestones and read by nothing had
never been checked.

## 4. Weaknesses

**W1 — `collect()` reads every object on every call.** Unclassified and revision
tasks are derived from stored objects, so listing the queue costs a full pack
scan. Shares the fate of TD-2; at 222 objects it is invisible.

**W2 — A failing provider is skipped silently.** Right for a listing (one broken
kind must not hide the others), but a consistently failing provider shows as an
empty category rather than an error.

**W3 — `resolve --all` has no dry-run.** Acceptable because resolving is
reversible; would not be acceptable for an action that minted or deleted. Worth
a `--dry-run` before any destructive bulk action is added (TD-14).

**W4 — Revision review only surfaces retitles.** Date corrections and summary
rewrites are recorded but never queued. That is a deliberate filter — most
revisions are noise — but the threshold is a judgement with no evidence behind
it yet.

**W5 — `Lifecycle.ARCHIVED` is still written by nothing** for objects. Queued
items can be archived; stored objects cannot. The asymmetry is unexplained.

## 5. Findings

### F1 — 35 objects carry polluted revision history (MEDIUM — needs a decision)

Reading histories back revealed **35 objects with 11 revisions each, every one
recording the identical field change** (`published_date`, `date_confidence`,
`date_precision`). They are residue from the M3 flip-flop bug, produced before it
was fixed.

**Why nothing caught it:** every individual revision is well-formed. The
corruption is only visible in the *pattern*, and no check looked at patterns.
`ke validate` reported zero errors throughout.

**It is not recurring** — three consecutive harvests report `0 updated`.

**Two honest options, and this is a data-integrity decision so it is raised
rather than taken:**

| Option | Argument |
|---|---|
| **Leave them** (current) | The revisions *truthfully record what the engine did*. Rewriting history to look tidier would make it less true, and CLAUDE.md's append-only rule exists for exactly this temptation |
| **Collapse them** | 350 junk entries make `ke history` unreadable for those objects and will confuse anyone reading them in a year. They are provably artifacts of a known bug, not knowledge |

My recommendation is **leave them and keep the warning**. The history is accurate
about a period when the engine misbehaved, and that is worth more than a clean
log. The 35 warnings are visible in every `ke validate` run and cost nothing.

Detection is now permanent: `REV002` flags three or more consecutive identical
revisions.

### F2 — A speculative enum value cost three milestones of friction (resolved)

`Lifecycle.SUPERSEDED` was defined in M3 without a producer, flagged as a
weakness in ADR-0029, carried in the glossary and the playbook, and removed in
M5 having never been used. `ARCHIVED` was defined the same way and survives only
because its meaning is unambiguous.

**Lesson: define a state when something produces it.** Guessing at semantics in
advance costs more than adding the value later.

## 6. Technical debt

| # | Item | Severity | When |
|---|---|---|---|
| TD-8 | ~~No bulk triage~~ | **Closed** | — |
| TD-2 | Objects re-parsed per harvest **and** per review listing | Medium | ~1,000 objects |
| TD-6 | `models.py` ~1,480 lines | Medium | M6 |
| TD-10 | No retirement path for features that vanish from source | Medium | M6 |
| TD-12 | No `ke reclassify` | Medium | M6 |
| TD-13 | No rule over-match warning | Low | M6 |
| TD-14 | Bulk actions have no `--dry-run` | Low | Before any destructive bulk action |
| TD-15 | Revision review only surfaces retitles | Low | When evidence suggests otherwise |
| TD-16 | 35 objects with polluted history (F1) | Medium | Maintainer decision |

## 7. Risks for M6

| Risk | Severity | Mitigation |
|---|---|---|
| **The weekly workflow is the first unattended run** | **High** | Everything so far has been run by hand with a human watching. M6 puts it on a cron where failures are silent by default — the run log and notifications are what make that safe |
| **Notification failure must not fail the run** | Medium | Already the plan (ADR-0013); needs testing, not just intent |
| **Concurrent runs could mint duplicate IDs** | Medium | Needs a `concurrency` group on the workflow; the registry's consistency check would catch the damage afterwards but not prevent it |
| **`git pull --rebase` before push** | Medium | A hand-edit landing between harvest and push would otherwise conflict and lose the run |
| **Backlog visibility depends on somebody looking** | Medium | The digest should carry the review count, or the unified queue is just a better-organised place to be ignored |

## 8. Assessment

M5 did the foundation work before the feature work, and it paid off twice: the
review workflow absorbed revision review as one provider rather than a third
queue, and reading the Time Machine back — the point of the milestone — found
damage that four milestones of green pipelines had not.

That is the clearest illustration yet of the lesson this project keeps
relearning: **a successful run says nothing about whether the output is right.**

**Recommend merge**, with F1 as a decision to take rather than a defect to fix.
