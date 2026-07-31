# Knowledge Object Schema

This is the contract between the engine and the knowledge it stores. It is
enforced by `ke validate`, which runs in CI on every push.

The files in a Domain Pack must remain useful without this engine — GitHub is
the source of truth, and a human reading `metadata.yaml` in the GitHub UI should
be able to understand it. That constraint shapes everything below.

---

## 1. The knowledge object

A knowledge object is a **directory**, not a file. Its path is stable for the
object's entire lifetime.

```
domain-packs/microsoft-fabric/knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
├── feature.md        canonical knowledge article (human-readable)
├── metadata.yaml     structured metadata (machine-readable)
├── artifacts/        tutorials, LinkedIn posts, interview questions, quizzes,
│                     coding examples, presentations
├── images/           infographics, diagrams, thumbnails
└── references/       supporting notes and additional references
```

The three subdirectories are **created on demand**, when something is first
written into them ([ADR-0015](adr/0015-create-object-subdirectories-on-demand.md)).
Git cannot track an empty directory, so creating them up front would mean they
vanished on every clone. A freshly harvested object contains exactly two files.

**Why a directory from day one.** The alternative — a flat file promoted to a
directory when its first artifact appears — would rewrite the object's path,
breaking every index entry, digest link and bookmark pointing at it. Promotion
costs path stability, which is worth more than the file it would save.

**Why knowledge and metadata are separate files.** `feature.md` stays clean for
reading, and tools can read `metadata.yaml` without parsing Markdown. The cost
is that the two can drift, so `ke validate` checks that they agree.

The directory name is always `<feature-id>-<slug>`.

---

## 2. Feature IDs

```
MSF-2026-04-001
│   │    │  └── sequence within that month, zero-padded to 3 digits
│   │    └───── month (01-12)
│   └────────── year
└────────────── pack prefix
```

| Rule | Detail |
|---|---|
| Month basis | The **publication** month when a reliable date was parsed; the **discovery** month otherwise. `date_confidence` records which. |
| Permanence | Once assigned, a Feature ID **never changes and is never reused** — including for objects later marked `replaced`. |
| Counters | Held per month in `state/id-registry.json`, so backfilling an old month mints correctly dated IDs without disturbing the current month. |
| Overflow | A month exceeding 999 objects widens the sequence to 4 digits. Existing IDs are **not** rewritten. |

Pack prefixes: `MSF` Microsoft Fabric, `PBI` Power BI, `SQL`, `PY` Python,
`DBX` Databricks, `AZ` Azure, `SNF` Snowflake, `AWS`, `PKB` Personal Knowledge.

A new Feature ID is minted only for a genuinely new feature or concept. An
updated source article revises the existing object — see §5.

---

## 3. Field ownership

This is the most important rule in the schema.

The weekly run is automated and rewrites files. The user maintains learning
state by hand in those same files. Without an explicit rule, the automation
would eventually destroy the user's work. So every field belongs to exactly one
ownership class, declared in `engine/ke/models.py` and asserted at import time.

| Class | Engine behaviour | Fields |
|---|---|---|
| **Engine-owned** | Rewritten freely on every run | `schema_version`, `id`, `slug`, `title`, `source_name`, `source_url`, `source_authority`, `published_date`, `discovered_date`, `date_confidence`, `content_hash`, `url_hash`, `reading_time`, `status`, `needs_review`, `revisions`, `generation` |
| **Engine-proposed** | Written **only** if absent, or if not named in `overrides` | `tier`, `learning_priority`, `category`, `tags`, `difficulty`, `workload`, `version` |
| **User-owned** | **Never written by the engine** | `learning_status`, `notes`, `prerequisites`, `builds_on`, `related_topics`, `replaced_by`, `replaces`, `overrides` |

Everything under `artifacts/`, `images/` and `references/` is user-owned. The
engine writes there only when you explicitly run `ke generate --attach`.

### Locking a proposed field

If you disagree with the engine's judgement, change the value and add the field
name to `overrides`:

```yaml
difficulty: advanced
overrides: [difficulty]
```

The engine will now leave `difficulty` alone forever. `ke validate` fails if
`overrides` names a field that is not engine-proposed — locking an engine-owned
field is meaningless, and locking a user-owned field is redundant.

Every automated write goes through `KnowledgeObject.with_engine_fields()`, which
raises `PermissionError` rather than writing a field it does not own.

---

## 4. `metadata.yaml`

