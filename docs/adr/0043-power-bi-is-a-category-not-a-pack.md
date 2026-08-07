# ADR-0043 — Power BI is a category within the Fabric pack, not a pack of its own

**Status:** Accepted
**Date:** 2026-08-01
**Supersedes:** nothing. **Amends:** the M8 line in the original plan.

## Context

The implementation plan named `domain-packs/power-bi/` (prefix `PBI`) as the
second pack, chosen to prove that adding a pack needs no engine change.

By M8 that choice had a problem the plan could not have seen. ADR-0016 defines a
pack as **a source boundary and an identifier namespace**. Microsoft publishes
Power BI announcements *inside* the Fabric release communications the
`microsoft-fabric` pack already harvests — Power BI is a workload within Fabric,
not a separate product with separate sources.

So a Power BI pack would have shared the Fabric pack's sources almost entirely.
Every Power BI announcement would have been discovered twice and minted twice,
under two permanent Feature IDs, and the two packs would have generated a
continuous stream of cross-pack duplicates that are not duplicates in any
meaningful sense — they are one announcement, filed twice, because the
configuration said to.

That would have tested the cross-pack duplicate machinery thoroughly and tested
**pack independence** not at all.

## Decision

**Power BI is a category within the `microsoft-fabric` pack.** ADR-0016 stands
unamended: a pack is a source boundary, and Power BI does not have one.

**Azure (prefix `AZ`) is the second pack.** It has genuinely separate sources
(`https://www.microsoft.com/releasecommunications/api/v2/azure/rss`), its own
taxonomy, its own release cadence and its own vocabulary. It is an independent
real-world domain rather than a slice of an existing one.

## Consequences

**The milestone tests what it was meant to test.** Onboarding Azure exercised
independent sources, an independent ID namespace, an independent taxonomy and
independent state. `git diff engine/` for the Azure pack commit is **zero
files** — 200 knowledge objects, 10 categories and 29 classification rules
added, no engine change of any kind.

**Cross-pack duplicates became a real signal rather than noise.** Fabric and
Azure overlap occasionally and legitimately — an Azure networking announcement
that also matters to Fabric — which is exactly the case the detection in
ADR-0044 is designed to surface for a human. Had Power BI been the second pack,
the duplicate report would have been permanently full and permanently ignored.

**Power BI knowledge is not lost or deferred.** It is classified under a
`power-bi` category in the Fabric pack and is findable by
`ke search --category power-bi` like any other category.

**The rule this generalises to**, for `docs/ADDING-A-PACK.md`: *if a candidate
pack would share sources with an existing pack, it is a category, not a pack.*
Separate sources are the test, because sources are what a pack actually
partitions.

## Alternatives rejected

**Ship the Power BI pack as planned.** Would have satisfied the letter of the
milestone while proving nothing about independence, and would have permanently
polluted the cross-pack review queue.

**Ship both Power BI and Azure.** Adds the cost above for no additional
evidence; one genuinely independent pack proves pack-agnosticism as well as two.

**Split Fabric's sources so Power BI has its own.** Filtering one feed into two
packs by keyword makes a pack a *view* rather than a source boundary,
contradicting ADR-0016 to preserve a name.
