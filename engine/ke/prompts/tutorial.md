---
prompt_version: 1
artifact_type: tutorial
output: artifacts/tutorial.md
description: A hands-on tutorial that teaches the feature by building something with it.
---

# Task

Write a hands-on tutorial for the feature described in **Knowledge** below.

The reader is a working data professional. They are competent and busy. They do
not need to be told what a data warehouse is, and they do not want to read three
paragraphs before the first useful instruction.

## Shape

1. **What this is and why it matters** — three or four sentences. Lead with what
   changed and what it now makes possible, not with background.
2. **Before you start** — prerequisites, permissions, licence tier, anything
   that will otherwise waste the reader's afternoon. If the Knowledge section
   does not state these, say which ones the reader should confirm rather than
   inventing specifics.
3. **Walkthrough** — numbered steps, each one an action with an observable
   result. Say what the reader should see after each step, so they can tell when
   something has gone wrong.
4. **A worked example** — one concrete scenario carried through the whole
   walkthrough. Invented data is fine; label it as invented.
5. **What to watch out for** — the two or three things that actually bite. Cost,
   region availability, permissions, an interaction with an older feature.
6. **Where to go next** — what to read or try after this.

## Rules

- **Do not invent specifics.** If you do not know the exact menu path, the exact
  API parameter or the exact pricing, say what the reader should look up rather
  than producing something plausible. A confidently wrong step costs more time
  than a missing one.
- **Mark uncertainty inline** as `> ⚠️ Verify:` followed by what needs checking.
- Use the terminology in the Knowledge section. If the source calls it a
  "semantic model", do not call it a "dataset".
- Code and configuration in fenced blocks with a language tag.
- Prose in plain English. No marketing register, no "unleash", no "seamlessly".
- Target 800–1,500 words. Stop when it is complete rather than padding to length.

Write only the tutorial. No preamble about what you are about to do.
