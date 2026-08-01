# ADR-0029: Knowledge Lifecycle is separate from status

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M1
**Relates to:** ADR-0028 (identity confidence), ADR-0009 (update in place)

## Context

Gating minting on Identity Confidence (ADR-0028) created a state the engine had
no vocabulary for: knowledge that has been discovered and graded but not yet
minted. Roughly 20% of discovered items are now in it.

`status` (`active` / `replaced` / `deprecated`) cannot express this. It describes
whether *knowledge is current*, which is a question that only makes sense once an
object exists. An item waiting in the review queue has no status, because it has
no object.

Without a name, that state would be inferred from side effects — "it is in the
queue file", "it has no Feature ID" — and inferred states are the ones that go
wrong quietly.

## Decision

Introduce **`Lifecycle`**, the acquisition axis:

```
Discovered → Queued → Approved → Minted → Superseded → Archived
```

| Stage | Meaning |
|---|---|
| `discovered` | Seen in a run, not yet graded |
| `queued` | Graded and held: confidence was not high enough to mint |
| `approved` | Cleared for minting — by the gate, or by a human working the queue |
| `minted` | A permanent Feature ID exists and a knowledge object was written |
| `superseded` | A later acquisition of the same feature replaced this record |
| `archived` | Retired from the working set. Retained forever, never deleted |

**It is orthogonal to `status`:**

```
Lifecycle  answers  "how far through ACQUISITION is this?"
status     answers  "is this KNOWLEDGE still current?"
```

An object is routinely `minted` + `active`, and may be `minted` + `replaced`:
fully acquired, and superseded as knowledge. Collapsing the two would make "we
have not finished processing this" indistinguishable from "this is out of date",
which are different problems with different remedies.

**Transitions are explicit and forward-only.** `LIFECYCLE_TRANSITIONS` declares
the legal moves and `is_valid_transition` enforces them, so an illegal move fails
loudly instead of silently rewriting acquisition history. A no-op transition is
legal — re-running discovery over an already-queued item must not be an error, or
the weekly run could never be idempotent.

**Queuing never blocks.** Every high-confidence item in a run is still returned
in `mintable`. One ambiguous row must not be able to stall a weekly harvest.

**A queued item carries `first_discovered_date`.** If a Medium item is approved
weeks later and its Feature ID month came from *that* run, review latency would
silently shift a permanent identifier. `id_basis_date` falls back to
`first_discovered_date` before `discovered_date` for exactly this reason.

## Consequences

### Positive

- **The waiting state has a name**, so it can be counted, reported and drained
  rather than inferred from the absence of an ID.
- **Illegal transitions fail loudly.** Acquisition history is append-only like
  everything else, and now that is enforced rather than assumed.
- **Review latency cannot move a Feature ID.** A permanent identifier records
  when knowledge appeared, never how long a human took to look at it.
- **Two questions, two fields**, consistent with `date_precision` /
  `date_confidence` (ADR-0017) and `SourceStatus` / `HealthState`.

### Negative

- **`superseded` and `status: replaced` read similarly and are not the same.**
  Lifecycle `superseded` means *this acquisition record* was replaced by a later
  one for the same feature; `status: replaced` means *this feature* was replaced
  by a different feature. The names are close enough to be confused, and the
  glossary has to carry that. This is the weakest part of the design and worth
  revisiting in M5 when revisions and supersession are actually implemented.
- **A fourth state enum.** `Lifecycle`, `ObjectStatus`, `SourceStatus`,
  `HealthState` and `IdentityConfidence` now coexist. Each answers a genuinely
  different question, but the surface area is real.
- **Stages exist that nothing yet produces.** `superseded` and `archived` are
  written by M5 and M9. Defining them now risks getting them slightly wrong;
  defining them later risks objects minted without them.

## Alternatives considered

**Extend `status` with `queued` and `approved`.** Rejected: it would make
`status` mean two things, and the union would contain combinations that cannot
coexist (`queued` + `replaced`). Enum values that are mutually exclusive by
accident rather than by design are how invalid states become representable.

**Infer the stage from other fields** — no ID means queued, ID means minted.
Rejected: it works until it doesn't, and the failure is silent. It also cannot
express `approved but not yet minted`, which is exactly the window `ke review`
operates in.

**A boolean `is_queued`.** Rejected: it collapses at the second state, and there
are six.
