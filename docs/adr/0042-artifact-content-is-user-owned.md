# ADR-0042: Artifact content is user-owned; only its tracking is not

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M7

## Context

`ke generate --attach` writes two things into the same object directory:

```
knowledge/2026/05/MSF-2026-05-029-.../
├── artifacts/tutorial.md      the artifact
└── metadata.yaml              ... containing a `generation:` block about it
```

ADR-0008 divides every field into engine-owned, engine-proposed and user-owned.
An artifact is a new kind of thing — a file rather than a field, produced by a
model rather than a source or a person — and it needs placing in that model
before anything writes one.

The question has a real edge. If artifacts are engine-owned, the weekly harvest
could rewrite a tutorial you spent an hour correcting. If the `generation` block
is user-owned, the engine cannot compute staleness, and the Time Machine loses
its most useful application.

## Decision

**Split them, along the line between the thing and the bookkeeping about the
thing.**

| | Owner | Written by |
|---|---|---|
| `artifacts/*`, `images/*` | **User** | `--attach`, once. Then never again by anything automated. |
| `generation:` block | **Engine** | `--attach`, `--request`, `ke status --refresh` |

The `generation` block records `status`, `path`, `generated_at`,
`generated_from_revision`, `model` and `prompt_version`. It contains no content
and describes rather than duplicates.

## Consequences

**Staleness is computable without reading your prose.** An artifact is stale
exactly when `generated_from_revision` is behind the object's current revision.
The engine knows which revision an artifact came from because it recorded it at
the moment of attachment — not because it inferred it later from a file it does
not understand and must not parse.

This is the whole reason for the split. Ownership of the *content* and knowledge
of its *provenance* are different things, and conflating them would have cost one
or the other.

**Editing an artifact is free and invisible.** Rewrite it, delete two thirds of
it, replace it with your own notes. Nothing checks, nothing complains, nothing
overwrites. The `generation` block still says which revision it started from,
which remains true.

**The engine will overwrite an artifact in exactly one circumstance:** a human
runs `--attach` again. Even then it refuses without `--force` when the existing
artifact is *current*. Regenerating something the source has outgrown is the
normal path; silently discarding something you have since edited is not.

**`model` is recorded and never read.** Nothing in the engine branches on it. A
pack with artifacts from four vendors behaves identically to one from a single
vendor. It exists so that when something reads oddly in six months you can see
what produced it. Recording it is not a dependency; reading it would be.

**Stale artifacts are never deleted.** They are marked. CLAUDE.md forbids
deleting knowledge, and a tutorial written against an older revision is still a
tutorial — often still a correct one, since most revisions are a corrected date
or a reworded sentence.

**A hand-written artifact is indistinguishable from a generated one**, and that
is deliberate. If you write a tutorial yourself and attach it, the engine tracks
it the same way. There is no "authored by AI" flag because there is no behaviour
that should depend on one.

## Alternatives considered

**Artifacts fully engine-owned, regenerated automatically when stale.** Rejected
twice over: it would need an API call in the pipeline (ADR-0004, ADR-0040), and
it would destroy hand-edits — the exact failure the field-ownership model exists
to prevent. "The engine rewrote my corrected tutorial" is the same category of
harm as "the engine overwrote my notes".

**The whole `generation` block user-owned.** Rejected: the engine could then not
compute staleness, and `ke status --stale` — the only mechanism that surfaces
"you made this from knowledge that has since changed" — would not exist.

**Storing artifacts outside the object directory.** Rejected: the object's
directory is stable for its lifetime (ADR-0006), which makes it exactly the right
home for everything derived from it. Splitting them would create a second path to
keep in sync.

**Inferring `generated_from_revision` by comparing content hashes.** Rejected as
unreliable and as a boundary violation — it would require the engine to read and
reason about artifact content, which is the thing this ADR says it does not own.
