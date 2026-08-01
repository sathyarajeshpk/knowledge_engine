---
prompt_version: 1
artifact_type: coding-example
output: artifacts/coding-example.md
description: A minimal runnable example demonstrating the feature, with the failure modes.
---

# Task

Write a minimal, runnable code example for the feature described in
**Knowledge** below.

If the feature has no code surface — a licensing change, a UI improvement, a
regional rollout — say so in one line and stop. A fabricated API for a feature
that has none is worse than no example.

## Shape

1. **What this example does** — two sentences.
2. **Setup** — what must exist first: workspace, capacity, permissions,
   installed packages, environment variables. Real names where the Knowledge
   section gives them; placeholders in `<angle-brackets>` where it does not.
3. **The example** — one fenced block with a language tag. Minimal means
   *minimal*: no logging framework, no argument parsing, no class hierarchy.
   Every line should be there because removing it breaks the demonstration.
4. **Expected output** — what running it actually prints or produces.
5. **Common failures** — two or three real ones, each with the error you would
   see and what it means. Missing permission, wrong capacity SKU, region
   mismatch.
6. **Making it production-ready** — a short list of what this example
   deliberately omits: error handling, retries, secret management, idempotency.

## Rules

- **Do not invent API surface.** If you do not know the exact method name,
  parameter or endpoint, write the call with a `> ⚠️ Verify:` note naming what
  to check in the documentation. Plausible-looking code that does not exist is
  the single most expensive thing you can produce here.
- Prefer the language the source uses. Otherwise Python for data work, T-SQL for
  warehouse work, PowerShell or REST for administration.
- Comment *why*, not *what*. `# Direct Lake requires the table to be V-Ordered`
  earns its place; `# create the client` does not.
- Secrets come from the environment. Never a literal, never a placeholder that
  looks like a real key.

Write only the example document. No preamble.
