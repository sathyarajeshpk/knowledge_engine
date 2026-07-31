# ADR-0004: No AI model in the scheduled pipeline

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

The project's purpose involves AI heavily: generating tutorials, interview
questions, LinkedIn posts, quizzes, coding examples, architecture explanations
and infographic prompts. The obvious design calls a model during the weekly run
to summarise and classify each item.

Three constraints make that obvious design wrong.

**Cost.** The target is ₹20/month — about $0.24. Any per-item API call across
nine packs exceeds that immediately, and the moment the system costs money it
acquires a business case, a budget review, and a reason to be switched off.

**Vendor independence.** `CLAUDE.md` requires the implementation stay
AI-model-independent. A provider adapter is not independence — it is
indirection. The provider still shapes the output, the rate limits, the
availability, and the terms.

**Determinism.** A pipeline that calls a model is not reproducible. The same
input produces different output on different days, which makes it untestable,
hard to debug, and impossible to reason about.

## Decision

**The scheduled pipeline never calls an AI model.** Not through a free tier, not
behind an adapter, not optionally.

The weekly run does exactly this, all deterministically: discovery,
deduplication, classification, metadata extraction, Markdown generation,
indexing, and weekly summaries.

AI is invoked **on demand only**. `ke generate` assembles a self-contained
Markdown *context pack* — the prompt template, the knowledge object, its
prerequisites and source links — which the user pastes into whatever model they
prefer. The engine never holds an API key.

This is enforced by a CI step that fails the build if any workflow with a
`schedule:` trigger invokes `ke generate`. That guard was added in M0, before the
weekly workflow it constrains exists.

## Consequences

### Positive
- **Running cost is genuinely zero.** Not "cheap" — zero. No account, no key, no
  billing relationship.
- **Vendor independence is structural, not aspirational.** There is no provider
  to be independent of. Claude, ChatGPT, Gemini, Kimi and models not yet built
  all work identically, because the interface is Markdown and copy-paste.
- **The pipeline is a pure function** — testable, reproducible, debuggable. Every
  M0 test runs in 0.6 seconds with no network.
- **No rate limits, quotas, outages or terms-of-service changes** in the critical
  path.
- **Generated content is always deliberate.** The system will never quietly fill
  the repository with machine-written text nobody asked for.

### Negative
- **Classification is rule-based, so it is worse than a model would manage.**
  Mitigated by routing anything unmatched to `needs_review` rather than guessing
  silently, and by keeping the rules in `pack.yml` as tunable data.
- **Summaries are extracted, not written.** A model would produce better prose.
  Partly offset by ADR-0003's requirement to store only short summaries anyway.
- **Generation requires a manual step.** Copy, paste, paste back. This is real
  friction, accepted deliberately: it is the price of the three benefits above,
  and it keeps a human in the loop on what enters the knowledge base.
- **Relationships cannot be auto-derived**, since that genuinely needs a model.
  They are user-curated, with the engine only proposing candidates into a review
  queue.

### Neutral
- `GenerationEntry.model` records which model produced an artifact, for
  provenance. Nothing in the engine reads it — that is what makes it provenance
  rather than a dependency.
- The manual step could be automated later by a user-run local script. That would
  be their choice, outside the scheduled pipeline, and the CI guard would still
  hold.

## Alternatives considered

**A free-tier LLM adapter** (Gemini, Groq) for summarisation and classification.
Rejected: free tiers change, impose quotas, require key management, and reintroduce
non-determinism. "Free today" is not a foundation, and an adapter is not
independence.

**A local model** (Ollama, llama.cpp) in the runner. Rejected: GitHub Actions
runners cannot run a useful model in reasonable time on the free tier, and it
would make the pipeline non-deterministic for no cost saving over doing nothing.

**AI for classification only**, not summarisation. Rejected: same three problems
at smaller scale, and classification is the part most amenable to rules.

**Optional AI, off by default.** Rejected: an off-by-default path is an untested
path. It would also make the CI guard meaningless, and the guarantee "this system
never calls a model" is worth more than the flexibility.
