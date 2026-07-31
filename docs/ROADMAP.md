# Roadmap

Two independent axes:

- **Milestones (M0–M9)** build the engine. Sequential — each depends on the last.
- **Domain Packs** are the knowledge. Pure data. Adding one requires no engine
  change once M8 has proven the abstraction.

Status legend: **done** · **in progress** · **planned**

---

## Milestones

| # | Milestone | Status | Delivers |
|---|---|---|---|
| M0 | Foundation, schema, guardrails | **done** | Schema contract, Feature IDs, field ownership, `ke validate`, CI |
| M1 | Discovery | **planned** | Source adapters, normalisation, `ke discover` |
| M2 | Identity, dedupe, storage | **planned** | ID minting, 3-layer dedupe, `ke harvest` |
| M3 | Classification and learning metadata | **planned** | Tier/priority/difficulty rules, indexes |
| M4 | Relationships and knowledge graph | **planned** | Prerequisite DAG, learning paths |
| M5 | Revisions, updates, staleness | **planned** | In-place updates, revision history |
| M6 | Weekly automation | **planned** | Digest, notifications, Sunday cron |
| M7 | Retrieval and on-demand generation | **planned** | `ke search`, context packs |
| M8 | Second pack (Power BI) | **planned** | Proof the engine is pack-agnostic |
| M9 | Hardening and split-readiness | **planned** | Migrations, runbook, extraction guide |

---

### M0 — Foundation, schema and guardrails · **done**

Ships no pipeline, by design. Builds only what is hard to retrofit once data
exists.

- [x] Installable `engine/ke` package, packaged from `engine/` for later extraction
- [x] Core models: `KnowledgeObject`, `FeatureId`, `Revision`, `GenerationEntry`, `RawItem`, `RunReport`
- [x] Field ownership registry, asserted at import time, enforced by `with_engine_fields()`
- [x] `docs/SCHEMA.md` — the full contract
- [x] Microsoft Fabric pack skeleton and state files
- [x] `ke validate` — 31 checks across structure, schema, identity, ownership, registry, copyright
- [x] CI: tests + validation + a guard that no scheduled workflow calls `ke generate`
- [x] 107 tests

**Known gap carried into M1:** no source URL is verified. The planning
environment blocked `*.microsoft.com`, so `sources: []` is empty with candidates
in comments.

---

### M1 — Discovery · **planned**

Fetch from real sources and print what was found. No writes.

- [ ] **Validate and pin every source URL** — *blocking, do this first*
- [ ] `sources/base.py` — Source protocol and registry
- [ ] `sources/rss.py` — RSS/Atom feeds
- [ ] `sources/sitemap.py`, `sources/github_docs.py` — sources without usable RSS
- [ ] `normalize.py` — canonical URL, HTML→text, date parsing with `exact`/`inferred` confidence
- [ ] `ke discover --dry-run`
- [ ] Per-source failure isolation and health reporting

**Open question to resolve here:** Fabric and Power BI blogs overlap heavily.
Decide whether Power BI content in the Fabric feed belongs to `MSF` or `PBI`
*before* IDs are minted — retrofitting means permanent duplicates.

---

### M2 — Identity, dedupe, storage · **planned**

- [ ] `ids.py` — date-based minting, per-month counters, publication/discovery fallback
- [ ] `dedupe.py` — URL hash, content fingerprint, near-duplicate Jaccard
- [ ] `store.py` (create path) — build the object directory, atomic writes
- [ ] `ke harvest`
- [ ] Idempotency: two consecutive harvests produce zero new objects
- [ ] Backfill with correctly dated historical IDs

---

### M3 — Classification and learning metadata · **planned**

- [ ] Rule sets in `pack.yml` for tier, learning priority, category, difficulty, workload
- [ ] `classify.py` — unmatched → tier 3 + `needs_review`, never a silent guess
- [ ] `reading_time` from word count; `version` from release-wave patterns
- [ ] Ownership-aware writes honouring `overrides`
- [ ] `indexer.py` — `INDEX`, `by-tier`, `by-category`, `by-learning-priority`, `by-difficulty`, `by-source`, `review-queue`

---

### M4 — Relationships and knowledge graph · **planned**

- [ ] `graph.py` — referential integrity, DAG cycle detection
- [ ] Inverse consistency for `replaced_by`/`replaces`
- [ ] `learning-path.md` — topological sort of the prerequisite graph
- [ ] Relationship *proposals* into `review-queue.md` (never auto-written)
- [ ] `ke link` for safe manual curation

