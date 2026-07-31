# ADR-0001: Record architecture decisions

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

This project makes a number of decisions that look arbitrary or even wrong
without their context:

- No database, in a system that manages structured records.
- No AI calls, in a system built to feed AI models.
- An empty `sources: []` list, in a system whose purpose is discovery.
- A directory per object, most of them containing one file.

A contributor arriving later — including the original author after six months —
cannot distinguish a deliberate constraint from an oversight. The predictable
outcome is that someone "fixes" a constraint and quietly breaks a guarantee.

Code comments explain *what* a line does. They are the wrong place for "we
considered three options and rejected two, here is why."

## Decision

Record every significant architectural decision as a numbered ADR in
`docs/adr/`, following Michael Nygard's format.

ADRs are **immutable once accepted**. When a decision changes, write a new ADR
that supersedes the old one and update the old one's status. The record of what
we used to believe, and why we stopped, is itself valuable.

A decision qualifies as significant if it is expensive to reverse, constrains
future work, or would look wrong without its context.

## Consequences

### Positive
- Constraints are distinguishable from accidents.
- Reversing a decision becomes a conscious act with a written counter-argument.
- Onboarding improves sharply — the *why* is written down once, not re-explained.
- Rejected alternatives are preserved, so the same debate is not relitigated.

### Negative
- Ongoing discipline. An ADR set that stops being maintained is worse than none,
  because it looks authoritative while being stale.
- Some decisions are genuinely borderline; judgement is required about what to
  record.

### Neutral
- ADRs are Markdown in the repository, consistent with everything else here.

## Alternatives considered

**Comments in the code.** Wrong scope. Decisions span files, and comments are
lost in refactors.

**A single `DECISIONS.md`.** Becomes an unnavigable wall of text, and mutable
files invite quiet rewriting of history.

**Commit messages.** Our commit messages already carry the reasoning behind each
change, and that is valuable — but nobody reads `git log` to answer "why is there
no database?". Commits record change; ADRs record standing decisions.

**A wiki.** Not versioned with the code, drifts immediately, and violates the
project's own rule that GitHub's repository is the single source of truth.
