# M6 — Architecture Review

**Milestone:** M6 — Weekly automation
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect

---

## What changed architecturally

M6 adds no new domain concepts. There is no new entity, no schema change, no
change to identity, minting, classification or revisions. `schema_version` stays
at 1 and every one of the 222 stored objects is byte-identical to before this
branch except where a genuine change occurred.

What M6 changes is the engine's **operating mode**, and that turns out to be the
larger shift:

| | Through M5 | M6 |
|---|---|---|
| Who runs it | A person, on demand | A scheduler, weekly |
| Who watches | A person, live | Nobody |
| Credentials held | None | SMTP + a write token |
| Failure visibility | Immediate | Whatever the engine chooses to report |
| Write authority | A person's own checkout | A job with `contents: write` |

Every design decision in this milestone follows from row three and row four.

## The four additions

### `digest.py` — making a silent system observable

The digest is not a reporting nicety; it is the only thing that distinguishes
"a quiet week" from "the engine stopped working". ADR-0037 records the reasoning
and the three rules that follow from it.

The architecturally interesting choice is that the digest is **derived, not
accumulated**. `gather()` reads the pack as it now stands and `render()` is a
pure function of that snapshot. There is no digest state, nothing to keep in
sync, and no way for the digest to disagree with the repository — the same
property that makes indexes safe to rebuild in full every run.

### `notify/` — the pluggable boundary from ADR-0013, finally used

Two channels, one protocol. The design constraint that shaped the code is that
**a notifier must never be able to fail a harvest**. By the time notification
runs, the knowledge is committed; losing a harvest because an SMTP server was
down would be an absurd trade. `notify_all` therefore never raises, and
`send_notifications` is the last stage in the pipeline.

`from_environment() -> Notifier | None` is worth calling out as a small but
load-bearing decision. An unconfigured channel returns `None` rather than
raising or constructing a broken object, so "the user has not set up email" and
"email failed" stay distinguishable — and running without secrets is the
supported default rather than an error path.

### `lock.py` — protecting the one unrecoverable invariant

ADR-0039. Of everything the engine can get wrong, a duplicate Feature ID is the
only failure that is genuinely permanent, and a cron is the first thing capable
of causing one.

The lock lives in the **CLI**, around `harvest_pack`, not inside the pipeline.
That placement is deliberate: locking is a property of *running a harvest*, not
of the algorithm, and a pipeline that acquires OS resources stops being a pure
staged transform and starts being hard to test.

### `weekly-harvest.yml` — the containment boundary

The most important line in the file is `git add domain-packs/`. A harvest that
could commit outside the pack could modify the engine that runs it next week, or
the workflow's own permissions — a self-modifying scheduled job holding a write
token. Everything else in the workflow is conventional; that line is the
architecture.

Second-most important: **validate before and after**. A pack that is already
broken must not be harvested into, and a run is only a success if what it wrote
is valid. That second check is this repository conceding, in executable form,
the lesson it has now learned five times.

## The pipeline, at eleven stages

```
discover → load_state → deduplicate → update_existing → gate_and_mint
→ classify_objects → persist_state → rebuild_indexes → write_digest
→ append_run_log → send_notifications
```

The M4 refactor (TD-1) is paying off exactly as intended. Adding two capabilities
to the run meant appending two functions to `STAGES` and writing two tests. No
existing stage changed. The ordering constraint that mattered — the durable
record must be written before the ephemeral one — is expressed as position in a
tuple and asserted by a test rather than living in a comment.

This is the second milestone in a row where the staged pipeline absorbed new
work without structural change. I consider the abstraction proven.

## Where the design bent

**`HarvestReport` grew a `warnings` list.** This was not planned. It became
necessary when the classification defect surfaced: the engine needed to say
"this run worked *and* you should know something", and it had only `errors`,
which fails the run, and silence, which is what caused the bug in the first
place.

The alternative was reusing `errors` and accepting a non-zero exit for a
configuration gap. Rejected: dressing a warning as a failure is precisely how
people learn to ignore failures, and this engine's whole value proposition rests
on its reports being believed.

**`models.py` gained an input-sanitisation responsibility.** The single-line
title invariant sits on `RawItem` and `KnowledgeObject` rather than in
`normalize.py`, where cleaning otherwise lives. This is a mild layering
compromise, made knowingly: `normalize.py` imports from `models.py`, so the
dependency cannot run the other way, and enforcing the invariant on the type is
the only placement that holds for adapters not yet written. A helper in
`normalize.py` would protect only the adapters that remembered to call it.

## What did not change, and should not have

* **No new domain concepts.** The temptation in an automation milestone is to
  add "run", "schedule" or "notification" as first-class stored entities. None
  of them are knowledge, and none of them are in the schema.
* **No AI anywhere in the scheduled path**, asserted by a test on the workflow
  file. The digest is counting and formatting.
* **No new runtime dependency.** SMTP is `smtplib`, the GitHub API call is
  `urllib.request`, ISO weeks are `date.isocalendar()`.
* **Feature IDs, object paths and revision history: untouched.**

## The recurring lesson, again

The maintainer's standing note — *a successful pipeline execution does not
guarantee correct output* — held for a sixth milestone, and this time it bit in
the tests rather than the code.

I wrote a digest test guarded by `if total:`. It passed. Tightening it to an
unconditional assertion made it fail, and the failure was not in the test: a
pack with no classification rules had every object stored with `category: None`
and `needs_review: False`, invisible in the review queue, reported as a clean
run. That is the state every new domain pack starts in, and M8 exists to add
one.

The generalisable form: **a conditional assertion is a test that has been given
permission to prove nothing.** Worth watching for as deliberately as the
"successful execution" trap it belongs to.

## Assessment

| Dimension | Verdict |
|---|---|
| Schema stability | **Unchanged** — no migration, no `schema_version` bump |
| Abstraction integrity | **Strengthened** — pipeline absorbed two stages structurally unchanged |
| Coupling | **Low** — notify and lock are leaf modules with one caller each |
| Testability | **Improved** — the workflow's shell is now executable in tests |
| Operating cost | **Unchanged** — ~1% of the free Actions budget |
| Vendor independence | **Unchanged** — no AI, two dependencies, both stdlib-adjacent |
| Reversibility | **High** — deleting `notify/`, `lock.py` and the workflow returns the engine to its M5 behaviour exactly |

M6 is the least architecturally adventurous milestone so far and, in my
judgement, the right one to be least adventurous in. The engine did not need new
ideas at this point; it needed to survive running without supervision, and the
work went into the properties that determine whether it does.
