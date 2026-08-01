# M7 — Operational Readiness Review

**Milestone:** M7 — Retrieval and on-demand generation
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** the engine as it will actually run — unattended, weekly, on a
disposable runner, with a repository write token

---

## What this review asks

Not *"does it fail?"* — everything fails. The question is:

> **What state does it leave behind, and who has to clean it up?**

Every scenario below is classified as exactly one of:

| Class | Meaning |
|---|---|
| **Graceful degradation** | The run completes, does less than it wanted to, and says so. No human needed. |
| **Automatic recovery** | The run is damaged, but the *next* run repairs it with nobody involved. |
| **Manual recovery** | A person must act. The engine's obligation is to make that unmissable and to say what to do. |

A scenario with no test is a guess. Every claim here cites a test in
`engine/tests/test_operational.py` (25 tests), `test_security.py` or
`test_workflow_push.py`, so the document is checkable rather than reassuring.

## One methodological note, because it changes what the results mean

Two of the ten scenarios — permission failures and disk full — are conventionally
tested with `chmod` and a small filesystem. **That does not work here.** CI and
the development container both run as **root**, and root bypasses permission
bits entirely. A `chmod`-based test passes without exercising anything: a green
check mark attached to an unverified claim.

I found this by writing such a test and watching it pass against a directory
that should have refused the write.

So `PermissionError` and `OSError(ENOSPC)` are **injected at the write
boundary**. That tests the engine's handling, which is the part this repository
owns; the kernel's enforcement is not under test and does not need to be. One
`chmod`-based test remains, marked `skipif(root)`, precisely so the distinction
stays visible rather than being quietly papered over.

---

## Summary

| # | Scenario | Class | Data loss? | Tests |
|---|---|---|---|---|
| 1 | Interrupted harvest (stage raises) | Automatic recovery | No | 2 |
| 2 | Disk full | Graceful degradation | No | 5 |
| 3 | Permission failures | Graceful degradation | No | 2 (+1 skipped as root) |
| 4 | Git push failures | Automatic recovery | No | 3 |
| 5 | SMTP failures | Graceful degradation | No | 2 |
| 6 | GitHub API failures | Graceful degradation | No | 2 |
| 7 | Lock recovery | Automatic recovery | No | 3 |
| 8 | Corrupted state files | Mixed — see below | No | 4 |
| 9 | Partial writes | Graceful degradation | No | 3 |
| 10 | Unexpected termination | Automatic recovery* | No | 3 |

\* with one exception, **O-1**, which is the only manual-recovery path in the
engine. It is described in full below.

**No scenario in this review results in lost knowledge.** That is not luck: it
follows from three decisions made earlier — objects are written before state
(ADR-0031), writes are atomic and both-or-neither (M2), and nothing is ever
deleted (CLAUDE.md).

---

## 1 · Interrupted harvest — **automatic recovery**

**What happens.** A stage raises. The pipeline records the error, sets `stop`,
and skips the remaining stages. Everything written by earlier stages stays
written; nothing is unwound.

**Why not roll back.** Stages are deliberately not transactional, and pretending
otherwise would be worse than the honest version. The ordering is chosen so a
partial run is always recoverable: objects before state, state before indexes,
indexes before the digest. A run that stops anywhere in that sequence leaves the
pack in a state the next run can read.

**Recovery.** The next harvest completes normally. Indexes are rebuilt in full
from what is on disk, so anything the interrupted run failed to write is
regenerated rather than patched.

*Tests:* `test_a_crash_in_one_stage_stops_the_run_without_unwinding_earlier_ones`,
`test_an_interrupted_run_leaves_a_pack_the_next_run_can_use`.

## 2 · Disk full — **graceful degradation**

**What happens.** `ENOSPC` during an object write. The run **completes**, mints
nothing, records one error naming the failure, and writes no partial object. The
Feature ID is not consumed — the counter is still where it was, so the number is
available next time.

**Why the run completes rather than crashing.** A weekly job that dies with a
traceback has told GitHub it failed and told the operator nothing useful. The
digest and run log are still written, so the failure is *reported* rather than
merely *suffered*.

**Recovery.** Free space; the next run rediscovers the item and mints it.

