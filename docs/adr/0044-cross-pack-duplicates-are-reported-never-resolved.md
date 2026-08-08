# ADR-0044 — Cross-pack duplicates are detected and reported, never merged, dropped or blocked

**Status:** Accepted — **amended by ADR-0046**
**Date:** 2026-08-01
**Relates to:** ADR-0014 (never auto-drop), ADR-0016 (a pack is a source
boundary and an ID namespace), ADR-0022 (deterministic output)
**Amended by:** ADR-0046 — an *acknowledged* duplicate is reported as `INFO`
rather than `WARNING`, so it no longer blocks `--strict`. Nothing in the
decision below is reversed: both objects are still kept, the engine still picks
no winner, and an *unreviewed* duplicate is still a warning.

## Context

ADR-0016 makes packs independent: separate sources, separate `seen.json`,
separate ID registries, separate taxonomies. That independence is what makes
adding a pack a data change rather than a code change.

It also means nothing stops two packs minting two permanent Feature IDs for one
real-world feature. ADR-0016 named this and deferred it, because with one pack it
could not happen. M8 adds the second pack.

The engine cannot tell a true duplicate from a legitimate double-filing. An
Azure announcement that also appears in Fabric's what's-new is often genuinely
two pieces of knowledge — filed under two taxonomies, answering two different
questions, useful to two different readers. Nothing in the data distinguishes
that from an accident.

## Decision

**The engine reports a cross-pack duplicate as a review item and does nothing
else.** It does not pick a winner, does not rewrite either object, does not
suppress the second mint, and does not delete anything. Every one of those would
either lose knowledge or make a permanent decision on a human's behalf, and both
are forbidden (CLAUDE.md, ADR-0014).

Three properties make that trustworthy:

**1. Detection is not a pipeline stage.** It reads what is on disk *after* all
packs have harvested, groups by identity, and reports. A naive implementation
that asked "is this already in another pack?" during minting would depend
entirely on which pack ran first, and would suppress a mint in one order and not
the other.

**2. The output is order-independent.** `find_duplicates` sorts packs by name
before reading anything, relies on objects already being returned in sorted path
order, and sorts each pair's sides by Feature ID before building a canonical pair
key. Harvesting Azure then Fabric produces a byte-identical report to Fabric then
Azure (ADR-0022). **Feature IDs are unaffected by pack order** — each pack mints
from its own registry, for its own items, and nothing here touches that.

**3. Resolutions live at the repository root.** A cross-pack duplicate belongs to
neither pack, so acknowledging one cannot be stored in either without the two
copies drifting. `state/cross-pack.json` is the first genuinely repo-level state
this project has needed. Because the pair key is canonical — sorted Feature IDs —
resolving from either side clears it for both.

An acknowledgement stores the evidence alongside it (both titles, URLs, packs and
the matching basis), so the record still means something if one of the objects is
later superseded or retitled.

## Consequences

**Nothing is ever lost.** Both objects are minted, stored and indexed. The worst
outcome of a false positive is a review item somebody dismisses.

**A decision is made once.** An acknowledged pair is not re-surfaced. Re-showing
a decision somebody already made every Sunday is how a review queue teaches
people to ignore it.

**`ke validate` warns, never errors** (XPK001). Failing CI over a cross-pack
duplicate would make a judgement the engine is not entitled to make. Contrast
REF001 — a reference resolving to no object in any pack — which *is* an error,
because that is unambiguously wrong.

> **Amended by ADR-0046.** Still never an error. But `ke validate` now reads the
> resolution store, so an *acknowledged* duplicate is `INFO` rather than
> `WARNING`. As written, this clause and the resolution store above contradicted
> each other under `--strict`: acknowledging cleared `ke review` and left the
> `ke validate` warning standing, with no way to clear it short of deleting real
> knowledge.

**Cross-pack references are legitimate.** Validation resolves a relationship
against the whole repository rather than one pack, so an Azure object may be a
prerequisite for a Fabric one and a typo is still caught.

**A cost, measured and accepted for now.** Because detection needs every pack,
and it runs during each pack's index rebuild, index rebuild is O(packs²) in
full-pack reads. At two packs this is four reads and immeasurable; at ten it is
3.6× the single-pack cost for identical knowledge. Documented in the M8
Performance Review with a recommendation to hoist the scan out of per-pack
rebuild before the pack count reaches roughly five — which would also fix the
related staleness noted there, where each pack sees the others as they were
before this week's harvest.

## Alternatives rejected

**Merge automatically on a strong identity match.** Deletes knowledge, makes a
permanent decision without a human, and would be wrong every time the double
filing was deliberate.

**Suppress the second mint.** Order-dependent by construction: which pack wins
depends on which ran first, and the Feature ID that never existed cannot be
recovered.

**Block the harvest until a human resolves it.** Turns a weekly unattended job
into one that stops on a judgement call. The engine's job is to make the
decision *visible*, not to wait for it.

**Store the resolution in both packs.** Two copies of one fact, guaranteed to
disagree the first time one is edited by hand.
