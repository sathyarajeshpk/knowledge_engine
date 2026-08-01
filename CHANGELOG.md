# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Versioning policy

Two version numbers exist and move independently:

| Version | What it describes | Where |
|---|---|---|
| **Package version** | The engine's released behaviour | `pyproject.toml`, `ke.__version__` |
| **`SCHEMA_VERSION`** | The `metadata.yaml` file layout | `ke.SCHEMA_VERSION` |

The package version follows SemVer against the engine's public surface — the CLI
commands, their flags and exit codes, and the `ke` package API:

- **MAJOR** — a breaking change to the CLI contract or a `SCHEMA_VERSION` bump
  that requires migrating stored objects.
- **MINOR** — new commands or capabilities, backward compatible.
- **PATCH** — fixes and internal changes with no interface effect.

`SCHEMA_VERSION` changes **only** when the on-disk layout of `metadata.yaml`
changes. It is at `1` and is expected to stay there for most of M0–M9. Adding an
optional field does not bump it; removing or renaming one does, and requires a
migration in `ke migrate`.

While the project is pre-1.0, minor versions may contain breaking changes to the
CLI; the schema version is the stability guarantee that actually matters, because
it protects data rather than commands.

---

## [Unreleased]

Nothing yet. M1 (Discovery) begins after M0 is reviewed and merged.

---

## [0.4.0] — 2026-08-01

Fourth milestone: **M3 — The update path**.
Release notes: [`docs/releases/v0.4.0.md`](docs/releases/v0.4.0.md).

The engine can now revisit objects it already wrote **without destroying the work
their owner added by hand** — the promise ADR-0008 made in M0, tested for the
first time by deliberately trying to break it against production data.

**Schema version:** 1 (unchanged)

### Added

- `revisions.py` — change detection over engine-owned fields, with revisions
  appended only on material change. Reflowed whitespace is not a revision, and
  neither is `identity_confidence` moving on its own.
- `store.load_object` and `store.update_object` — read a stored object; rewrite
  only when the rendered bytes actually differ.
- The update stage in `harvest_pack`, plus `updated`/`unchanged` in the report,
  the CLI output and the run log.
- `test_regressions.py` — 11 tests, one per bug that reached a running system,
  each recording why the existing suite could not see it.
- `test_update_path.py` — 21 tests covering detection, preservation, revisions,
  ID permanence and multi-run idempotency.
- [ADR-0033](docs/adr/0033-update-scope.md) — an update refreshes a subset of
  engine-owned fields, not all of them.

### Fixed

- Two sources reporting the same feature both ran the update, so the object
  flipped between their renderings twice per harvest — 70 phantom "updates" on
  an unchanged run and a permanently dirty git diff. Now one sighting per
  identity.
- `update_object`'s byte-comparison was unreachable from the pipeline and
  therefore untested; deleting it left every test green. It now has a direct
  test, and the end-to-end test states which guard it actually exercises.

## [0.3.0] — 2026-08-01

Third milestone: **M2 — Identity, dedupe, storage and the first working
pipeline**. Release notes: [`docs/releases/v0.3.0.md`](docs/releases/v0.3.0.md).

**The engine now produces knowledge.** `ke harvest` runs discover → dedupe →
gate → mint → store → index in one pass and, against the live Microsoft sources,
produced 222 knowledge objects. A second harvest mints nothing and leaves the ID
registry byte-identical.

**Schema version:** 1 (unchanged)

### Added

- `ids.py` — date-based Feature ID minting with per-month counters and a
  registry that validates its own consistency on load.
- `dedupe.py` — three layers: identity key, content fingerprint, near-duplicate
  Jaccard. The first two resolve silently; the third only flags
  ([ADR-0014](docs/adr/0014-flag-near-duplicates-never-drop.md)).
- `store.py` — object directories, atomic paired writes, deterministic bytes.
- `review.py` and `ke review list|approve|archive` — the supported way to drain
  the queue. An approved item mints under its **original** discovery date.