*Tests:* `test_a_write_failure_completes_the_run_and_reports_it`,
`test_a_write_failure_leaves_no_partial_object`,
`test_a_write_failure_partway_through_does_not_consume_a_feature_id`,
`test_a_later_run_recovers_after_a_transient_disk_failure`,
`test_a_digest_is_written_even_when_the_run_had_errors`.

**Note on the runner.** A GitHub-hosted runner has a fixed disk allowance. A
harvest writes a few hundred kilobytes; exhausting it would require something
else to have gone wrong first. The scenario is covered because it is cheap to
cover, not because it is likely.

## 3 · Permission failures — **graceful degradation**

Identical handling to disk full: `PermissionError` is caught at the same
boundary, the run completes, the error is reported, nothing partial is written.

The realistic cause is not a hostile permission change but a checkout owned by a
different user, or a pack directory on a read-only mount.

*Tests:* the parametrised `permission_denied` cases of the two write-failure
tests, plus `test_a_read_only_pack_directory_is_reported` (skipped under root,
deliberately).

## 4 · Git push failures — **automatic recovery**

**What happens.** The push is rejected — branch protection, a race, an outage.
The workflow retries three times with backoff, rebasing between attempts. If all
three fail, the step **exits non-zero** and the commit dies with the runner.

**Nothing partial is published.** The remote is byte-identical to what it was
before the run. That is the property that makes a failed harvest cost nothing:
the pipeline is idempotent, so next Sunday rediscovers the same items and mints
them then.

**Why no `--force`, ever.** A force-push from an unattended weekly job is the
one operation in this design that could actually destroy knowledge. Asserted by
a test on the script text, because its absence cannot be tested by running it.

**The concurrent hand-edit case.** Learning state is user-owned and edited by
hand. If a push landed between checkout and push, the rebase picks it up; both
survive. This is the case the rebase exists for, and removing all rebasing fails
the test.

*Tests:* `test_a_rejected_push_fails_the_run_rather_than_reporting_success`,
`test_a_rejected_push_leaves_the_remote_untouched`,
`test_an_unreachable_remote_fails_before_pushing`,
`test_the_push_step_never_forces`,
`test_a_concurrent_hand_edit_survives_the_push`.

## 5 · SMTP failures — **graceful degradation**

**What happens.** The notifier raises; `notify_all` catches it, redacts the
message, records it in `notification_failures`, and the run succeeds.

**Why it cannot fail the run.** By the time notification happens, the knowledge
is already on disk and the run log is already appended. Losing a harvest because
an SMTP server was down would be an absurd trade — and failing here would also
suppress the weekly commit that keeps the cron from being auto-disabled.

**Unconfigured is not failed.** `from_environment()` returns `None` when the
secrets are absent, so running with no email configured is the supported default
rather than an error path.

*Tests:* `test_a_failing_notifier_never_fails_the_harvest`,
`test_a_notification_failure_does_not_stop_the_other_channels`.

## 6 · GitHub API failures — **graceful degradation**

Same path as SMTP: a `503` from `api.github.com` while posting the digest Issue
is recorded, not raised.

**What is lost.** The Issue for that week. The digest itself is already in the
repository at `digests/YYYY-Www.md` — the durable record is written *before* the
ephemeral one, which is why `write_digest` precedes `send_notifications` in the
stage list. A reader who never receives the notification can still read
everything it would have told them.

*Tests:* `test_a_github_api_failure_is_recorded_not_raised`,
`test_the_digest_is_written_before_notifications_are_sent`.

## 7 · Lock recovery — **automatic recovery**

**What happens.** A process killed hard cannot release its lock file.

**Recovery, three ways:**

* A lock older than `STALE_AFTER_SECONDS` (one hour) is reclaimed. A harvest
  takes seconds; an hour is far outside the normal range.
* A lock whose contents cannot be parsed is reclaimed, not obeyed. Treating "I
  cannot read this" as "somebody is working" is how a system deadlocks on its
  own garbage.
* A lock held by a run that raised is released in `finally`.

**The trade-off, stated.** An hour-old lock is *assumed* dead. If a harvest
genuinely ran longer than an hour and a second started, both could mint. The
workflow's 20-minute timeout makes that impossible on the scheduled path.
Never reclaiming would turn one crash into a permanently unusable pack, which is
strictly worse than the concurrency it prevents.

