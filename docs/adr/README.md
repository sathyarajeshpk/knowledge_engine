# Architecture Decision Records

An ADR records **one significant decision**: the situation that forced it, what
was chosen, what it costs, and what was rejected.

They exist so that a contributor arriving in two years can tell the difference
between a deliberate constraint and an accident — and so that reversing a
decision is a conscious act rather than a tidy-up.

## Rules

- **ADRs are immutable once accepted.** New information means a new ADR that
  supersedes the old one. Never rewrite history; mark it.
- **One decision per record.** If it needs "and", it is two ADRs.
- **Record the rejected options.** The alternatives are usually more useful than
  the choice.
- **Number sequentially**, never reuse a number.

## Status values

| Status | Meaning |
|---|---|
| `Proposed` | Under discussion |
| `Accepted` | In force |
| `Superseded by ADR-NNNN` | Replaced; kept for the record |
| `Deprecated` | No longer applies, nothing replaced it |

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-github-as-the-storage-layer.md) | GitHub as the storage layer and single source of truth | Accepted |
| [0003](0003-markdown-files-instead-of-a-database.md) | Markdown and YAML files instead of a database | Accepted |
| [0004](0004-no-ai-in-the-scheduled-pipeline.md) | No AI model in the scheduled pipeline | Accepted |
| [0005](0005-date-based-feature-ids.md) | Date-based Feature IDs with per-month counters | Accepted |
| [0006](0006-directory-per-knowledge-object.md) | One directory per knowledge object | Accepted |
| [0007](0007-separate-feature-and-metadata-files.md) | Separate `feature.md` and `metadata.yaml` | Accepted |
| [0008](0008-field-ownership-model.md) | Field ownership model | Accepted |
| [0009](0009-update-in-place-with-revisions.md) | Update in place with a revision history | Accepted |
| [0010](0010-three-classification-axes.md) | Three independent classification axes | Accepted |
| [0011](0011-monorepo-engineered-to-split.md) | Single repository, engineered to split | Accepted |
| [0012](0012-findings-over-exceptions.md) | Validation returns findings rather than raising | Accepted |
| [0013](0013-pluggable-notifiers.md) | Pluggable notifier interface | Accepted |
| [0014](0014-flag-near-duplicates-never-drop.md) | Flag near-duplicates, never drop them | Accepted |
| [0015](0015-create-object-subdirectories-on-demand.md) | Create object and pack subdirectories on demand | Accepted (amends 0006) |

## Template

```markdown
# ADR-NNNN: Title

**Status:** Proposed | Accepted | Superseded by ADR-NNNN
**Date:** YYYY-MM-DD
**Milestone:** M0

## Context
The situation and the forces at play. What made a decision necessary.

## Decision
What we chose. Stated plainly, in the active voice.

## Consequences
### Positive
### Negative
### Neutral

## Alternatives considered
What was rejected and why.
```
