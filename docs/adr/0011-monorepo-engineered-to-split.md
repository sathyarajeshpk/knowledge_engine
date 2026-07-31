# ADR-0011: Single repository, engineered to split

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

The project has two distinct kinds of content with different natural audiences
and lifecycles:

- **The engine** — generic code that knows nothing about any particular subject.
  Potentially useful to other people. A candidate for open source.
- **The knowledge** — personal, private, growing forever, specific to one user's
  interests.

They could live in one repository or two. The choice affects tooling, CI,
credentials and how easily either can be shared later — and it is expensive to
change once the knowledge base is large.

## Decision

**One private repository now, structured so the engine can be extracted later
without a refactor.**

Concretely:

- All code lives under `engine/`. `pyproject.toml` sets
  `package-dir = { "" = "engine" }`, so `engine/ke/` already imports as `ke` and
  `engine/` is already a self-contained, installable Python project.
- All data lives under `domain-packs/`. Packs are found by scanning that
  directory — never by name.
- **The engine contains no reference to any specific pack.** This is verifiable:
  `grep -ri "microsoft-fabric" engine/` returns nothing, and it is checked as
  part of every milestone review.

Extraction, when it happens, is `git subtree split` on `engine/` plus pointing
the packs repository at the published package.

## Consequences

### Positive
- **One repository to clone, one CI pipeline, one place to look.** For a
  single-user project at this stage, that is a real reduction in friction.
- **No cross-repository credentials.** A split would require a PAT for the
  knowledge repo to be readable by the engine's CI, which is a secret to manage,
  rotate and leak.
- **Atomic changes.** A schema change and the migration touching pack data land
  in one commit, so the repository is never in a half-migrated state.
- **The split stays cheap.** Because the boundary is enforced now, extraction is
  mechanical rather than a rewrite.
- **The discipline pays off immediately**, not only at extraction: it is the same
  property that makes M8 (add a Power BI pack, `git diff engine/` is empty)
  achievable.

### Negative
- **The engine cannot be open-sourced today** without also exposing the private
  knowledge, or performing the split first.
- **Requires ongoing discipline.** The moment someone writes
  `if pack.name == "microsoft-fabric":` inside `engine/`, the property is gone
  and nothing automatically catches it. Currently checked by review and by the
  `grep`; a CI check would be stronger.
- **Repository size mixes code and data**, so `git clone` gets both. Irrelevant
  at this scale.
- **Versioning is coupled.** The engine and the knowledge share a version and a
  changelog, which is slightly wrong conceptually — `SCHEMA_VERSION` exists
  partly to decouple the part that actually matters.

### Neutral
- `CHANGELOG.md` currently covers the whole repository. After a split it would
  belong to the engine, with packs versioned by their content.

## Alternatives considered

**Split from day one** — public engine, private pack repositories. Cleanest
long-term shape and the honest end state. Rejected *for now*: it requires
cross-repo tokens, two CI setups, coordinated releases and a published package,
before there is any consumer to justify that overhead. All of it can be deferred
at essentially zero cost because the boundary is being maintained anyway.

**Pure monorepo with no split intent** — let the engine import pack specifics
freely. Rejected: it is the same amount of work to keep the boundary clean, and
losing it forecloses both open-sourcing and the M8 abstraction proof.

**One repository per Domain Pack, sharing the engine as a submodule.** Rejected:
Git submodules are a well-known source of confusion, and nine pack repositories
for one user is administrative overhead with no benefit.

**Publish the engine to PyPI immediately** and have the knowledge repo depend on
it. Rejected: premature. It adds a release step to every engine change while
there is exactly one consumer, and slows the M0–M9 iteration loop for no gain.
