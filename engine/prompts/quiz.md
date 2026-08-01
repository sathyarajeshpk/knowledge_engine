---
prompt_version: 1
artifact_type: quiz
output: artifacts/quiz.md
description: A self-test on the feature, with answers and explanations kept separate.
---

# Task

Write a self-test on the feature described in **Knowledge** below.

The reader is testing themselves. That means the answers must be reachable but
not visible while answering, and every wrong option must be one a person could
genuinely believe.

## Shape

**Part 1 — Questions.** Ten items:

- 6 multiple choice, four options each
- 2 true/false, each with a one-line "why" required from the reader
- 2 short answer

**Part 2 — Answers.** Below a `---` rule and a `## Answers` heading, so the
reader can stop scrolling. For each: the correct answer, and **one sentence on
why each wrong option is wrong**.

## Rules

- **Distractors must be plausible.** An option nobody would pick tests nothing
  and turns a four-option question into a two-option one. Good distractors are
  the previous behaviour, an adjacent feature, or a reasonable-sounding
  limitation that does not apply.
- **No trick questions**, and no answers that hinge on a word like "always" or
  "never" unless the distinction is genuinely the point.
- **Do not invent specifics.** Every question must be answerable from the
  Knowledge section. If you cannot write ten such questions, write fewer and say
  how many you wrote.
- Vary what is being tested: what it does, when to use it, what it costs, what
  it replaces, what it requires.
- Do not number the correct option consistently.

Write only the quiz. No preamble.
