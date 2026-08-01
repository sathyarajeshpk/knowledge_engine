# ADR-0017: `date_precision` separate from `date_confidence`

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

The Microsoft Learn "What's New" page — M1's primary source — dates updates to a
**month**, not a day. The schema had one date-quality field, `date_confidence`
(`exact` | `inferred`), and no way to express "we know the month exactly".

Both available workarounds lose information:

- Mark it `inferred` — wrongly implies we guessed, and degrades Feature ID
  minting to the discovery month when the publication month is known exactly.
- Store `2026-07-01` as an exact day — quietly false, and indistinguishable from
  a genuine 1 July publication.

The two properties are simply different questions: *do we trust this date?* and
*how precise is it?*

## Decision

Add **`date_precision`** (`day` | `month` | `year`) as an independent
engine-owned field. `date_confidence` is unchanged.

`published_date` always stores a real date — the first of the month for month
precision, the first of January for year precision — so ordering stays
deterministic and no consumer needs special cases to sort. `date_precision` says
how much of that date to believe.

Feature ID minting is unaffected: ADR-0005 needs the publication *month*, and
month precision supplies exactly that. `RawItem.id_basis_date` consults only
`date_confidence`.

## Consequences

### Positive
- A month-precise date is recorded as what it is: exactly known, to the month.
- Feature IDs stay correct for the primary source instead of falling back.
- Sorting, filtering and range queries need no special handling.
- Display can be honest — "July 2026" rather than a fake "1 July 2026".
- Extends naturally to `year` for roadmap-style content.

### Negative
- One more required field, and one more thing to get right in every adapter.
- Consumers that ignore it will render `2026-07-01` and be slightly wrong. The
  field is required precisely so the information is always available.
- Defaults to `day` when absent, which is the safest reading of a file written
  before the field existed.

### Neutral
- No `hour` precision. Nothing in scope publishes at that granularity.

## Alternatives considered

**Overload `date_confidence` with a `month` member.** Rejected: it would make
"trusted" and "precise" the same axis, so a *guessed* month would be
inexpressible — the same mistake one level down.

**A nullable `published_month` alongside `published_date`.** Rejected: two fields
that can disagree, and every consumer must check both.

**Store an ISO partial string (`"2026-07"`).** Rejected: not a date, so sorting
and comparison stop being free and every reader needs a parser.
