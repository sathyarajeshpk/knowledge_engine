# ADR-0016: One Fabric pack, with Power BI as a first-class category

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

The original vision listed Microsoft Fabric and Power BI as separate Domain
Packs, with permanent prefixes `MSF` and `PBI`. That raised a question flagged as
the highest-severity open item in the project: **when Power BI content appears in
a Fabric source, which pack owns it?**

The question could not be deferred. Feature IDs are permanent and never reused
(ADR-0005), so a wrong answer produces cross-pack duplicates that can never be
cleaned up.

Two facts settled it, one architectural and one empirical.

**Architectural.** `seen.json` is per-pack (`engine/ke/pack.py`). Nothing today
prevents the same canonical URL being ingested into two packs and receiving two
permanent identifiers. Two packs drawing on overlapping sources is therefore not
merely untidy — it is a correctness problem with no recovery path.

**Empirical.** Source validation on a GitHub runner
([`M1_SOURCE_VALIDATION.md`](../reviews/M1_SOURCE_VALIDATION.md)) found **no
working Power BI blog feed at all**. Every `powerbi.microsoft.com` feed returns
403 from a runner, and the alternatives return 404. The only Power BI–specific
source that passes is `powerbi-docs-commits-atom` — Git merge commits carrying no
knowledge. A Power BI pack created today would be fed almost entirely by
plumbing noise.

There was also a conflation worth naming. **Packs and topics are different
things.** A pack is a source boundary and an identifier namespace. A topic is
something you filter an index by. Treating "I want to see only Power BI content"
as a *pack* requirement, when it is an *index* requirement, is what made the
boundary look necessary in the first place.

## Decision

**One Domain Pack, `microsoft-fabric`, prefix `MSF`, covering the whole Fabric
platform including Power BI.**

- `power-bi` becomes a first-class **category** and tag, replacing the narrower
  `power-bi-integration`. A Power BI view is an index filter, not a separate
  pack.
- Power BI sources (`powerbi-docs-*`) are configured **inside** the Fabric pack.
- The **`PBI` prefix is reserved and unused.** It is not retired.

A separate Power BI pack may be created later if Microsoft provides sufficiently
independent sources, or if the two products diverge. Because identifiers are
permanent, such a split would apply to **new content only** — existing `MSF`
objects keep their identifiers and stay where they are. Nothing is renamed, and
nothing is lost.

## Consequences

### Positive
- **Cross-pack duplicate identifiers become impossible for these two products**,
  because there is only one namespace. This is the whole point.
- **No cross-pack dedupe machinery is needed**, so `seen.json` stays per-pack and
  the architecture stays simple (CLAUDE.md: prefer simplicity).
- **No per-item ownership judgement is required.** ADR-0004 forbids AI in the
  pipeline, and judging "is this a Power BI feature or a Fabric feature?" is
  exactly the kind of call rules make badly. The question no longer arises.
- **A monthly summary covering both products stays one object**, as it should —
  it is one article.
- **Matches the product reality.** Power BI is a Fabric workload. The knowledge
  base now mirrors how Microsoft actually ships.
- **Power BI remains fully addressable** via `category: power-bi`, tags, and a
  generated `by-category` index.

### Negative
- **The nine-pack plan becomes an eight-pack plan** until a Power BI split is
  justified. `docs/ROADMAP.md` is updated to reflect this.
- **`MSF` identifiers will be attached to pure Power BI knowledge**, which reads
  slightly oddly. Accepted: identifiers denote a namespace, not a subject, and
  the alternative is duplicates.
- **If Power BI later separates cleanly, the pack will contain historical Power
  BI content that stays under `MSF`.** A future `PBI` pack would start from that
  point forward, so the same topic would span two prefixes by era. Ugly, but
  honest and lossless — and far cheaper than renaming permanent identifiers,
  which is not possible.
- **One pack grows faster**, since it absorbs both products' volume.

### Neutral
- `pack.yml` gains `power-bi` as a category and drops `power-bi-integration`. No
  objects exist yet, so there is nothing to migrate.
- The `PBI` prefix remains documented as reserved so it is not reused by another
  pack.

## Alternatives considered

**Two packs, source decides ownership.** Each source assigned to exactly one
pack; fully deterministic, no judgement, preserves the original plan. Rejected on
the evidence: the Power BI pack would launch fed only by merge commits, and
because `seen.json` is per-pack, any announcement reaching both packs would mint
two permanent identifiers. Deterministic, but deterministically producing
duplicates.

**Two packs, topic decides ownership.** Classify by subject regardless of source.
Conceptually the cleanest. Rejected: it requires per-item judgement that ADR-0004
rules out, misclassification is unrecoverable once an identifier is minted, and a
single monthly summary covering both products cannot be assigned to one pack at
all without splitting the article.

**Two packs plus a global cross-pack dedupe registry.** Would prevent the
duplicate-identifier problem directly. Rejected as premature: it introduces
cross-pack state, contradicting the per-pack state model, to solve a problem that
disappears entirely under this decision. Worth revisiting only if two packs with
genuinely overlapping sources ever become necessary.

**Retire the `PBI` prefix.** Rejected. Reserving it costs nothing and keeps a
future split available.
