# ADR-0006: One directory per knowledge object

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

A knowledge object starts as a short summary and a link. Over time it may
accumulate: a tutorial, interview questions, a LinkedIn post, a quiz, coding
examples, an architecture explanation, infographics, diagrams, and supporting
reference notes.

Most objects will never gain any of these. A minority will gain several,
including binary images and multi-file code examples.

The storage layout must answer: where do those artifacts live?

## Decision

**Every knowledge object is a directory, from the moment it is created**, whether
or not it has artifacts:

```
knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
├── feature.md      canonical knowledge article
├── metadata.yaml   structured metadata
├── artifacts/      tutorials, posts, quizzes, code examples
├── images/         infographics, diagrams, thumbnails
└── references/     supporting notes and additional references
```

**The directory path is stable for the object's entire lifetime.** It is
`knowledge/<YYYY>/<MM>/<feature-id>-<slug>/`, derived from the permanent Feature
ID.

The three subdirectories are created up front, so attaching the first artifact
never has to create structure or move anything.

## Consequences

### Positive
- **Path stability.** Every index entry, digest link, relationship reference and
  bookmark stays valid forever. This is the property that motivated the whole
  decision.
- **One code path.** No "is this an object or a directory?" branch anywhere, no
  promotion logic to write, test and maintain forever.
- **Binaries and multi-file artifacts work naturally.** An infographic PNG and a
  three-file code example need no special handling.
- **Everything about one object is in one place** — you can `ls` it and see its
  complete state.
- **Artifacts are namespaced by construction**, so two objects can both have
  `artifacts/tutorial.md` with no collision.

### Negative
- **Mostly-empty directories.** An object with no artifacts still has three empty
  subdirectories. Git does not track empty directories, so they exist on disk but
  contribute nothing to the repository — the cost is close to zero, and where it
  matters (inode count on a very large pack) it is still trivial at our scale.
- **More filesystem entries**, which makes a recursive listing noisier.
- **`ke validate` must check for the subdirectories**, which it does as a
  *warning* (`OBJ005`) rather than an error, since they are trivially recreated.

### Neutral
- Object count is visible as directory count, which is convenient.
- `Pack.iter_object_dirs()` walks exactly three levels, which is simple and fast.

## Alternatives considered

**A flat file, promoted to a directory when the first artifact appears.**
Lighter for the common case. **Rejected — this is the important rejection.**
Promotion rewrites the object's path from
`.../MSF-2026-04-001-direct-lake-ga.md` to
`.../MSF-2026-04-001-direct-lake-ga/feature.md`, breaking every index entry,
digest link and external bookmark pointing at it. It also adds a migration code
path that must be maintained forever, and a class of bug that only appears the
first time a user generates an artifact. Saving an empty directory is not worth
losing path stability.

**A flat `knowledge/` tree plus a mirrored `artifacts/<feature-id>/` tree.**
Keeps the knowledge layer visually clean. Rejected: the object is split across
two locations that can drift, deleting or moving one half orphans the other, and
`ke validate` would need to reconcile two trees.

**All artifacts in one shared directory, named by ID.** Rejected: same drift
problem, plus a single directory with thousands of files.

**Artifacts as extra sections inside `feature.md`.** Rejected: cannot hold
binaries, makes the canonical article unreadable, and conflates curated knowledge
with generated content — which ADR-0008 keeps deliberately separate by ownership.
