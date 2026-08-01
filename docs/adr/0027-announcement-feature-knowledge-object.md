# ADR-0027: Announcement, Feature and Knowledge Object are three things

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1
**Relates to:** ADR-0023 (identity hierarchy), ADR-0009 (one ID per concept)

## Context

ADR-0023 established a deterministic hierarchy for item identity, anchored on the
canonical URL. It has held up well — measured across two independent
representations of the same source, URL-anchored identity agrees with itself 98%
of the time.

But it rests on an assumption it never states: **one URL identifies one update.**
That is true for feeds, where an entry has its own permalink. It is false for
curated update tables, and M1's validation measured how false.

Against the live Fabric "What's New" source:

* 315 rows resolve to 214 distinct announcement URLs
* 65 URLs are cited by more than one row
* 49 of those are the same feature listed twice — correct de-duplication
* **16 are genuinely different features sharing one URL**
* **75% of all items cite an announcement blog post** rather than a doc page

The largest single announcement — a monthly feature-summary post — is cited by
**18 rows covering 11 independent features**.

Under the previous model those eleven features share one identity. M2 would have
minted **one** permanent Feature ID and stored one object; the other ten features
would have been silently absent. Not flagged, not queued — absent. Feature IDs
are never reused (ADR-0005), so that loss could never be repaired.

The root cause is a missing distinction: the engine treated *where knowledge was
reported* as *what the knowledge is*.

## Decision

Model three concepts, not one.

| Concept | Is | Identified by |
|---|---|---|
| **Announcement** | a published artifact that *reports* features — blog post, doc page, release note | canonical URL |
| **Feature** | the unit of knowledge; **owns the permanent Feature ID** | announcement + feature name |
| **Knowledge Object** | the stored, versioned record of a Feature | its permanent directory path |

An Announcement has a **one-to-many** relationship with Features. A Feature has a
one-to-one relationship with a Knowledge Object.

**An announcement URL is a citation, not an identity.**

Concretely:

1. **`announcement_url` is a separate, explicitly nullable field.** It is *not*
   defaulted to `source_url`, which falls back to the source document when a link
   cannot be resolved. The document being read is not an announcement, and
   recording it as one invents a citation that does not exist. "This feature has
   no announcement" is a fact worth representing.

2. **Identity computation is unchanged** for now. ADR-0023's hierarchy still
   picks the anchor. What changes is what happens when an anchor is ambiguous.

3. **A collision is never a merge.** When one identity is claimed by several
   distinct normalised feature titles, the engine surfaces a `Collision` for
   review. It does not merge them, and it does not drop any of them.

4. **Category and content are excluded from identity, permanently.** Both were
   evaluated and rejected on measurement:

   * **Category** is many-to-many and time-varying. 67 of 242 features (28%)
     appear under more than one section, so including it would mint 2–3 IDs per
     feature; and sections encode lifecycle state ("Features currently in
     preview" / "Generally available features"), so a feature reaching GA would
     move sections and mint a second ID — contradicting **ADR-0009**. Measured,
     it produced 315 identities from 315 rows, destroying de-duplication
     entirely.
   * **Normalised content** is the same input `content_hash` uses to detect that
     a feature *changed* (ADR-0020). If it also determined identity, then "this
     changed" and "this is different" would be one signal, and revisions could
     never be recorded at all.

Full analysis and measurements: `docs/design/IDENTITY_MODEL.md`.

## Consequences

### Positive

- **The most expensive available failure is closed.** Distinct features can no
  longer be silently merged under one permanent ID.
- **The model matches reality**, so future questions have somewhere to be
  answered. "How many features did this announcement cover?" is now expressible.
- **Nothing is lost.** Collisions are queued for a human; the alternative was
  silent absence.
- **The rejections are recorded with evidence**, so category and content will not
  be re-proposed on intuition.

### Negative

- **The merging defect is contained, not solved.** Recommendation C surfaces
  collisions rather than resolving them; 16 announcements covering ~28 features
  currently require human triage. Solving it means putting the feature title into
  identity, which is deferred (see below).
- **A new field on every item.** `announcement_url` duplicates `source_url` for
  the majority of items where the anchor did resolve. That redundancy is the
  price of being able to tell "resolved to itself" from "could not resolve".
- **Review queue depth is now a thing that can be neglected.** A queue nobody
  drains is a slower kind of data loss. M2 needs a path to drain it.

### Neutral

- Adding the feature title to identity — the change that would genuinely
  supersede ADR-0023 — is **deferred, not rejected**. Measured, it eliminates
  merging completely but drops cross-representation agreement from 98% to 35%,
  which means failing over to a secondary source would churn two-thirds of all
  Feature IDs. That is dominated by inconsistent title extraction between our own
  adapters, which is fixable. The decision waits on evidence
  (`IDENTITY_MODEL.md` §7.2).

## Alternatives considered

**Composite identity (URL + title) now.** Correct model, wrong moment. Rejected
on the failover evidence above: it would convert the fallback chain — built to
survive Learn becoming unreachable — into a mass duplicate-ID generator that
fires automatically with no human present.

**Treat announcement hosts as non-identifying.** Proposed in the M1 architecture
review and withdrawn: measurement showed 3 of 16 merging groups are
`learn.microsoft.com`, so it fixes only ~80% of the defect while pushing 75% of
the pack onto title-derived identity. Most of the risk, less of the benefit.

**Merge only when titles also match within the run.** Rejected: run-dependent, so
an identity would change when a row stopped appearing. Permanence forbids it.

**Ask a model whether two rows are the same feature.** Forbidden by ADR-0004, and
it would make identity non-deterministic — worse than imperfect.