*Tests:* `test_a_lock_left_by_a_killed_process_is_reclaimed`,
`test_a_lock_from_a_live_run_is_respected`,
`test_the_lock_is_released_when_the_run_raises`.

## 8 · Corrupted state files — **deliberately mixed**

The policy is **not uniform**, because the consequences are not uniform
(ADR-0032). Uniformity here would mean either stopping for damage that costs
nothing, or continuing through damage that is permanent.

| File | Policy | Why |
|---|---|---|
| `id-registry.json` | **Stop the run** | The counter is the only thing preventing a duplicate permanent Feature ID. Guessing is unrecoverable. |
| `seen.json` | **Degrade and continue** | Worst case is re-processing something already known. Wasteful, not harmful. |
| `review-queue.json` | **Stop the run** | See below. |
| One `metadata.yaml` | **Skip it, continue** | One damaged object must not cost the other 221. |

**The review queue surprised me, and the reason is worth recording.** I expected
it to degrade — queued items are rediscovered next run, so treating a damaged
queue as empty looks free. It is not. The queue stores `first_discovered_date`,
and a Feature ID's month comes from when the knowledge *appeared*, not when a
human got round to approving it (ADR-0028). Silently losing those dates would
shift the month of every ID minted from the queue afterwards — permanently.

So it belongs with the registry, for the same underlying reason: the damage
would be to something that cannot be undone. I wrote the test asserting
degradation, it failed, and the existing behaviour was right.

**Recovery.** Registry or queue: restore from Git, which holds every previous
version. `seen.json`: nothing to do. A damaged object: `ke validate` names the
file.

*Tests:* `test_a_corrupt_dedup_cache_degrades_rather_than_stopping`,
`test_a_corrupt_registry_stops_the_run`,
`test_a_corrupt_review_queue_stops_the_run`,
`test_one_unreadable_object_does_not_cost_the_others`.

## 9 · Partial writes — **graceful degradation**

A knowledge object is a **pair** of files. A failure between them would leave a
directory that looks like an object and is not, at a path that is permanent.

Three layers prevent it:

1. **Both documents are rendered before either is written.** A serialisation
   error becomes "no files written" rather than "half an object" — this is the
   fix for the M2 bug that produced 222 orphaned `feature.md` files.
2. **Each write is atomic**: temp file in the same directory, then `os.replace`.
   Same directory matters — `os.replace` is only atomic within a filesystem.
3. **Cleanup on failure**: whatever was written is unlinked, and an empty
   directory is removed.

*Tests:* `test_a_write_failure_leaves_no_partial_object` (both failure kinds),
`test_an_interrupted_object_write_leaves_nothing`.

## 10 · Recovery after unexpected termination — **automatic recovery, with one exception**

A `SIGKILL` — OOM, cancelled runner, closed laptop — can land anywhere. Walking
the stage list:

| Killed during | State left | Recovery |
|---|---|---|
| discover / dedupe | Nothing written | Automatic |
| gate_and_mint | Objects on disk, registry not yet saved | **See O-1** |
| persist_state | Registry saved, indexes stale | Automatic — indexes rebuild in full |
| rebuild_indexes | Indexes partially written | Automatic — rebuilt in full next run |
| write_digest | No digest for the week | Automatic — next run writes it |
| send_notifications | Everything on disk, no notification | Automatic |

The registry is saved **after** objects are written, deliberately: a crash
leaves an ID *gap*, which is cosmetic, where the reverse order would leave an ID
pointing at nothing, which is permanent.

*Tests:* `test_a_crash_mid_harvest_leaves_an_id_gap_not_a_dangling_id`,
`test_an_interrupted_run_leaves_a_pack_the_next_run_can_use`,
`test_a_lock_left_by_a_killed_process_is_reclaimed`.

---

## Findings

### O-1 · Medium · A failed registry write leaves objects the registry does not know about

**The only manual-recovery path in the engine.**

**What happens.** Objects are minted and written. `persist_state` then fails —
disk full, permission denied, killed at exactly the wrong moment. The objects
exist on disk; the registry does not record them.

**What is *not* damaged, and this matters:**

* **No knowledge is lost.** Both files are on disk, complete and valid.
* **No Feature ID is duplicated.** The next run allocates the same number, and
  `write_object` refuses to overwrite a minted object. That refusal is what
  stops the one genuinely permanent failure this engine can suffer.
