---
prompt_version: 1
artifact_type: architecture-explanation
output: artifacts/architecture-explanation.md
description: How the feature works underneath, and what it changes about system design.
---

# Task

Explain how the feature described in **Knowledge** below actually works, and
what it changes about the way a system using it should be designed.

This is the artifact for the reader who already knows *what* the feature does
and wants to know *why it behaves the way it does* — the one who needs to decide
whether to adopt it.

## Shape

1. **The problem it solves** — what the architecture looked like before, and
   which part of it was unsatisfying. Be specific about the cost: an extra copy
   of the data, a refresh window, a permissions boundary in the wrong place.
2. **How it works** — the mechanism, at the level of "what moves where and
   when". Enough that the reader can predict its behaviour in a case you have
   not described.
3. **A diagram** — Mermaid, in a fenced ` ```mermaid ` block. `flowchart` for
   components and data movement, `sequenceDiagram` for an ordered interaction.
   Keep it to what the prose already established; a diagram that introduces new
   nouns is a second explanation competing with the first.
4. **What it costs** — latency, money, operational complexity, a new failure
   mode, a new thing to monitor. Every architectural choice trades something.
5. **When to use it and when not to** — two short lists. The "not" list is the
   more valuable one and the one usually missing.
6. **What it interacts with** — the existing features whose behaviour changes,
   or whose limits now bind.

## Rules

- **Do not invent internals.** If the mechanism is not described in the
  Knowledge section, say what is publicly known and mark the rest
  `> ⚠️ Verify:`. Confident invented internals are the most damaging possible
  output of this prompt, because they are exactly what a reader cannot check.
- Distinguish "the source says" from "this generally implies" in the text.
- No marketing framing. If the honest answer is "this is a modest improvement
  to an existing path", write that.
- Target 700–1,200 words.

Write only the explanation. No preamble.
