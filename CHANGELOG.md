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

## [0.1.0] — 2026-07-31

First milestone: **M0 — Foundation, Schema and Guardrails**.

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
- `ke validate` with 25 checks across pack structure, metadata schema, Feature ID
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
- `docs/adr/` — 14 Architecture Decision Records.
- `CONTRIBUTING.md`, and a rewritten `README.md`.

**Automation**
- CI running tests and validation on every push and pull request.
- A CI guard that fails the build if any scheduled workflow invokes
  `ke generate`, enforcing [ADR-0004](docs/adr/0004-no-ai-in-the-scheduled-pipeline.md)
  before the weekly workflow it constrains exists.

**Tests**
- 91 tests across models, pack loading, validation and the CLI. Suite runs in
  under one second with no network access.

### Changed

- `CLAUDE.md` gained five clarifications, each backed by a mechanism: corrections
  are revisions not rewrites; the scheduled pipeline never calls an AI model; the
  engine never writes user-owned fields; third-party article text is never
  stored; Feature IDs and object paths are permanent. Also records the
  per-milestone development workflow.
- `README.md` rewritten from a one-line placeholder.

### Removed

- `domain-packs/microsoft-fabric/.gitkeep` — redundant once `pack.yml` keeps the
  directory tracked. No knowledge was affected.

### Known limitations

- **No source URL is verified.** The environment M0 was built in blocks
  `*.microsoft.com`, so `sources: []` is empty with candidate feeds recorded as
  comments. A pinned unverified URL is indistinguishable from a validated one.
  Validating each endpoint is M1's first and blocking task.
- **Graph validation is absent.** Referential integrity and prerequisite cycle
  detection land in M4. M0 validates only the schema of relationship fields, so a
  `prerequisites` entry pointing at a non-existent ID currently passes.
- **CI is not `--strict`.** Warnings pass. This tightens once M2 populates the
  first pack.
- **No linter or formatter** is configured.
- **The package version is declared in two places** — `pyproject.toml` and
  `ke.__init__`. A candidate for `importlib.metadata` in a later milestone.

[Unreleased]: https://github.com/sathyarajeshpk/knowledge_engine/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/sathyarajeshpk/knowledge_engine/releases/tag/v0.1.0
