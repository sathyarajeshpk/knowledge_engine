# Knowledge Engine

This repository contains the Knowledge Engine project.

The AI assistant working on this repository must:

- Never delete existing knowledge.
- Always preserve chronological order.
- Store knowledge as Markdown.
- Use GitHub as the single source of truth.
- Avoid duplicate Feature IDs.
- Build incrementally.
- Prefer simplicity over complexity.
- Minimize operating costs.
- Keep the implementation AI-model independent.

## Clarifications

These make the rules above precise enough to enforce. `ke validate` checks them
in CI; see `docs/SCHEMA.md` for the full contract.

**Corrections are revisions, not rewrites.** When a source changes, update the
existing knowledge object's engine-owned fields and append a revision entry.
Never rewrite history and never remove a file. An object superseded by a genuinely
new feature is marked `status: replaced` and stays in the repository. Generated
artifacts whose knowledge has moved on are marked `stale`, never deleted.

**The scheduled pipeline must never call an AI model.** The weekly run is
deterministic: discovery, deduplication, classification, metadata extraction,
Markdown generation, indexing, and summaries. AI is invoked on demand only, for
tutorials, LinkedIn posts, interview preparation, coding examples, architecture
explanations, quizzes, and infographic prompts. This is what keeps the engine
vendor-independent and the running cost at zero.

**The engine must never write user-owned fields.** Learning state
(`learning_status`, `notes`), relationships, and everything under `artifacts/`,
`images/` and `references/` belong to the user. Engine-proposed fields
(`tier`, `difficulty`, `tags`, and the rest) may be locked by naming them in
`overrides`. All automated writes go through
`KnowledgeObject.with_engine_fields()`, which refuses anything else.

**Never store the full text of a third-party article.** Store a short original
summary and a link. This is a repository-size decision and a copyright one.

**Feature IDs and object paths are permanent.** Once minted, a Feature ID never
changes and is never reused, including for replaced objects. A knowledge
object's directory path is stable for its entire lifetime.
