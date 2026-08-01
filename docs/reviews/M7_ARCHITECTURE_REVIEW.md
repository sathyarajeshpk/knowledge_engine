# M7 — Architecture Review

**Milestone:** M7 — Retrieval and on-demand generation
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect

---

## The shape of this milestone

Six milestones built a machine for getting knowledge **in**. M7 is the first one
about getting it **out**, and that turns out to be a different kind of work:
almost no new state, almost no new invariants, and a great deal of care about
what the engine is allowed to *not* do.

| | Through M6 | M7 |
|---|---|---|
| Direction | Sources → repository | Repository → you → a model → repository |
| New stored state | Every milestone | **One field group** (`generation`) that already existed in the schema |
| AI involvement | None, anywhere | **On demand, outside the engine, with a human in the loop** |
| New commands | `harvest`, `review`, `history` | `search`, `get`, `generate`, `status` |

`schema_version` stays at 1. The `generation` block has been in the schema since
M0 and was never written until now — which is the first time a piece of
speculative design in this project has paid off rather than needing removal
(compare `Lifecycle.SUPERSEDED`, deleted in M5).

## The three decisions worth defending

### 1. Context packs, not API calls (ADR-0040)

The obvious implementation of `ke generate` calls a model. One command, no
copy-paste. It is what comparable tools do, and it would have undone most of what
the previous six milestones were for.

What the chosen design keeps: zero running cost permanently, vendor independence
that is real rather than adapter-shaped, and — the part I would defend hardest —
**a human reading every artifact before it is stored**.

Everything generated here is plausible-sounding prose about a technical subject.
That is precisely the category where a wrong answer is hardest to spot and most
expensive to act on. An automated path would produce a repository slowly filling
with confident errors nobody had reason to doubt. The copy-paste step is not
friction awaiting optimisation; it is the quality control.

What it costs is real and is stated in the ADR: it is manual, there is no batch
mode, and the prompts cannot exploit any particular model's strengths.

### 2. Search scans rather than indexes (ADR-0041)

No inverted index, no cache, no `search.json`. Every query reads the objects.

The performance argument (222 objects, milliseconds) is not the real one. The
real one is that **a derived structure that can disagree with its source is a bug
waiting for a quiet week**, and this repository has already produced two of them:
the 222 orphaned `feature.md` files in M2 and the registry path mismatch in M3.
Both were two things that should have agreed, not agreeing.

The ADR is explicit that this reverses at scale, and equally explicit that the
reversal is a *different* design — a cache thrown away whenever it might be stale
— to be decided when there is evidence rather than in anticipation.

### 3. Artifact content is user-owned; its tracking is not (ADR-0042)

The sharpest point in the field-ownership model so far, because `--attach` writes
to both sides of it in one command.

The split is along the line between the thing and the bookkeeping about the
thing. It is what makes `generated_from_revision` trustworthy: the engine knows
which revision an artifact came from because it recorded that at the moment of
attachment, not because it inferred it later from a file it does not understand
and must not parse.

Conflating them would have cost either the ability to hand-edit an artifact
safely, or the ability to compute staleness at all.

## What the architecture absorbed without changing

**The pipeline did not change at all.** Eleven stages, same order. `ke generate`
is not a stage and cannot be — a new boundary test asserts that `pipeline.py`
does not import `ke.generate` or `ke.attach`. The workflow check catches
`ke generate` being *invoked* on a schedule; this catches the subtler version
where a stage imports it directly and generates inside the harvest, which would
reintroduce cost and vendor dependency without touching the workflow at all.

`ke.artifacts` is deliberately *not* on that forbidden list. Counting artifacts
and reporting staleness is exactly what the scheduled run should do; making them
is what it must not.

**The field-ownership model absorbed a new kind of thing** — a file rather than a
field — with one ADR and no code change to `with_engine_fields`.

**The indexer gained a document** by adding one entry to a dict.

Three milestones in a row now where new capability meant new leaf modules and no
structural change. I consider the core abstractions settled.

## Where the design bent

**`harvest.py` grew a second loader.** `load_existing_objects` returns each
object with a path *relative to `indexes/`* — a Markdown link target, not a
filesystem directory. M7 needed the directory, used the wrong one, and got
`TypeError: unsupported operand type(s) for /`.

That was the lucky version of the mistake. A string that happened to be a valid
relative path would have written objects somewhere else entirely, silently.

The fix is `load_objects_with_dirs` alongside it, one shared walk underneath, and
a docstring on each naming the other. The honest assessment is that the original
function was misnamed — it returns a link, not a path — and renaming it now would
touch six call sites for a clarity gain I judged smaller than the churn. Recorded
as **TD-12**.

**`models.py` gained a property that used to be a method.** `stale_artifacts` sat
in a block of `@property` accessors with no decorator. Because a bound method is
always truthy, `if obj.stale_artifacts:` was silently true for all 222 stored
objects, none of which have artifacts. The first code to read it got this wrong
immediately.

This is a small change with a general lesson: **a zero-argument accessor that
looks like a call is a trap whose failure mode is silent**. Worth watching for
elsewhere.

## The recurring lesson, in a new place

The standing note — *a successful pipeline execution does not guarantee correct
output* — held for a seventh milestone, and this time it moved outward again.

M6's version was "the tests pass but the test was worthless". M7's is **"the
tests pass but they are testing the wrong artifact"**: every suite in this
repository runs against `engine/` on `sys.path`, and the prompt templates lived
one directory above the package. All seven loaded in every test and in every
manual check. In a real `pip install` **zero** shipped, and `ke generate` would
have failed for every artifact type — a command that was thoroughly tested and
completely broken.

`test_packaging.py` now installs the package into a throwaway virtualenv and
checks what actually shipped. It is slow by the standards of the rest of the
suite. That is the price of testing the thing that ships rather than the thing in
front of you.

The generalisable form is worth keeping: **the test environment is itself an
assumption, and assumptions in the test environment are invisible by
construction.**

## Assessment

| Dimension | Verdict |
|---|---|
| Schema stability | **Unchanged** — `generation` was already in the contract |
| Abstraction integrity | **Held** — no structural change absorbed four new commands |
| Coupling | **Low** — `retrieve`, `generate`, `attach`, `artifacts` are leaves |
| New boundaries | **One added and enforced** — the pipeline cannot reach generation |
| Operating cost | **Unchanged at zero** — no API calls anywhere, by construction |
| Vendor independence | **Strengthened** — this is the milestone that makes it usable rather than theoretical |
| Reversibility | **High** — deleting the four new modules returns the engine to M6 exactly |

M7 is the milestone where the project's central claim stops being architectural
and becomes something you can run. Everything before it was a bet that separating
knowledge from intelligence would pay off; `ke generate` is where the payoff is
collected, and it is collected by *not* building the integration everyone expects.