---

### M5 — Revisions, in-place updates, staleness · **planned**

Must land before the cron, because every run after the first is an update run.

- [ ] `revisions.py` — change detection via `content_hash`
- [ ] Append revision entries only on real change
- [ ] `store.py` (update path) — merge engine fields, preserve user fields byte-for-byte
- [ ] Staleness marking for generated artifacts
- [ ] `ke supersede`
- [ ] **Preservation test** — hand-edited learning state survives a forced source change

---

### M6 — Weekly automation · **planned**

- [ ] `digest.py` — one digest per ISO week
- [ ] `notify/base.py` — pluggable Notifier protocol
- [ ] `notify/github_issue.py` — durable audit trail, no secrets needed
- [ ] `notify/smtp_email.py` — inbox copy; failure must not fail the run
- [ ] Sunday cron with a `concurrency` group and `git pull --rebase`
- [ ] **Always append to `run-log.md`**, so a commit lands weekly and GitHub cannot disable the cron after 60 quiet days

---

### M7 — Retrieval and on-demand generation · **planned**

- [ ] `retrieve.py` — `ke search`, `ke get`, filters
- [ ] Seven prompt templates, each versioned
- [ ] `generate.py` — context packs including prerequisites
- [ ] `--attach` — write artifacts and update the generation block
- [ ] `ke status` + `generation-status.md`

---

### M8 — Second pack · **planned**

- [ ] `domain-packs/power-bi/` — **data only**
- [ ] Workflow loops over all discovered packs
- [ ] Cross-pack relationships
- [ ] `docs/ADDING-A-PACK.md`
- [ ] **Acceptance: `git diff engine/` is empty**

---

### M9 — Hardening and split-readiness · **planned**

- [ ] `ke migrate` — `schema_version` upgrades
- [ ] `ke review` — work through the review queue
- [ ] `docs/SPLITTING-REPOS.md`
- [ ] `docs/RUNBOOK.md` — re-enable a disabled cron, rotate secrets, repair state
- [ ] Tighten CI to `--strict`

---

## Domain Packs

Each pack is a directory under `domain-packs/`. The prefix is **permanent** —
changing it would orphan every Feature ID ever minted.

| Pack | Prefix | Status | Notes |
|---|---|---|---|
| Microsoft Fabric | `MSF` | **in progress** | First pack. Skeleton exists; sources land in M1. |
| Power BI | `PBI` | **planned** (M8) | The abstraction proof. Heavy source overlap with Fabric — boundary must be decided in M1. |
| SQL | `SQL` | **planned** | Slower-moving. More evergreen concepts than release news. |
| Python | `PY` | **planned** | PEPs, release notes, major library changes. |
| Azure | `AZ` | **planned** | Very high volume. Will need aggressive source scoping. |
| Databricks | `DBX` | **planned** | Overlaps Fabric on Spark and Delta concepts. |
| Snowflake | `SNF` | **planned** | Good cross-pack test — a genuine competitor to Fabric. |
| AWS | `AWS` | **planned** | Highest volume of all. Scope to services actually used. |
| Personal Knowledge | `PKB` | **planned** | No external sources. Manual entry only — exercises the engine's non-discovery paths. |

### Pack rollout principle

Do not add a pack until the previous one is genuinely useful. Nine
half-maintained packs are worth less than two good ones, and the weekly digest
becomes noise the moment it stops being read.

**Personal Knowledge is the interesting one.** It has no feeds, so it exercises
the parts of the engine that have nothing to do with discovery — identity,
relationships, learning state, generation. If the engine works with zero sources
configured, the separation of concerns is real.

---

## Cross-cutting work

| Item | When | Why |
|---|---|---|
| Tighten CI to `--strict` | After M2 populates the first pack | Warnings must not accumulate unnoticed |
| Add a linter (Ruff) | Any time | Cheap consistency; deliberately not chosen unilaterally |
| Cross-pack relationships | M8 | A Fabric concept declaring a SQL prerequisite |
| Extract `engine/` to its own repo | After M9 | Already structurally ready; a folder move |

---

## Explicit non-goals

- **A web UI.** GitHub Pages is unavailable for private repos on the Free plan. Retrieval is repo-native.
- **A database.** Files in Git are the source of truth.
- **Embeddings or a vector store.** `grep` and generated indexes first; revisit only if they genuinely fail.
- **Any AI call in the scheduled pipeline.** Enforced by CI since M0.
- **Automatic content generation.** Artifacts are produced when you ask, never on a schedule.
