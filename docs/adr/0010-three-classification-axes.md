# ADR-0010: Three independent classification axes

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

The original requirement was a single classification: Tier 1, Tier 2, Tier 3. The
tiers were never defined, which forced the question of what they actually mean —
and three plausible definitions surfaced, each useful for a different purpose:

- **Operational impact.** A GA release you must act on now versus a minor
  improvement you should merely know about.
- **Learning value.** Whether an item is worth building a tutorial, interview
  question or LinkedIn post around.
- **Source authority.** Official Microsoft documentation versus a community blog.

Collapsing these into one scale loses information, and the loss is not
theoretical. A licensing change is operationally urgent (act now) but has almost
no learning value — nothing to teach, you just need to know. Conversely, an
excellent community deep-dive on Direct Lake internals has no operational urgency
at all but is high-value learning material and the best possible source for a
tutorial.

A single tier forces those two items into the same bucket or into wrong ones.

## Decision

Use **three independent fields**:

| Field | Question it answers | Values |
|---|---|---|
| `tier` | How urgently does this matter in real work? | `1` act now · `2` learn soon · `3` awareness |
| `learning_priority` | Is this worth building content around? | `high` · `medium` · `low` |
| `source_authority` | Where did it come from? | `official-microsoft` · `microsoft-community` · `third-party` |

They are computed separately and stored separately. `tier` and
`learning_priority` are engine-proposed and user-overridable; `source_authority`
is engine-owned and derived purely from which source produced the item, so it
requires no heuristics at all.

`tier` definitions:
- **1 — act now:** GA releases, breaking changes, deprecations, licensing and
  pricing changes, security.
- **2 — learn soon:** preview features, major capability additions, notable
  performance changes.
- **3 — awareness:** minor improvements, documentation refreshes, community
  content, events.

## Consequences

### Positive
- **No information is lost to collapsing.** A tier-3 item can be high learning
  priority; a tier-1 item can be low. Both are common and both are now
  expressible.
- **Different consumers use different axes.** The weekly digest groups by `tier`
  because it is answering "what do I need to act on". `ke generate` will filter by
  `learning_priority` because it is answering "what is worth teaching".
- **`source_authority` needs no rules.** It is configured per source in
  `pack.yml`, so it is always correct and never needs review.
- **Each axis can be tuned independently.** Getting tier rules wrong does not
  require re-deriving learning priority.
- **Filtering composes naturally** — "tier 1 or 2, high learning priority, not
  yet learned" is a useful query that a single axis could not express.

### Negative
- **Three fields to populate rather than one**, and two of them need rules in
  `pack.yml` (M3).
- **More for the user to understand.** Mitigated by documenting the distinction
  prominently in `docs/SCHEMA.md` §4 with the pricing-tweak example, which is the
  clearest case.
- **The two engine-proposed axes will disagree with the user sometimes.** That is
  what `overrides` (ADR-0008) is for.
- **Rule-based classification is worse than a model would manage** — a direct
  consequence of ADR-0004. Mitigated by routing unmatched items to
  `needs_review` rather than guessing.

### Neutral
- `difficulty` and `workload` are effectively two more axes, added later in the
  same conversation. They describe the *material*, whereas these three describe
  its *significance*, so they are documented as learning metadata rather than
  classification.
- Indexes are generated per axis (`by-tier.md`, `by-learning-priority.md`), which
  is cheap since indexes are fully regenerated each run.

## Alternatives considered

**A single impact-based tier.** The original proposal. Rejected as above: it
cannot express a high-value tier-3 item, which is precisely the material most
worth building tutorials from.

**A single learning-relevance tier.** Optimises the pack for content generation
but says nothing about operational urgency, so the weekly digest loses its
primary purpose — telling you what broke.

**A single source-authority tier.** Simplest and most objective to automate, and
genuinely valuable information. Rejected as the *only* axis because it says
nothing about importance: everything from the official blog would rank equally,
which is useless for prioritisation. Retained as its own field, where it is
correct by construction.

**A numeric composite score.** Combine the axes into one 0–100 ranking. Rejected:
composite scores hide their inputs, cannot be reasoned about, and the weighting
becomes an unfalsifiable opinion baked into code. Keeping the axes separate lets
a consumer weight them however it likes.
