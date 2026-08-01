# ADR-0037: A digest every week, even an empty one

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M6

## Context

M6 is the first milestone where the engine runs with nobody watching.

Every earlier milestone was executed by hand. A failure was visible immediately,
because a human was reading the output at the moment it happened. On a cron that
stops being true, and the failure modes change shape:

* A source quietly starts returning zero items. The run is green every week.
* The workflow is disabled and nobody notices for two months.
* A path is mistyped, the harvest finds no packs, and exits 0 having done nothing.
* The review backlog grows past a hundred items with nobody looking at it.

Every one of those produces the same observable evidence as a healthy week that
happened to be quiet: **nothing**. That is the problem this ADR exists to solve.

There is a second, GitHub-specific reason. Scheduled workflows on a repository
with no commit activity for 60 days are auto-disabled. An engine that publishes
only when it finds something would switch itself off during a quiet period —
exactly when it is least likely to be noticed.

## Decision

**A digest is written on every run, unconditionally, including runs that found
nothing.** One file per ISO week, at `digests/YYYY-Www.md`.

Three rules govern its content:

1. **Absence of news produces evidence, not absence of a file.** "No updates
   this week" and "the harvest did not run" must be distinguishable, and the
   only reliable way to tell them apart is that one of them wrote a file. The
   empty digest says so in words: *every source was read successfully and
   reported no updates the engine had not already stored.*

2. **The review backlog is always in it, above the headline counts.** A unified
   queue (ADR-0036) that is mentioned in a footnote is a better-organised place
   to be ignored. The total goes where it cannot be missed.

3. **Problems are rendered before successes.** A dead source, then errors, then
   warnings, then the summary. A digest that leads with "12 new items" and
   buries an unreachable source four sections down has reported the wrong thing
   first — and the reader will stop at the good news.

The digest is overwritten if a harvest runs twice in the same week. It describes
the **week**, not the run; two files for one week would be two answers to the
same question, with nothing to say which is current.

## Consequences

**A commit lands every week.** The digest plus the appended run log guarantee a
non-empty diff, so the 60-day auto-disable rule can never trigger. This is a
side effect of the decision rather than its purpose, but it is the reason the
run log is appended unconditionally too.

**Silence becomes a signal.** A missing `digests/2026-W32.md` now means
something specific and checkable: the run did not happen. Before this, it meant
nothing at all.

**One more file per week, forever.** ~52 small Markdown files per year per pack.
Acceptable against a repository that already holds a directory per knowledge
object, and each one is a durable weekly record of what the engine believed.

**The digest can be wrong in a new way.** It now reports counts that a reader
will trust without re-deriving. A digest that renders its own plausible-looking
numbers rather than the run's would be the most dangerous output this engine
has: confident, readable and wrong. `test_digest.py` asserts the rendered counts
against the `HarvestReport` for exactly that reason.

## Alternatives considered

**Publish only when something changed.** Smaller repository, and the obvious
default. Rejected: it makes a broken engine and a quiet week indistinguishable,
and it walks straight into the auto-disable rule.

**One digest per run rather than per week.** Rejected: it makes the common case
(one scheduled run per week) identical while making the uncommon case — a manual
re-run — produce a confusing second file describing the same period.

**Notify without writing a file.** Rejected: notifications are ephemeral and
best-effort. A reader who finds a summary in their inbox and no detail in the
repository has been told a story with no evidence behind it. The durable record
is written first; see the stage ordering in `pipeline.py`.
