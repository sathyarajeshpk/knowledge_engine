# ADR-0003: Markdown and YAML files instead of a database

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

Knowledge objects are structured records with ~30 fields, relationships between
them, and several access patterns: chronological listing, filtering by tier or
difficulty, graph traversal for prerequisites, and full-text search.

That description sounds like a database. `CLAUDE.md` says "avoid databases unless
absolutely necessary", which forces the question: is it necessary?

The realistic scale is a few thousand objects over several years. A single
Microsoft Fabric monthly update yields perhaps 20–40 items; nine packs at that
rate is low thousands per year at the outside.

## Decision

Store each knowledge object as **plain files**: `feature.md` for the article and
`metadata.yaml` for structured fields. Engine bookkeeping lives in small JSON
files under `state/`.

There is no database and no index server. Indexes are **regenerated Markdown**,
rebuilt in full from the source files on every run so they cannot drift. Search
is filesystem search.

## Consequences

### Positive
- **Human-readable at rest.** You can read a knowledge object in the GitHub UI,
  in an editor, or with `cat`, with no tooling and no schema knowledge.
- **Diffable.** A change to a knowledge object shows up as a readable diff. This
  is what makes revision history legible rather than merely present.
- **Hand-editable.** Learning state is maintained by a human. A database would
  require building a UI for that, which is a project in itself.
- **No query language, no migrations, no connection handling, no server.**
- **The knowledge outlives the engine.** Markdown and YAML will be readable in
  twenty years whether or not this Python code still runs. That is the durability
  goal, achieved by refusing complexity rather than by managing it.
- **Git becomes the transaction log** for free.

### Negative
- **No ad-hoc querying.** "All tier-1 items I haven't learned, published after
  March" means walking every file. At thousands of objects this is milliseconds;
  at millions it would not be.
- **No referential integrity from the storage layer.** `ke validate` has to
  provide it — and does, which is a large part of why that command exists.
- **Full scans for cross-object checks.** `_check_duplicate_ids` and
  `_check_registry` load every object. Acceptable and measured; would need
  revisiting at a scale we will not reach.
- **No concurrent writes.** Single-writer only, enforced by a workflow
  `concurrency` group.

### Neutral
- Pushes complexity from the storage layer into the validation layer. That is a
  deliberate trade: validation code is testable and readable; database migrations
  are neither.
- If querying ever genuinely fails us, the files remain the source of truth and
  building an index over them is an afternoon's work. The reverse — extracting
  clean files from a database — is much harder.

## Alternatives considered

**SQLite.** Zero-config, embedded, real SQL, no server. Genuinely tempting.
Rejected because committing a binary file to Git destroys diffs, hand-editing and
merge resolution — the three properties doing the most work here. A derived
SQLite index built at read time remains available later without changing the
source of truth.

**A single large JSON or YAML file.** Simple, one parse. Rejected: every write
rewrites the whole file, so every change is a whole-file diff and every
concurrent edit is a conflict. Also unreadable past a few hundred entries.

**Front matter in one Markdown file per object** (metadata embedded at the top,
no separate `metadata.yaml`). Very common, one fewer file. Rejected in ADR-0007
— see that record for the reasoning.

**A document database (MongoDB, CouchDB).** Flexible schema, real queries.
Rejected: a server, a cost, a dependency, and non-portable storage. Fails cost
and durability at once.

**A vector database with embeddings.** Would enable semantic search. Rejected for
now: adds a service, a cost, and a model dependency — the last being precisely
what this project is built to avoid. `grep` plus generated indexes first; revisit
only if they genuinely fail.