* **It is detected, loudly.** `ke validate` reports `REG003: <id> is not
  registered`, an **error**, not a warning.

**What is damaged.** The pack is internally inconsistent until somebody acts.
Left alone across several runs, the same feature can end up stored twice under
two different IDs — a duplicate *object*, not a duplicate *ID*. That is a data
quality problem, and nothing is destroyed by it.

**Why the scheduled path recovers automatically anyway.** The weekly workflow
validates **after** harvesting and **before** committing. A pack in this state
fails validation, the job stops, the push step never runs, and the runner is
discarded. The next Sunday starts from the last good commit. **This state can
never reach the repository on the scheduled path** — asserted by
`test_the_scheduled_path_cannot_publish_an_inconsistent_pack`.

It therefore needs a human only after a **local** `ke harvest`, where the
working tree survives.

**What M7 changed.** The error message. Previously the operator saw
`OSError: [Errno 28] No space left on device` — accurate, and useless. Now:

> could not write the ID registry (…). 3 object(s) were written this run and are
> NOT registered. Nothing is lost and no Feature ID was duplicated. Run
> `ke validate` to list them (REG003), free space or fix permissions, then
> re-run the harvest.

**What M7 did not change, and why.** The structural fix is to save the registry
entry as each object is minted rather than once at the end. That reorders
minting and persistence, which is ADR-0031 — an architectural decision about the
harvest sequence, and one with its own failure trade-offs (a registry entry
written before its object is the *permanent* version of this bug). Making that
change unilaterally is outside what this milestone should decide.

**Recommended for M8**, alongside a `ke repair --registry` command that
re-registers orphaned objects from what is on disk. Tracked as **TD-10**.

*Tests:* `test_a_registry_write_failure_is_reported_with_its_remedy`,
`test_a_registry_write_failure_never_duplicates_a_feature_id`,
`test_validate_detects_an_unregistered_object`,
`test_the_scheduled_path_cannot_publish_an_inconsistent_pack`.

### O-2 · Low · There is no runbook

Every recovery in this document is derivable from the code and the tests. None
of it is written down where somebody would look at 07:00 on a Sunday with a red
workflow badge.

`docs/RUNBOOK.md` is scheduled for M9 and should cover: re-enabling a disabled
cron, rotating the SMTP secret, recovering `seen.json`, repairing an
unregistered object (O-1), and reading a failed harvest's digest.

Tracked as **TD-11**.

### O-3 · Low · The stale-lock window is not configurable

`STALE_AFTER_SECONDS` is a module constant. An operator who genuinely needs a
two-hour harvest cannot raise it without editing code.

Not worth fixing now: the workflow times out at 20 minutes and a 222-object
harvest completes in seconds. Recorded so the constant is a known decision
rather than an unexamined default.

### O-4 · Informational · Disk exhaustion on the runner is untested end to end

The handling is tested by injection. Actually filling a GitHub runner's disk to
observe real behaviour is not something this suite can do, and the value would
be low: the code path is the same one the injected tests exercise.

### O-5 · Informational · The engine has never recovered from a real failure

Every scenario here is induced. As of this review the weekly workflow has run
zero times unattended — it reached `main` with M6 and the first scheduled run
has not happened.

The tests are evidence about the code, not about the world. Worth restating
plainly in the next review once there is operational history to draw on.

---

## What would change my assessment

Two things would move this from "ready" to "not ready", and neither is currently
true:

1. **Any scenario that loses user-owned data.** None does. The field-ownership
   model means the engine cannot write learning state, notes or artifacts even
   when it is failing.
2. **Any scenario that duplicates a Feature ID.** None does. The `write_object`
   refusal holds even when the registry is wrong, which is the case that
   matters.

## Assessment

**Ready to run unattended.** One manual-recovery path (O-1), which the scheduled
workflow's validate-before-push already prevents from reaching the repository,
and which now explains itself when it happens.

The recurring shape of this review is worth naming: three times, the behaviour I
expected to find was less careful than the behaviour actually there — the review
queue's stop-the-run policy, the ID counter not being consumed by a failed
write, and the digest still being written on a failed run. Each was a decision
made in an earlier milestone for a reason that had been written down. That is
the return on the ADRs.
