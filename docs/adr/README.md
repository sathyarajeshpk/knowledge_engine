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
| [0016](0016-single-fabric-pack-with-power-bi-as-category.md) | One Fabric pack, with Power BI as a first-class category | Accepted |
| [0017](0017-date-precision-separate-from-confidence.md) | `date_precision` separate from `date_confidence` | Accepted |
| [0018](0018-provenance-and-uniform-adapter-interface.md) | Provenance on every item, behind one adapter interface | Accepted |
| [0019](0019-source-health-and-fallback.md) | Source health, fallback chains, and never failing silently | Accepted |
| [0020](0020-knowledge-time-machine.md) | Capture history for a Knowledge Time Machine | Accepted (amends 0009) |
| [0021](0021-injected-clock.md) | Time is injected, never read from the system clock | Accepted |
| [0022](0022-deterministic-output.md) | Same inputs produce byte-identical outputs | Accepted |
| [0023](0023-stable-item-identity.md) | A deterministic hierarchy for item identity | Accepted |
| [0024](0024-immutable-source-definitions.md) | Source definitions are immutable and versioned | Accepted |
| [0025](0025-design-for-replayability.md) | Design for replayability without building it yet | Accepted |
| [0026](0026-discovery-chain-provenance.md) | Record the full discovery chain; representation is not adapter | Accepted |
| [0027](0027-announcement-feature-knowledge-object.md) | Announcement, Feature and Knowledge Object are three things | Accepted |
| [0028](0028-identity-confidence.md) | Identity Confidence gates minting | Accepted |
| [0029](0029-knowledge-lifecycle.md) | Knowledge Lifecycle is separate from status | Accepted |
| [0030](0030-acquisition-subsystem.md) | Acquisition is a subsystem with an enforced boundary | Accepted |
| [0031](0031-harvest-ordering.md) | The order of pipeline stages is a safety property | Accepted |
| [0032](0032-state-failure-policies.md) | Each state file gets its own failure policy | Accepted |
| [0033](0033-update-scope.md) | An update refreshes a subset of engine-owned fields | Accepted |
| [0034](0034-classification-writes-once.md) | Classification proposes once and never churns | Accepted |

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
