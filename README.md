# Knowledge Engine

An AI-vendor-independent engine that builds and maintains structured knowledge
repositories — **Domain Packs** — as Markdown in this Git repository.

GitHub is the single source of truth. There is no database, no web service and
no paid infrastructure.

The first Domain Pack is **Microsoft Fabric**. Power BI, SQL, Python,
Databricks, Azure, Snowflake, AWS and Personal Knowledge follow.

## How it works

Every Sunday a GitHub Actions job discovers new knowledge from trusted sources,
deduplicates it, classifies it, stores it as a knowledge object, rebuilds the
indexes, writes a weekly digest and notifies you.

That pipeline is **fully deterministic and never calls an AI model**. Generation
— tutorials, LinkedIn posts, interview questions, coding examples, architecture
explanations, quizzes, infographic prompts — happens **on demand**, by producing
a self-contained context pack you paste into Claude, ChatGPT, Gemini, Kimi or
anything else. No API key, no adapter, no vendor lock-in.

## Repository layout

```
engine/ke/            the engine (installable, extraction-ready)
engine/tests/         test suite
docs/SCHEMA.md        the knowledge object contract
domain-packs/         the knowledge itself (pure data)
.github/workflows/    CI, and the weekly harvest from M6
```

A knowledge object is a directory whose path never changes:

```
domain-packs/microsoft-fabric/knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
├── feature.md      canonical knowledge article
├── metadata.yaml   structured metadata
├── artifacts/      generated tutorials, posts, quizzes, code examples
├── images/         infographics, diagrams
└── references/     supporting notes
```

## Getting started

```bash
python -m pip install -e ".[dev]"   # Python 3.11+
python -m pytest engine/tests -q    # run the tests
python -m ke validate               # check every Domain Pack
```

`ke validate` is the guardrail. It enforces the rules in `CLAUDE.md`: no
duplicate Feature IDs, nothing silently deleted, paths that match their IDs, and
short summaries rather than copied article text. It runs in CI on every push.

```
usage: ke validate [--pack NAME] [--repo-root PATH] [--strict]
```

## Build status

Built milestone by milestone. See `docs/SCHEMA.md` for the schema contract.

| Milestone | Scope | Status |
|---|---|---|
| M0 | Foundation, schema, guardrails | done |
| M1 | Discovery — source adapters | next |
| M2 | Identity, dedupe, knowledge objects | |
| M3 | Classification and learning metadata | |
| M4 | Relationships and knowledge graph | |
| M5 | Revisions, in-place updates, staleness | |
| M6 | Weekly automation, digest, notifications | |
| M7 | Retrieval and on-demand generation | |
| M8 | Second pack (Power BI) | |
| M9 | Hardening, migration, split-readiness | |
