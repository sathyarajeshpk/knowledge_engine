# Knowledge Engine

**A model-independent Knowledge Operating System.**

Knowledge Engine discovers, structures and maintains technical knowledge as plain
Markdown in a Git repository you own — then hands it to whatever AI model you
like, on demand.

The first Domain Pack is **Microsoft Fabric**. Power BI, SQL, Python, Azure,
Databricks, Snowflake, AWS and Personal Knowledge follow.

[![CI](https://github.com/sathyarajeshpk/knowledge_engine/actions/workflows/ci.yml/badge.svg)](https://github.com/sathyarajeshpk/knowledge_engine/actions/workflows/ci.yml)

---

## Why this exists

If you work in a fast-moving platform ecosystem, you face four problems:

1. **Updates arrive faster than you can read them**, so you find out about
   breaking changes from a broken pipeline.
2. **What you learn evaporates.** Bookmarks rot; notes apps become write-only.
3. **Your best explanations are trapped in someone's chat history**, in a format
   you do not control.
4. **Learning has no state.** Nothing knows what you have learned, what is
   in progress, or what you should study next.

Knowledge Engine fixes all four by keeping one idea straight:

> **Separate the knowledge from the intelligence.**

| Layer | What it is | Who owns it | Lifespan |
|---|---|---|---|
| Knowledge | Markdown + YAML in Git | You | Decades |
| Structure | IDs, indexes, relationships, learning state | Deterministic code | Decades |
| Intelligence | Whatever model you feel like today | Rented, replaceable | Months |

**The test of whether this works:** if every AI vendor disappeared tomorrow, the
repository would still be a complete, structured, useful body of knowledge — and
the engine would keep maintaining it.

Full reasoning in [`docs/VISION.md`](docs/VISION.md).

---

## How it works

Every Sunday, a GitHub Actions job discovers new knowledge from trusted sources,
deduplicates it, classifies it, stores it as a knowledge object, rebuilds the
indexes, writes a weekly digest and notifies you.

**That pipeline never calls an AI model.** It is fully deterministic — same
inputs, same outputs, zero cost, no vendor.

Generation — tutorials, LinkedIn posts, interview questions, coding examples,
architecture explanations, quizzes, infographic prompts — happens **on demand**.
`ke generate` produces a self-contained Markdown *context pack* that you paste
into Claude, ChatGPT, Gemini, Kimi, or anything that comes next. No API key, no
adapter, no lock-in. ([ADR-0004](docs/adr/0004-no-ai-in-the-scheduled-pipeline.md))

```mermaid
graph LR
    SRC["Trusted sources<br/>RSS · docs · roadmap"] -->|weekly, deterministic| ENG

    subgraph ENG["Engine — no AI, no database"]
        D[discover] --> N[normalise] --> DD[dedupe]
        DD --> ID[mint Feature ID] --> ST[store] --> IX[index] --> DG[digest]
    end

    ENG --> REPO[("Private Git repo<br/>Markdown + YAML")]
    REPO --> NOTIFY["GitHub Issue<br/>+ email"]

    REPO -->|on demand only| GEN["ke generate<br/>context pack"]
    GEN -.->|you paste it| AI["Any AI model"]
    AI -.->|you attach the result| REPO

    style ENG fill:#1e3a5f,color:#fff
    style REPO fill:#2d5a3d,color:#fff
    style AI fill:#5c4317,color:#fff
```

The dotted lines are the only places AI touches the system — and a human is
standing on both of them.

---

## Getting started

Requires **Python 3.11+**.

```bash
git clone https://github.com/sathyarajeshpk/knowledge_engine.git
cd knowledge_engine

python -m pip install -e ".[dev]"

python -m pytest engine/tests -q     # 594 tests, ~30s
python -m ke validate                # check every Domain Pack
```

Expected output:

```
1 pack(s), 222 knowledge object(s): 0 error(s), 35 warning(s)
```

The 35 warnings are real and deliberate: 35 objects carry revision history
produced by a bug fixed in M3. The revisions truthfully record what the engine
did at the time, so they are surfaced rather than rewritten.

For a reproducible install with hash-pinned dependencies — what the weekly job
uses — see `requirements.lock`:

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps -e ".[dev]"
```

### `ke validate`

The guardrail. It enforces the rules in [`CLAUDE.md`](CLAUDE.md): no duplicate
Feature IDs, nothing silently deleted, paths that match their IDs, no full
third-party article text. It runs in CI on every push.

```
usage: ke validate [--pack NAME] [--repo-root PATH] [--strict]
```

Every finding carries a stable code (`ID003`, `OWN001`, `REG002`) documented in
[`docs/SCHEMA.md`](docs/SCHEMA.md) §9.

---

## Using your knowledge

This is the half the engine exists for. Everything above gets knowledge *in*;
these commands get it *out*.

### Find something

Filters compose by AND. There is no query language and no relevance ranking — a
wrong ranking hides things convincingly, which is worse than no ranking at all.

```bash
ke search                                  # everything, most useful first
ke search "direct lake"                    # title, category and tags
ke search --tier 1 --learning-status not-started
ke search --tag governance --since 2026-06-01
ke search --needs-review                   # what the engine could not classify
ke search --stale                          # artifacts the source has outgrown
ke search --tier 1 --ids-only | head -5    # for piping
```

Text matching ignores case *and* punctuation, so `direct lake`, `Direct-Lake`
and `directlake` all find the same objects.

```bash
ke get MSF-2026-05-029     # one object in full, including artifact status
```

### Turn it into something

```bash
ke generate list                                    # the seven artifact types
ke generate tutorial --id MSF-2026-05-029           # prints a context pack
```

The output is a **self-contained document**: the instruction, the knowledge
article, the metadata a model would otherwise guess at, related objects, and the
source link. Paste it into Claude, ChatGPT, Gemini, or whatever exists next.
Nothing about it is vendor-specific, and there is no API key anywhere.

The full loop:

```bash
# 1. Assemble the pack and put it on the clipboard
ke generate tutorial --id MSF-2026-05-029 | pbcopy       # macOS
ke generate tutorial --id MSF-2026-05-029 | xclip -sel c # Linux

# 2. Paste into any model. Read what comes back. This step is not optional —
#    everything generated is plausible-sounding text about a technical subject,
#    which is exactly where a wrong answer is hardest to spot.

# 3. Paste the answer back
pbpaste | ke generate tutorial --id MSF-2026-05-029 --attach - --model <name>
```

That writes `artifacts/tutorial.md` inside the object's directory and records
what produced it:

```yaml
generation:
  tutorial:
    status: generated
    path: artifacts/tutorial.md
    generated_at: 2026-08-01
    generated_from_revision: 1
    model: <name>          # provenance only — nothing in the engine reads it
    prompt_version: 1
```

**The artifact file is yours.** Edit it, rewrite it, throw half of it away. The
engine will never touch it again. Only the `generation` block above — bookkeeping
*about* the artifact — belongs to the engine.

`--model` is recorded so that when something reads oddly in six months you can
see what wrote it. Recording it is not a dependency; reading it would be.

### Not right now, but soon

```bash
ke generate quiz --id MSF-2026-05-029 --request
```

Records the intention without generating anything. It shows up in `ke status`
and the weekly digest, which is the only mechanism this system has for making a
good intention visible after the enthusiasm has passed.

### Keep track

```bash
ke status                # coverage by artifact type
ke status --stale        # what the source changed after you made it
ke status --requested    # what you promised yourself
ke status --refresh      # write computed staleness into metadata
```

**Staleness is computed, never guessed.** An artifact is stale exactly when the
knowledge object has been revised since it was generated. Nothing is regenerated
automatically and nothing is ever deleted — the engine detects and reports, you
decide.

```bash
ke generate tutorial --id MSF-2026-05-029 --attach new.md   # stale: allowed
ke generate tutorial --id MSF-2026-05-029 --attach new.md --force  # current: needs --force
```

Replacing a *stale* artifact is the normal path. Replacing a *current* one needs
`--force`, because an artifact you have since edited by hand should not vanish
because you re-ran a command.

### The rest of the CLI

```bash
ke harvest              # the full pipeline (what Sunday runs)
ke review next          # the most urgent pending decision
ke history MSF-2026-05-029          # every recorded state of an object
ke supersede <old> --by <new>       # record that one feature replaced another
ke validate             # the guardrail; runs in CI
```

---

## Repository structure

```
knowledge_engine/
├── engine/                   ← ALL CODE. Knows nothing about any subject.
│   ├── ke/
│   │   ├── models.py         What a knowledge object IS + field ownership
│   │   ├── pack.py           Finding and loading Domain Packs
│   │   ├── validate.py       schema, ID, ownership and history checks
│   │   ├── retrieve.py       ke search / ke get
│   │   ├── generate.py       context packs for any AI model
│   │   ├── prompts/          seven versioned prompt templates
│   │   └── __main__.py       the CLI
│   └── tests/                594 tests
│
├── domain-packs/             ← ALL DATA. Contains no code.
│   └── microsoft-fabric/
│       ├── pack.yml          Config: prefix, categories, limits, sources
│       ├── knowledge/        Knowledge objects, by YYYY/MM
│       ├── indexes/          Generated, rebuilt every run
│       ├── digests/          Weekly summaries
│       └── state/            Engine bookkeeping (ID registry, run log)
│
├── docs/
│   ├── SCHEMA.md             The data contract
│   ├── VISION.md             Why this exists, and where it goes
│   ├── ROADMAP.md            M0–M9 and the Domain Pack plan
│   ├── JOURNAL.md            Development journal per milestone
│   ├── adr/                  40 Architecture Decision Records
│   ├── reviews/              architecture, security and readiness reviews
│   ├── playbook/             File-by-file developer guides
│   └── learning/             Concept guides for each milestone
│
└── .github/workflows/ci.yml  Tests + validation on every push
```

**The one structural rule:** `engine/` is code, `domain-packs/` is data, and
neither knows the other's specifics. Verifiable:

```bash
grep -ri "microsoft-fabric" engine/     # returns nothing
```

This is what lets a new Domain Pack be a folder rather than a code change, and
what will let `engine/` become its own repository later.
([ADR-0011](docs/adr/0011-monorepo-engineered-to-split.md))

---

## How Domain Packs work

A **Domain Pack** is one knowledge repository for one subject. It is pure data.

### A knowledge object

Every object is a **directory** whose path never changes for its entire lifetime:

```
knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
├── feature.md      canonical knowledge article (short summary + link)
├── metadata.yaml   structured metadata
├── artifacts/      generated tutorials, posts, quizzes, code examples
├── images/         infographics, diagrams
└── references/     supporting notes
```

The three subdirectories are created on demand — Git cannot store an empty
directory, so a freshly harvested object is just the two files.

### Feature IDs

```
MSF-2026-04-001
│   │    │  └── sequence within that month
│   │    └───── month — from the publication date, or discovery if unknown
│   └────────── year
└────────────── pack prefix
```

**Permanent. Never reused. Never renumbered** — including for replaced objects.
Sorting IDs gives chronological order.
([ADR-0005](docs/adr/0005-date-based-feature-ids.md))

### Field ownership — the important part

`metadata.yaml` mixes facts from the source with **your own work**. A job rewrites
those files weekly. So every field belongs to exactly one class:

| Class | Engine behaviour | Examples |
|---|---|---|
| **Engine-owned** | Rewritten freely | `title`, `source_url`, `content_hash`, `reading_time` |
| **Engine-proposed** | Written only if absent or unlocked | `tier`, `difficulty`, `tags`, `category` |
| **User-owned** | **Never written by the engine** | `learning_status`, `notes`, `prerequisites`, relationships |

Disagree with the engine's judgement? Change the value and lock it:

```yaml
difficulty: advanced
overrides: [difficulty]
```

This is not a convention. `with_engine_fields()` raises `PermissionError`, and
three import-time assertions prove the classes never overlap.
([ADR-0008](docs/adr/0008-field-ownership-model.md))

### The three classification axes

Independent on purpose. A licensing change is urgent but has nothing to teach; a
community deep-dive is not urgent but is the best possible tutorial source.

| Field | Question | Values |
|---|---|---|
| `tier` | How urgent in real work? | `1` act now · `2` learn soon · `3` awareness |
| `learning_priority` | Worth building content around? | `high` · `medium` · `low` |
| `source_authority` | Where did it come from? | official / community / third-party |

### Adding a pack

```bash
mkdir -p domain-packs/<name>/{knowledge,indexes,digests,state}
# write pack.yml with a permanent id_prefix
echo '{"counters": {}, "paths": {}}' > domain-packs/<name>/state/id-registry.json
python -m ke validate
```

No engine change. If one was needed, that is an engine bug.

---

## Development workflow

Work proceeds **one milestone at a time**, and the next does not start until the
current one is reviewed, merged and approved.

Every milestone delivers, without being asked:

- A **PR review summary** written as a Senior Software Architect
- An **Architecture Review** — `docs/reviews/M<n>_ARCHITECTURE_REVIEW.md`
- A **Security & Vulnerability Review** — from M6 onward
- An **Operational Readiness Review** — from M7 onward
- Release notes, a test summary, remaining technical debt, risks for the next
  milestone
- Journal, roadmap, changelog and ADR updates

Developer Playbooks and Learning Guides (`docs/playbook/`, `docs/learning/`)
exist for M0–M1 and are paused: from M2 the project moved from
architecture-first to product-first, and they resume on request.

| # | Milestone | Status |
|---|---|---|
| M0 | Foundation, schema, guardrails | **done** |
| M1 | Discovery and acquisition | **done** |
| M2 | Identity, dedupe, storage | **done** |
| M3 | The update path | **done** |
| M4 | Orchestration and classification | **done** |
| M5 | Review workflow and history | **done** |
| M6 | Weekly automation and notifications | **done** |
| M7 | Retrieval and on-demand generation | **done** |
| M8 | Second pack — proves the abstraction | next |
| M9 | Hardening, migration, split-readiness | planned |

Full detail in [`docs/ROADMAP.md`](docs/ROADMAP.md).

---

## Contributing

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) for coding standards, commit
conventions, testing requirements and the review process.

The short version — six rules are enforced by mechanisms, not by review:

| Rule | What enforces it |
|---|---|
| Knowledge is never deleted | `ObjectStatus` has no `deleted` member |
| The engine never writes user-owned fields | `PermissionError` |
| Ownership classes never overlap | Import-time assertions |
| Feature IDs are unique and permanent | `ke validate` |
| No AI in the scheduled pipeline | CI workflow scan |
| No full third-party article text | `ke validate` |

If a change needs to break one of these, it needs an ADR — not a workaround.

**New here?** Start with [`docs/playbook/M0_FOUNDATION.md`](docs/playbook/M0_FOUNDATION.md)
for the codebase, or [`docs/learning/M0_LEARNING_GUIDE.md`](docs/learning/M0_LEARNING_GUIDE.md)
if the Python concepts are new.

---

## Design principles

**Rules must be mechanisms, not documentation.** If you find yourself writing "we
should remember to…", you have found a missing mechanism.

**Determinism is a feature.** Testable, reproducible, debuggable, free and
vendor-independent — five benefits from one constraint.

**Data over code.** Categories, limits and rules live in `pack.yml`. Tuning
behaviour is a text edit, not a release.

**Nothing is lost, only marked.** Corrections append revisions. Superseded
objects are `replaced`. Stale artifacts are `stale`. Near-duplicates are flagged
for review, never dropped.

**Build the guardrails before the thing they guard.** M0 shipped no pipeline —
only the schema, the ID rules, the ownership model, the validator and CI.

---

## Cost

| Item | Cost |
|---|---|
| GitHub private repository | ₹0 |
| GitHub Actions (~22 of 2,000 free minutes/month) | ₹0 |
| AI API calls | ₹0 — the pipeline makes none |
| Database, servers, hosting | ₹0 — there are none |
| **Total** | **₹0/month** |

Target was ₹20/month. Zero cost is a design constraint, not thrift: a system with
no running cost never needs a business case and never gets switched off.

---

## Documentation

| Document | What it covers |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | The non-negotiable project rules |
| [`docs/VISION.md`](docs/VISION.md) | Why this exists, goals, philosophy, long view |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | M0–M9, Domain Packs, non-goals |
| [`docs/SCHEMA.md`](docs/SCHEMA.md) | The data contract `ke validate` enforces |
| [`docs/adr/`](docs/adr/) | 40 Architecture Decision Records |
| [`docs/reviews/`](docs/reviews/) | Architecture, security and operational readiness reviews |
| [`docs/JOURNAL.md`](docs/JOURNAL.md) | Development journal per milestone |
| [`docs/playbook/`](docs/playbook/) | File-by-file developer guides |
| [`docs/learning/`](docs/learning/) | Concept guides per milestone |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Standards, conventions, review process |
