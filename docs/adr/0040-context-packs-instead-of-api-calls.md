# ADR-0040: Context packs instead of API calls

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M7

## Context

ADR-0004 established that the scheduled pipeline never calls an AI model. M7 is
where the other half of that sentence gets built: the on-demand path, where AI
*is* used.

The obvious implementation is an API client. `ke generate tutorial --id X` calls
a model, gets a tutorial back, writes it to `artifacts/tutorial.md`. One command,
no copy-paste, no human in the loop. It is what every comparable tool does.

It would also undo most of what the previous six milestones were for.

## Decision

**`ke generate` assembles a self-contained Markdown document and prints it. A
human pastes it into a model, reads the answer, and pastes it back with
`--attach`.**

The engine never makes an AI API call, on any code path, on demand or otherwise.

A context pack contains everything a model needs and nothing it has to be told
twice:

* the instruction, from a versioned template in `ke/prompts/`
* the metadata a model would otherwise guess at — category, tier, difficulty,
  publication date and its precision, source authority
* the stored article
* related objects, as one summary line each
* the source link, and an explicit statement that the article is an original
  summary rather than the source's text

The acceptance criterion from the plan is the definition of done: *paste it into
a fresh model session with no other context and confirm the result is usable.*

## Consequences

### What this buys

**Zero running cost, permanently.** No API key, no bill, no rate limit, no quota
to run out of on a Sunday. The whole system's cost stays at ₹0/month, which was
a design constraint rather than thrift: a system with no running cost never needs
a business case and never gets switched off.

**Genuine vendor independence, not adapter-shaped independence.** An abstraction
layer over three model APIs is still a dependency on those three APIs existing,
keeping their contracts, and remaining affordable. A Markdown document works in
every model that exists, including ones announced after this was written, and it
keeps working if a vendor disappears overnight.

**A human reads every artifact before it is stored.** This is the consequence I
would defend hardest. Everything generated here is plausible-sounding prose about
a technical subject — the exact category where a wrong answer is hardest to spot
and most expensive to act on. An automated path would produce a repository slowly
filling with confident errors that nobody had reason to doubt.

The copy-paste step is not friction to be optimised away later. It is the
quality control.

**Prompts are inspectable data.** Seven Markdown files a person can read, edit
and version, rather than strings buried in code.

### What it costs

**It is manual.** Generating artifacts for fifty objects is fifty round trips.
There is no batch mode and cannot be one.

**No programmatic pipeline.** "Generate a tutorial for everything tagged
`direct-lake`" is not expressible. `ke search --ids-only` makes the loop
scriptable up to the point where a human has to paste.

**The prompts cannot exploit any model's particular strengths.** No structured
output, no tool use, no vendor-specific formatting that would raise quality on
one platform. Asserted by a test that scans the templates for vendor syntax,
because this erodes gradually and for good reasons each time.

**A generated artifact's quality is unverifiable by the engine.** It records
`model` and `prompt_version` as provenance and reads neither. If an artifact is
wrong, only a person will notice.

## Alternatives considered

**An API client behind a provider interface.** Rejected. It reintroduces cost,
keys, rate limits and vendor coupling; the "provider interface" only moves the
dependency rather than removing it. It also removes the human review step, which
is the part that keeps the repository trustworthy.

**Optional API mode, off by default.** Superficially the best of both. Rejected
because an optional path is a maintained path: it needs a client, credential
handling, retry logic, error mapping and its own security surface, all for a
feature whose default is off. And the moment it exists, the temptation to run it
across the whole pack is one flag away.

**Local model inference.** No API cost and no vendor. Rejected on hardware
assumptions — it makes the engine unusable on a machine that cannot run a model,
and quality at the sizes that run comfortably on a laptop is not good enough for
knowledge intended to be kept for years.

**Storing prompts in `pack.yml`.** Rejected: a prompt is engine behaviour, not
knowledge. Every pack should get the same instruction for the same artifact type,
and a per-pack prompt is a per-pack fork of the engine's behaviour.