```yaml
schema_version: 1

# --- Identity (engine-owned, immutable after minting) ---
id: MSF-2026-04-001
slug: direct-lake-ga
title: Direct Lake mode reaches general availability

# --- Provenance (engine-owned) ---
source_name: fabric-blog
source_url: https://blog.fabric.microsoft.com/...
source_authority: official-microsoft
published_date: 2026-04-15
discovered_date: 2026-08-03
date_confidence: exact          # do we trust it?   exact | inferred
date_precision: day             # how precise?      day | month | year
content_hash: sha256:...
url_hash: sha256:...

# Where this item came from and exactly how it was extracted (engine-owned).
# Written in discovery-chain order: source → representation → adapter →
# version → method → time → identity. See §10.
provenance:
  source_name: learn-fabric-whats-new
  source_representation: html
  adapter_type: html
  parser_version: 1
  extraction_method: html-table-row
  discovered_at: 2026-08-03T06:00:00+00:00
  identity_basis: canonical-url
  identity_key: sha256:...
  selector: "h2#generally-available-features + table tr (date from cell 0)"
  run_id: run-2026-08-03T06:00:00Z
  source_role: primary

# --- Classification (engine-proposed, user-overridable) ---
tier: 1
learning_priority: high
category: data-engineering
tags: [direct-lake, semantic-model]
difficulty: intermediate
workload: moderate
version: "2026 Release Wave 1"

# --- Computed (engine-owned) ---
reading_time: 4

# --- Learning state (user-owned) ---
learning_status: not-started
notes: null

# --- Relationships (user-owned) ---
prerequisites: [MSF-2025-11-002]
builds_on: [MSF-2025-11-002]
related_topics: [MSF-2026-04-007]
replaced_by: null
replaces: null

# --- Lifecycle (engine-owned) ---
status: active
needs_review: false
overrides: []
revisions:
  - revision: 1
    date: 2026-08-03
    changed_fields: []
    summary: Initial ingestion

# --- Generation tracking ---
generation:
  tutorial:
    status: generated
    path: artifacts/tutorial.md
    generated_at: 2026-08-04
    generated_from_revision: 1
    model: claude-opus-5
    prompt_version: 1
  linkedin-post: {status: requested}
  quiz: {status: none}
```

### Field reference

| Field | Type | Required | Notes |
|---|---|---|---|
| `schema_version` | int | yes | Layout version. See §7. |
| `id` | string | yes | `<PREFIX>-<YYYY>-<MM>-<NNN>`. Must match the directory name and path. |
| `slug` | string | yes | Lower-case, hyphenated. Part of the directory name. |
| `title` | string | yes | Must match the `# ` heading in `feature.md`. |
| `source_name` | string | yes | Key of a source defined in `pack.yml`. |
| `source_url` | string | yes | Canonical URL, tracking parameters stripped. |
| `source_authority` | enum | yes | `official-microsoft` \| `microsoft-community` \| `third-party` |
| `published_date` | date \| null | yes | Null only when `date_confidence` is `inferred`. |
| `discovered_date` | date | yes | When the engine first saw it. |
| `date_confidence` | enum | yes | `exact` \| `inferred`. Do we trust the date at all? |
| `date_precision` | enum | yes | `day` \| `month` \| `year`. How precise is it? **Independent of confidence** — see below. |
| `provenance` | map | yes | Adapter, source, timestamp, extraction method, parser version, selector, run. See §10. |
| `content_hash` | string | yes | `sha256:…` of normalised title + summary. Drives change detection. |
| `url_hash` | string | yes | `sha256:…` of the canonical URL. Drives exact-duplicate detection. |
| `tier` | int | yes | `1` act now \| `2` learn soon \| `3` awareness. Operational impact. |
| `learning_priority` | enum | yes | `high` \| `medium` \| `low`. Content value — independent of tier. |
| `category` | string \| null | yes | One of `pack.yml`'s `categories`. |
| `tags` | list[string] | yes | May be empty. |
| `difficulty` | enum | yes | `beginner` \| `intermediate` \| `advanced` |
| `workload` | enum | yes | `light` \| `moderate` \| `heavy`. Hands-on effort, not reading effort. |
| `version` | string \| null | yes | Product version or release wave the item applies to. |
| `reading_time` | int | yes | Minutes, computed from word count. |
| `learning_status` | enum | yes | `not-started` \| `in-progress` \| `learned` \| `revisit` |
| `notes` | string \| null | yes | Free text. Yours. |
| `prerequisites` | list[FeatureId] | yes | Must be learned first. Forms a DAG. |
| `builds_on` | list[FeatureId] | yes | Extends these concepts. Forms a DAG. |
| `related_topics` | list[FeatureId] | yes | Symmetric in meaning; declared one-way (see §6). |
| `replaced_by` | FeatureId \| null | yes | Set when this object is superseded. |
| `replaces` | FeatureId \| null | yes | Inverse of `replaced_by`. |
| `status` | enum | yes | `active` \| `replaced` \| `deprecated`. There is no `deleted`. |
| `needs_review` | bool | yes | Engine could not classify confidently, or flagged a near-duplicate. |
| `overrides` | list[string] | yes | Engine-proposed fields the user has locked. |
| `revisions` | list[Revision] | yes | Append-only, with content hash and snapshots. See §5. |
| `generation` | map | yes | Artifact tracking. See §6. |

