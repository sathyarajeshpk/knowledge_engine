# Prompt templates

One file per artifact type. Each is a plain Markdown instruction with a small
YAML front matter block:

```yaml
---
prompt_version: 1
artifact_type: tutorial
output: artifacts/tutorial.md
description: A hands-on tutorial that teaches the feature by building with it.
---
```

`ke generate <type> --id <id>` reads the template, appends the knowledge object
and its context beneath it, and prints the result. You paste that into any model.

## Why there is no templating language

There are no `{{placeholders}}` and no substitution step. The template is
instruction; the context is appended below it under a heading the instruction
refers to. That is the whole mechanism.

A templating engine would be a second thing to learn, a second thing to get
wrong, and a second reason a prompt could fail at generation time rather than at
review time. Concatenation cannot fail.

## Why the templates are model-agnostic

Nothing here uses vendor-specific syntax — no system/user role markers, no XML
tags a particular model was trained on, no function-calling schema, no
"thinking" directives. The output of `ke generate` is Markdown that reads
correctly pasted into any chat window, which is the property that makes this
engine AI-vendor-independent in practice rather than only in principle
(ADR-0004).

The cost is that the prompts cannot exploit a specific model's strengths. That
is the intended trade.

## Why `prompt_version` exists

An artifact records the `prompt_version` that produced it. When a template is
improved, existing artifacts do not become wrong — they become *older*, and the
version records which instruction produced them. Bump it whenever a change would
plausibly alter the output.

`prompt_version` is engine-owned metadata about the artifact. The artifact
content itself is user-owned and the engine never rewrites it (ADR-0008).

## Adding a type

1. Add a member to `ArtifactType` in `models.py`.
2. Add `<type>.md` here, with front matter whose `artifact_type` matches.
3. Nothing else. `ke generate` discovers templates by name, and
   `test_prompts.py` asserts the two sets stay in step.
