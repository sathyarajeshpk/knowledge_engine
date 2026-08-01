---
prompt_version: 1
artifact_type: interview-questions
output: artifacts/interview-questions.md
description: Interview questions and model answers covering the feature at three depths.
---

# Task

Write interview questions and model answers about the feature described in
**Knowledge** below.

Two audiences use these: someone preparing to be interviewed, and someone
preparing to interview. Both need the *answer*, not just the question — a
question list without answers is a revision aid for people who already know the
material.

## Shape

Produce **eight questions**, graded:

- **3 foundational** — does the candidate know what this is and when to reach
  for it?
- **3 applied** — can they use it on a real problem, including the trade-offs?
- **2 probing** — the questions that separate someone who has read about this
  from someone who has run it in production. Failure modes, cost, migration,
  interaction with what came before.

For each:

```
### Q. <the question>

**Answer.** <2–5 sentences, complete enough to be marked against>

**Look for.** <what a strong answer contains that a memorised one does not>
```

## Rules

- **No trivia.** "What year was this released?" tests nothing. Every question
  should have a wrong answer that a plausible candidate would actually give.
- **Do not invent specifics.** If a number, limit or exact behaviour is not in
  the Knowledge section, ask the question in a way that does not depend on it,
  or mark it `> ⚠️ Verify:`.
- At least two questions should be answerable with "it depends, because…" —
  real systems questions usually are, and a candidate who never says so is
  worth noticing.
- Use the source's terminology exactly.

Write only the questions and answers. No preamble.
