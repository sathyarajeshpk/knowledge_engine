# Development Journal

One section per milestone. Written at the end of each, while the reasoning is
still fresh.

The purpose is not to duplicate the changelog. It is to record the things that
normally evaporate: what was surprising, what was harder than expected, what was
decided under uncertainty, and what the next milestone should watch out for.

---

## M0 — Foundation, Schema and Guardrails

**Dates:** 2026-07-31
**Status:** Complete, awaiting review
**Version:** v0.1.0 · Schema version 1

### What was built

An engine with no pipeline. Seven work items: package scaffold, core models,
schema contract, pack skeleton, validator and CLI, CI, and the CLAUDE.md
clarifications. Eight commits, ~1,100 lines of engine code, ~1,000 lines of
tests, 107 tests running in 0.6 seconds.

Nothing fetches a feed. Nothing mints an ID. Nothing writes a knowledge object.
That was the design: build the things that are nearly impossible to retrofit once
data exists on disk, and nothing else.

### Decisions made, and why

The architecture went through two full revisions before a line of code was
written, which turned out to be the most valuable part of the milestone.

**Revision 1** established the fundamentals: no AI in the scheduled pipeline,
files instead of a database, a monorepo engineered to split, three independent
classification axes, and both notification channels behind a pluggable interface.

**Revision 2** added date-based Feature IDs, learning metadata, topic
relationships, extensible knowledge objects, and generation status tracking.

The second revision is where the interesting thing happened. Adding
`learning_status` and `notes` to `metadata.yaml` was obviously correct — learning
state belongs next to the knowledge it describes. But it silently created the
project's most dangerous failure mode: **a weekly automated job now rewrites files
containing the user's own hand-maintained work.**

Nobody asked for protection against that. It emerged from the combination of two
individually sensible decisions. The response was the field ownership model
(ADR-0008): a three-way partition asserted at import time, with every automated
write funnelled through `with_engine_fields()`, which raises `PermissionError`
rather than overwriting.

That is now the single most important property in the codebase, and it exists
because the second revision was reviewed as a whole rather than as a list of
additions.

Fourteen ADRs record the rest.

### What was harder than expected

**Deciding what to do about unverifiable source URLs.** The build environment
blocks `*.microsoft.com`, so none of the candidate feeds could be tested. The
temptation was to pin the URLs anyway — they are almost certainly correct, and
`sources: []` looks unfinished.

Rejected, on the grounds that a pinned unverified URL is indistinguishable from a
validated one. Six months from now nobody reading `sources:` would know which
entries were actually tested. The candidates went into comments instead, where
they cannot be mistaken for configuration.

**Choosing what `ke validate` should require.** Requiring every metadata field,
even when null, felt heavy-handed. The argument that won: the engine always
writes the full field set, so a missing key means truncation or a bad hand-edit —
exactly what a validator should catch. Optional fields would have made the
validator quieter and less useful.

**The registry counter asymmetry.** A counter *behind* the highest sequence in
use is a future duplicate-ID bug. A counter *ahead* is completely harmless,
because IDs are never reused. Getting that asymmetry right took more thought than
it looks like, and it needed a test
(`test_a_counter_ahead_of_disk_is_allowed`) specifically to stop a future
contributor "fixing" it.

### Lessons learned

**Make invalid states unrepresentable rather than checking for them.**
`ObjectStatus` has no `deleted` member. There is no code path to audit and no rule
to remember, because the state cannot be expressed. This turned out to be
cheaper and stronger than any validation would have been.

**Import-time assertions are underrated.** The three lines proving the ownership
classes are disjoint mean an overlapping-field mistake breaks the package import
— caught on the next test run rather than at 3am on a Sunday when the cron
corrupts a file. Cost: three lines.

**Test the checker with something other than the writer.** `conftest.py` builds
knowledge objects with `yaml.safe_dump` directly rather than through engine code.
If a future `store.py` writes `difficulty: Intermediate`, a writer-based test
would produce that same wrong file and pass. Independent construction breaks the
loop. This was a deliberate choice documented in the file's own docstring, and it
is the kind of thing that is very hard to retrofit.

**Enforce a rule before the code that could break it exists.** The CI guard
against `ke generate` in a scheduled workflow was added in M0, though the weekly
workflow arrives in M6. Writing it later would mean writing it *after* the
temptation to break it appears.

**Architecture review before implementation paid for itself.** Two revision
cycles cost time and produced the field ownership model, which would have been
far more expensive to add once the pipeline was writing files.

### What was deliberately deferred

| Deferred | To | Why |
|---|---|---|
| Graph validation (referential integrity, cycles) | M4 | Nothing populates relationships yet; validating an always-empty field proves nothing |
| `--strict` in CI | After M2 | No objects exist, so no warnings exist to hold a line against |
| Linter / formatter | Any time | A tooling choice worth making deliberately rather than assuming |
| Single-sourcing the package version | Later | Real but small; `importlib.metadata` adds indirection for one duplicated string |
| `feedparser`, `requests` | M1 | Unused dependencies cost CI time and supply-chain surface |

### Open questions for M1

**The Fabric/Power BI boundary is the important one.** The two blogs overlap
heavily. If Power BI content appearing in the Fabric feed gets an `MSF` ID, and
the Power BI pack later ingests the same announcement as `PBI`, the result is
permanent cross-pack duplicates — permanent because Feature IDs never change.
This must be decided **before** M1 mints anything.

**Whether the docs repository is the better primary source.**
`MicrosoftDocs/fabric-docs` is public, structured, dated, and requires no
scraping. It may prove more reliable than the RSS feeds. Worth validating
alongside them.

**What happens when a source stops emitting dates.** The `inferred` fallback
handles it, but a feed that silently drops dates would quietly shift every ID to
the discovery month. Worth a health check.

### Pre-merge architecture review

M0 was reviewed cold before merge, and the claims the code makes about itself
were tested rather than trusted. Five defects turned up, all reproduced:
subdirectories that Git cannot store, a "copy" that shared mutable state,
findings that collide across packs, one bad pack aborting all validation, and
required directories that vanish on clone. All are fixed; the record is in
`docs/reviews/M0_ARCHITECTURE_REVIEW.md`.

**The lesson is one line: every defect was invisible in the state M0 ships in.**
One pack, zero objects, nothing committed. The suite was strong and tested the
world as it is today rather than as M2 and M8 will make it. Two of the five only
appeared when a second pack existed; one only appeared after an actual
commit-and-clone cycle.

The standing `two_packs` fixture closes half that gap. The other half — nothing
exercises a real commit and clone — is worth remembering when M2 starts writing
objects for real.

The secondary lesson: **fixing a defect is a good time to look for its
siblings.** The fifth defect was the same mistake one level up, and it was only
found because fixing the fourth meant simulating a clone.

### Metrics

| | |
|---|---|
| Commits | 8 |
| Engine code | ~1,100 lines |
| Test code | ~1,000 lines |
| Tests | 91, passing in 0.6s |
| Validation checks | 25 |
| ADRs | 14 |
| Runtime dependencies | 1 |
| Estimated monthly CI cost | ~0 minutes of 2,000 |

---

## M1 — Discovery

*Not started. Begins after M0 is reviewed and merged.*