**Date confidence and date precision are independent.** They answer different
questions, and overloading one to carry both loses information. The Microsoft
Learn "What's New" page dates updates to a *month*: that is an exactly known
month, so `date_confidence: exact` with `date_precision: month`. Marking it
`inferred` would wrongly suggest we guessed; recording day precision would be
quietly false. `published_date` always holds a real date — the first of the month
or of the year — so sorting stays deterministic; `date_precision` says how much
of it to believe. Feature ID minting is unaffected: ADR-0005 needs the month, and
month precision supplies exactly that.

**Tier and learning priority are independent.** A Tier 3 item can be high
learning priority (an excellent deep dive), and a Tier 1 item can be low (a
pricing tweak you just need to know happened).

---

## 5. Revisions and the update policy

When a re-harvest finds the source article has changed, the engine **updates the
existing object in place** and appends a revision. It does not mint a new ID —
the Feature ID represents the concept, and the concept has not changed.

```yaml
revisions:
  - revision: 1
    date: 2026-08-03
    changed_fields: []
    summary: Initial ingestion
    content_hash: sha256:aaa...
    title_snapshot: Direct Lake mode enters preview
    summary_snapshot: Direct Lake is available in preview for...
    run_id: run-2026-08-03T06:00:00Z
  - revision: 2
    date: 2026-09-14
    changed_fields: [title, content_hash]
    summary: Source article retitled and expanded
    content_hash: sha256:bbb...
    title_snapshot: Direct Lake mode reaches general availability
    summary_snapshot: Direct Lake is now generally available for...
    run_id: run-2026-09-14T06:00:00Z
```

The snapshots make the object **self-describing over time**: "how did Direct Lake
evolve?" is answerable by reading one file, deterministically and without Git or
an AI model. They are cheap because §8 already caps stored summaries at a short
paragraph. See [`docs/design/TIME_MACHINE.md`](design/TIME_MACHINE.md).

- Revision 1 is always the initial ingestion.
- A revision is appended **only** when an engine-owned field actually changed;
  a run that finds nothing new writes nothing.
- Only engine-owned fields may change. User-owned fields survive byte-for-byte.
- Revisions are append-only and never rewritten.

A new Feature ID is minted only when the source introduces a genuinely new
feature or concept. When that new object supersedes an old one, use
`ke supersede`, which sets `status: replaced` on the old object and links both
directions. **The old object stays in the repository.**

---

## 6. Relationships and generation tracking

### Relationships

`prerequisites` and `builds_on` form a **directed acyclic graph**. `ke validate`
checks referential integrity (every referenced ID exists) and detects cycles.
`replaced_by` and `replaces` must be inverse-consistent.

`related_topics` is symmetric in meaning but declared **one way** in the file.
The indexer materialises both directions when building indexes, so adding a
relationship never rewrites the other object's file.

Relationships cannot be derived reliably without an AI model, so they are
**user-curated**. The engine may propose candidates from tag and category
overlap into `indexes/review-queue.md`, but never writes them.

> Graph validation lands in M4. M0 validates the schema of these fields only.

### Generation tracking

| Status | Meaning |
|---|---|
| `none` | Never requested. |
| `requested` | You want it; not generated yet. |
| `generated` | Present and current. |
| `stale` | Present, but the knowledge has been revised since. |
| `rejected` | Generated and deliberately discarded. Do not re-offer. |

**Staleness is computed, never guessed:** an artifact is stale when
`generated_from_revision` is lower than the object's current revision. The
scheduled pipeline detects and reports this; it never regenerates anything.
Stale artifacts are marked, **never deleted**.

`model` is recorded for provenance only. Nothing in the engine reads it — that
is what keeps the system AI-vendor-independent while still letting you see which
model produced a given artifact.

Artifact types: `tutorial`, `interview-questions`, `linkedin-post`,
`infographic`, `coding-example`, `architecture-explanation`, `quiz`.

---

## 7. Schema versioning

`schema_version` is the layout version of `metadata.yaml`. It is **not** the
product version — that is the `version` field.

The engine reads only the version it was built for. When the schema changes,
`ke migrate` (M9) upgrades objects in place and appends a revision recording the
migration. `ke validate` fails on any object whose `schema_version` this build
does not support, rather than guessing at unfamiliar fields.

Current version: **1**.

---

## 8. `feature.md`

