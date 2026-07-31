# ADR-0002: GitHub as the storage layer and single source of truth

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

The system needs somewhere to keep knowledge that satisfies several constraints
at once:

- **Durable** — measured in decades, not product lifetimes.
- **Versioned** — corrections must be traceable, and "never delete knowledge"
  needs a mechanism behind it.
- **Private** — this is a personal knowledge base.
- **Free** — the target budget is ₹20/month, which is effectively zero.
- **Portable** — no vendor may hold the knowledge hostage.
- **Automatable** — something must run weekly without a server.
- **Editable by hand** — learning state is maintained manually.

Most storage options satisfy three or four of these. Very few satisfy all seven.

## Decision

Use a **private GitHub repository** as the single source of truth. All knowledge,
all state, and all configuration live as files in Git.

GitHub Actions provides the scheduled execution. GitHub Issues provide a durable
notification channel. The GitHub web UI provides read access from any device
without building one.

There is no other datastore. No database, no object store, no external service
holds any part of the knowledge.

## Consequences

### Positive
- **Version history is free and complete.** Every change to every fact is
  attributable and reversible, which is what makes "never delete knowledge"
  enforceable rather than aspirational.
- **Zero cost.** Free tier covers private repos, 2,000 Actions minutes/month
  (we use ~22), and unlimited Issues.
- **No server to operate.** Nothing to patch, monitor, or pay for.
- **Portable by construction.** `git clone` produces the entire knowledge base.
  Migration to any other Git host is a remote change.
- **Editable everywhere.** The GitHub UI, any editor, any Git client.
- **Automation is co-located with the data**, so the weekly job has no
  credentials to manage for its own storage.

### Negative
- **GitHub Pages is unavailable for private repos on the Free plan**, so a web UI
  is impossible without paying. Retrieval must be repo-native — Markdown indexes
  plus a CLI.
- **Scheduled workflows are disabled after 60 days without commit activity.**
  Mitigated by always appending to `state/run-log.md`, guaranteeing a weekly
  commit even on an empty run.
- **Concurrency is coarse.** Two simultaneous writers conflict at the file level.
  Mitigated with a workflow `concurrency` group and `git pull --rebase`.
- **Repository size grows forever**, since nothing is deleted. Mitigated by
  storing summaries and links rather than full articles (ADR-0003), and by
  `YYYY/MM/` partitioning.
- **A GitHub outage blocks the weekly run.** Acceptable — the next run catches up,
  and a local clone is always readable.

### Neutral
- Ties the project to Git semantics, which is the point.
- The engine is host-agnostic in principle; only the notifier and workflow are
  GitHub-specific.

## Alternatives considered

**A managed database (Postgres, Supabase, Firebase).** Better querying, real
concurrency. Rejected: costs money at some tier, adds an operational dependency,
makes hand-editing impractical, and the knowledge stops being portable plain
text. Fails the durability and cost goals simultaneously.

**SQLite committed to Git.** Keeps files and gains SQL. Rejected: a binary blob
in Git means no meaningful diffs, no hand-editing, and merge conflicts that
cannot be resolved. It would break the single most useful property of Git for
this use case.

**A cloud object store (S3, Blob).** Durable and cheap. Rejected: no versioning
semantics worth the name, no free scheduled compute, no UI, and hand-editing
requires tooling.

**A notes app with an API (Notion, Obsidian Sync).** Good editing experience.
Rejected: vendor lock-in of exactly the kind this project exists to avoid, API
rate limits, and the knowledge is not plain text you own.

**Self-hosted Git (Gitea on a VPS).** Full control. Rejected: costs money,
requires operating a server, and adds a failure mode with no compensating
benefit over GitHub for a private personal repository.
