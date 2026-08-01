---
prompt_version: 1
artifact_type: infographic
output: images/infographic-prompt.md
description: A specification for an infographic, plus an image-generation prompt.
---

# Task

Produce two things for the feature described in **Knowledge** below.

Note the output path: this artifact is a **specification and a prompt**, not an
image. The engine stores text. Whatever tool renders the image is yours to
choose, and the point of writing the specification down is that it survives
changing that tool.

## Part 1 — Specification

Describe the infographic in enough detail that two different designers would
produce recognisably the same thing:

- **Headline** — six words or fewer.
- **The single idea** it must communicate. One sentence. If you cannot reduce it
  to one, the infographic is trying to do too much.
- **Layout** — the blocks, top to bottom or left to right, and what goes in each.
- **The data or comparison** shown, with actual values where the Knowledge
  section provides them. Never invented numbers: an infographic with a fabricated
  benchmark is a lie with a chart on it.
- **Text labels**, verbatim, so nothing is left to the renderer's judgement.
- **Colour and emphasis** — what should draw the eye first, second, third.

## Part 2 — Image-generation prompt

One paragraph, in a fenced block, written to be pasted into an image model.
Describe style, composition, palette and mood. Name the text that must appear,
and note that text in generated images is frequently mangled and should be
checked or overlaid afterwards.

## Rules

- **No invented numbers, ever.** If the Knowledge section has no figures, design
  a conceptual diagram instead and say that is what you have done.
- Prefer a comparison (before/after, with/without) over a decorated list. A list
  is not an infographic.
- Keep total text under 40 words. An infographic that must be read is a document.
- Accessibility: do not encode meaning in colour alone; state the intended
  contrast.

Write both parts. No preamble.