- `indexer.py` — `INDEX.md`, `by-source.md`, `by-month.md`, `review-queue.md`,
  fully rebuilt every run.
- `harvest.py` and `ke harvest` — the pipeline.
- `ke index` — rebuild indexes without harvesting.
- `KnowledgeObject.announcement_url` and `.identity_confidence`, documented in
  `SCHEMA.md` since v0.2.0 and added before any object existed.
- [ADR-0031](docs/adr/0031-harvest-ordering.md) — stage ordering is a safety
  property. [ADR-0032](docs/adr/0032-state-failure-policies.md) — each state file
  gets its own failure policy.

### Changed

- `TRACKING_PARAMS` moved from `ke.acquisition.identity` to `ke.normalize`. Core
  must not import the acquisition subsystem, and the previous arrangement was a
  real import cycle that surfaced only for certain entry points. Guarded by a
  mirror-image boundary test and a cold-start import test.
- `metadata.yaml` is written with YAML aliases disabled — PyYAML emitted
  `&id001` anchors where a date is shared between `discovered_date` and the
  revision recording it, and the file is read by humans in the GitHub UI.

### Fixed

- `write_object` wrote `feature.md` before `metadata.yaml`, so a failure between
  them left half an object at a permanent-looking path. It did: the first run
  produced 222 orphaned `feature.md` files. Both documents are now rendered
  before either is written.
- The ID registry recorded object paths relative to the pack root while
  `ke validate` expects them relative to `knowledge/`, failing every object. Now
  uses `KnowledgeObject.knowledge_subpath`, the form the validator checks.
- `ke review approve` could not accept the key printed in `review-queue.md`: the
  report strips the `sha256:` prefix and the lookup did not, making the
  documented workflow impossible.

## [0.2.0] — 2026-08-01

Second milestone: **M1 — Discovery**.
Release notes: [`docs/releases/v0.2.0.md`](docs/releases/v0.2.0.md).

Discovery against live sources, with identity graded before anything can become
permanent. **Still writes nothing** — no knowledge objects, no Feature IDs. That
is what made it affordable to change the architecture twice on measurement.

**Schema version:** 1 (unchanged)

### Added

**Acquisition subsystem** (`ke.acquisition`, [ADR-0030](docs/adr/0030-acquisition-subsystem.md))
- Three adapters behind one interface — `html_table` (primary), `markdown_table`
  (secondary), `feed` (RSS/Atom) — so nothing downstream learns where knowledge
  came from.
- Fallback chains with per-source failure isolation: a failed source never fails
  the run ([ADR-0019](docs/adr/0019-source-health-and-fallback.md)).
- Source health: `healthy`/`degraded`/`failed`/`disabled`, with median-based
  parser-break detection.
- Boundary enforced by import scanning in `engine/tests/test_architecture.py`,
  against a forbidden list naming modules that do not exist yet.

**Identity and confidence**
- Four-level identity hierarchy ([ADR-0023](docs/adr/0023-stable-item-identity.md)).
- Announcement / Feature / Knowledge Object as three distinct concepts
  ([ADR-0027](docs/adr/0027-announcement-feature-knowledge-object.md)). A
  collision is surfaced for review, never merged.
- `IdentityConfidence` (`high`/`medium`/`low`) gating minting
  ([ADR-0028](docs/adr/0028-identity-confidence.md)). Identity is permanent and
  run-independent; confidence is a per-run judgement that never enters the ID.
- `Lifecycle` — `discovered → queued → approved → minted → superseded →
  archived` — orthogonal to `status`
  ([ADR-0029](docs/adr/0029-knowledge-lifecycle.md)).

**Infrastructure**
- Injected `Clock` ([ADR-0021](docs/adr/0021-injected-clock.md)) and `Fetcher`,
  making every adapter testable offline and a run replayable.
- Pure normalisation: canonical URLs, HTML→text, date parsing with precision and
  confidence, summary truncation enforcing the copyright rule.