```markdown
# Direct Lake mode reaches general availability

Direct Lake mode is now generally available for production workloads...

## Why it matters

...

Source: https://blog.fabric.microsoft.com/...
```

- The `# ` heading must match `title` in `metadata.yaml`.
- The summary must stay under `limits.max_summary_words` from `pack.yml`.

**Never store the full text of a third-party article.** Store a short original
summary and a link. This is both a repository-size decision and a copyright one,
and `ke validate` enforces the word limit.

---

## 9. What `ke validate` checks

| Area | Checks |
|---|---|
| Pack | `pack.yml` parses; required keys present; `state/` exists. A pack that cannot be loaded is reported (`PACK005`) and the remaining packs are still validated. |
| Structure | `feature.md` and `metadata.yaml` exist |
| Identity | ID well-formed; matches pack prefix, directory name and `YYYY/MM` path; no duplicates |
| Schema | Required fields present; enums and types valid; no unknown fields; supported `schema_version` |
| Ownership | `overrides` names only engine-proposed fields |
| Artifacts | Every artifact marked `generated`/`stale` records a path, inside a known subdirectory, and the file exists |
| Registry | Per-month counters not lower than the highest sequence actually used; `id → path` entries resolve |
| Consistency | `feature.md` heading matches `title` |
| Copyright | Summary within `limits.max_summary_words` |

Errors fail the build. Warnings are reported and pass, unless `--strict`.

Graph checks (referential integrity, cycle detection) arrive in M4.


---

## 10. Provenance

Every discovered item carries a provenance record, and it travels with the stored
object. It is engine-owned.

### The discovery chain

Provenance fields are stored in the order knowledge actually travelled, so the
record reads as a chain rather than a bag of attributes:

```
Source → Representation → Adapter → Adapter version → Extraction method
       → Identity basis → Date precision → Date confidence → Knowledge object
```

Every link answers a different question, and every link can break independently.
`date_precision` and `date_confidence` are the tail of the same chain even though
they live on the object rather than inside `provenance` — they describe how the
publication date was arrived at, which is what ADR-0005 mints a permanent Feature
ID from.

| Field | Meaning |
|---|---|
| `source_name` | Key of the source in `pack.yml` |
| `source_representation` | `html` \| `markdown` \| `rss` \| `atom` \| `api` — the format actually received |
| `adapter_type` | `rss` \| `atom` \| `html` \| `markdown` \| `github-commits` \| `sitemap` \| `manual` |
| `parser_version` | Version of the adapter's parser that produced it |
| `extraction_method` | `feed-entry` \| `html-table-row` \| `markdown-table-row` \| `html-heading-section` \| `html-list-item` \| `json-field` \| `commit-message` \| `manual-entry` |
| `discovered_at` | UTC ISO-8601 timestamp of the run that found it |
| `identity_basis` | `canonical-url` \| `source-identifier` \| `normalised-title-hash` \| `content-fingerprint` — which signal the Feature ID rests on |
| `identity_key` | The computed key itself |
| `selector` | The concrete selector, XPath, feed field or column used |
| `run_id` | Correlates the object with the run log and event log |
| `source_role` | `primary` \| `secondary` \| `manual-review` — which link in the fallback chain produced it |

**Representation is not adapter.** They are usually the same word and
occasionally are not, which is exactly why both are stored. The Fabric updates
exist as rendered `html` on Microsoft Learn *and* as `markdown` in the
`MicrosoftDocs/fabric-docs` repository — same authoritative content, different
hosts, different failure modes. That is what makes one a usable fallback for the
other, and "was this object read from the rendered page or from the source file?"
is a question that cannot be answered from the adapter name alone once a source
grows a second adapter. See [ADR-0026](adr/0026-discovery-chain-provenance.md).

**Why this is worth the bytes.** When a source changes its markup, the items
produced afterwards are subtly *wrong* rather than absent — the failure mode no
health check catches. `parser_version` and `selector` make it possible to find
every object a given parser produced and re-examine exactly those, instead of
re-verifying the whole pack. `identity_basis` narrows it further: a duplicate
investigation starts with "what were we matching on?", and objects resting on a
title hash are the ones worth looking at first.

---

## 11. Source health

Health state lives in `state/source-health.json`, outside any knowledge object,
because it describes the *source* rather than the knowledge. Full design in
[`docs/design/SOURCE_HEALTH.md`](design/SOURCE_HEALTH.md).

States: `healthy` · `degraded` · `failed` · `disabled`.

The one worth understanding is **`degraded`**: reachable and returning items, but
either falling back to a secondary source or returning far fewer items than its
historical median. A source that always returns twenty items and suddenly
returns zero has probably broken — treating that as "no news this week" is how a
pipeline dies without anyone noticing.