- `ke discover`, reporting items, confidence, collisions and source health.
- `feedparser` runtime dependency.
- Four diagnostic probes under `tools/`.

### Changed

- `date_precision` separated from `date_confidence`
  ([ADR-0017](docs/adr/0017-date-precision-separate-from-confidence.md)).
- Provenance stored in discovery-chain order, with `source_representation`
  distinct from `adapter_type`
  ([ADR-0026](docs/adr/0026-discovery-chain-provenance.md)).
- `ItemIdentity` and `IdentityBasis` moved from `ke.identity` to `ke.models`, so
  core types do not depend on the subsystem that computes them.
- `ke.identity`, `ke.confidence`, `ke.discover` and `ke.sources` moved under
  `ke.acquisition`.
- `ke review` promoted from M9 to M2: once items are queued there must be a
  supported way to process them.

### Fixed

- Publication dates were read from anywhere in a table row, so a month mentioned
  in prose ("the Gateway December 2025 release") was labelled `exact` and would
  have minted a permanent Feature ID from it. Only a dedicated date cell is
  trusted now. Affected 1 row in 361 on the live page.
- `status: disabled` was ignored on fallback links — `_discover_chain` checked
  `is_pollable` only for top-level sources, so a deliberately retired source
  would still be polled on the next failure.

## [0.1.0] — 2026-07-31

First milestone: **M0 — Foundation, Schema and Guardrails**.
Release notes: [`docs/releases/v0.1.0.md`](docs/releases/v0.1.0.md).

Ships no pipeline by design. M0 builds only the things that are difficult or
impossible to retrofit once knowledge objects exist on disk: the schema, the
identity rules, the field ownership model, the validator and CI.

**Schema version:** 1

### Added

**Engine package**
- `engine/ke` as an installable package, packaged from `engine/` via
  `package-dir` so it can later be extracted to its own repository without moving
  files ([ADR-0011](docs/adr/0011-monorepo-engineered-to-split.md)).
- `ke` console script and `python -m ke` entry point.
- Runtime dependency on PyYAML only; `feedparser` and `requests` arrive in M1
  with the code that needs them.

**Data models** (`ke.models`)
- `KnowledgeObject` with ~30 fields covering identity, provenance,
  classification, learning metadata, relationships, lifecycle and generation
  tracking.
- `FeatureId` — date-based, permanent, sortable identity in the form
  `MSF-2026-04-001`, with per-month counters and 4-digit overflow
  ([ADR-0005](docs/adr/0005-date-based-feature-ids.md)).
- **Field ownership registry** partitioning every metadata field into
  engine-owned, engine-proposed and user-owned, asserted at import time and
  enforced by `KnowledgeObject.with_engine_fields()`, which raises
  `PermissionError` rather than overwriting user work
  ([ADR-0008](docs/adr/0008-field-ownership-model.md)).
- `Revision`, `GenerationEntry` with computed staleness, `RawItem`,
  `SourceHealth`, `RunReport`.
- Ten controlled vocabularies as enums. `ObjectStatus` deliberately has no
  `deleted` member.

**Domain Pack support** (`ke.pack`)
- `Pack.discover()` finds packs by scanning `domain-packs/`; no pack is named
  anywhere in the engine.
- `Pack.load()`, `iter_object_dirs()`, `find_repo_root()`, and configuration
  accessors with backward-compatible defaults.

**Validation** (`ke.validate`)
- `ke validate` with 31 checks across pack structure, metadata schema, Feature ID
  integrity, field ownership, file consistency, the copyright word limit and the
  ID registry.
- Findings-based reporting with stable codes and error/warning severity
  ([ADR-0012](docs/adr/0012-findings-over-exceptions.md)).
- `--pack`, `--repo-root` and `--strict` flags.

**First Domain Pack**
- `domain-packs/microsoft-fabric/` skeleton: `pack.yml`, directory structure and
  empty state files. `sources` is intentionally empty — see Known Limitations.

**Documentation**
- `docs/SCHEMA.md` — the contract `ke validate` enforces.
- `docs/VISION.md`, `docs/ROADMAP.md`, `docs/JOURNAL.md`.
- `docs/playbook/M0_FOUNDATION.md` — file-by-file developer guide with
  architecture and sequence diagrams.
- `docs/learning/M0_LEARNING_GUIDE.md` — the underlying Python and tooling
  concepts.
- `docs/adr/` — 15 Architecture Decision Records.
- `CONTRIBUTING.md`, and a rewritten `README.md`.

**Automation**
- CI running tests and validation on every push and pull request.
- A CI guard that fails the build if any scheduled workflow invokes
  `ke generate`, enforcing [ADR-0004](docs/adr/0004-no-ai-in-the-scheduled-pipeline.md)
  before the weekly workflow it constrains exists.

**Tests**
- 107 tests across models, pack loading, validation and the CLI. Suite runs in
  under one second with no network access.

### Changed

- `CLAUDE.md` gained five clarifications, each backed by a mechanism: corrections
  are revisions not rewrites; the scheduled pipeline never calls an AI model; the
  engine never writes user-owned fields; third-party article text is never
  stored; Feature IDs and object paths are permanent. Also records the
  per-milestone development workflow.
- `README.md` rewritten from a one-line placeholder.

### Fixed

Five defects found by the pre-merge architecture review
([`docs/reviews/M0_ARCHITECTURE_REVIEW.md`](docs/reviews/M0_ARCHITECTURE_REVIEW.md)),
each reproduced before being fixed. All were latent — none broke M0, which has no
data yet — and all would have surfaced in M2, M5 or M8.

- **Object and pack subdirectories could not survive Git.** Empty directories are
  not tracked, so `artifacts/`, `images/`, `references/`, `indexes/` and
  `digests/` vanished on every clone. They are now created on demand
  ([ADR-0015](docs/adr/0015-create-object-subdirectories-on-demand.md)), the
  `OBJ005` check is retired, and `GEN001`–`GEN003` check artifacts that actually
  exist instead of empty scaffolding.
- **`with_engine_fields()` returned a shallow copy**, sharing the `generation`
  dict with the original and contradicting its own docstring. It now shares no
  mutable state, guarded by a test that walks every field so a future mutable
  field cannot silently alias.
- **Finding locations were pack-relative**, so two packs produced identical,
  ambiguous output that the reporter then merged into one group. They are now
  repository-relative and globally unique.
- **One malformed `pack.yml` aborted validation of every pack.** Unloadable packs
  are now reported as `PACK005` and the remaining packs are still validated.
- **`PACK004` required directories Git cannot store**, so the pack-creation
  recipe in `CONTRIBUTING.md` produced a pack that failed validation after a
  clone. Only `state/` is required now.

### Removed

- `domain-packs/microsoft-fabric/.gitkeep` — redundant once `pack.yml` keeps the
  directory tracked. No knowledge was affected.
- `.gitkeep` files in `knowledge/`, `indexes/` and `digests/` — they were
  propping up a requirement that no longer exists.

### Known limitations

- **No source URL is verified.** The environment M0 was built in blocks
  `*.microsoft.com`, so `sources: []` is empty with candidate feeds recorded as
  comments. A pinned unverified URL is indistinguishable from a validated one.
  Validating each endpoint is M1's first and blocking task.
- **Graph validation is absent.** Referential integrity and prerequisite cycle
  detection land in M4. M0 validates only the schema of relationship fields, so a
  `prerequisites` entry pointing at a non-existent ID currently passes.
- **CI is not `--strict`.** Warnings pass. This tightens once M2 populates the
  first pack; a fresh clone now validates cleanly under `--strict`, which the
  subdirectory fix made possible.
- **No linter or formatter** is configured.
- **The package version is declared in two places** — `pyproject.toml` and
  `ke.__init__`. A candidate for `importlib.metadata` in a later milestone.

[Unreleased]: https://github.com/sathyarajeshpk/knowledge_engine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sathyarajeshpk/knowledge_engine/releases/tag/v0.1.0
