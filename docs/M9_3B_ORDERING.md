# M9-3b — Discovery ordering, and the final fix design

**Status:** **APPROVED 2026-08-08.** Decisions recorded in §8; implementation follows.
**Engine inspected:** merged `main` at `dce0840`

---

## 1. Is discovery order guaranteed by contract, or merely stable today?

**Guaranteed by contract. [measured, by inspection]**

`sort_items` in `engine/ke/acquisition/sources/base.py`:

```python
return sorted(items, key=lambda item: (
    item.published_date is None,
    item.published_date or date_min(),
    item.identity.key,
))
```

Its docstring states the contract explicitly and names the reason:

> *"Same inputs must always produce byte-identical output (ADR-0022), so every
> adapter sorts before returning… the key breaks ties without ever depending on
> the order the source happened to present things in."*

Four things make it a genuine guarantee rather than an accident:

1. **It is applied twice.** Every adapter sorts before returning (`feed.py:72`,
   `html_table.py:169`, `markdown_table.py:149`), and `discover_all` sorts the
   merged result again (`discover.py:145`). Source declaration order in
   `pack.yml` therefore does not leak into item order.
2. **It is a total order.** The final key is `identity.key`, a SHA-256 that is
   unique per item, so no two distinct items can tie.
3. **It is stable across machines and runs.** Every component is derived from
   item content — no timestamps, no memory addresses, no set iteration.
4. **Nothing downstream re-orders.** `dedupe.classify` iterates items in the
   order given and appends decisions in that order, so `ctx.decisions` inherits
   it. Confirmed by inspection of `dedupe.py` and `pipeline.py`.

So positional "first wins" **would** be deterministic. ADR-0022's requirement is
met by the discovery path.

---

## 2. Why I am nevertheless recommending against positional order

Determinism was the question asked. It is not the only thing that matters, and
the sort key is the reason.

**The primary sort key is `published_date` — the very field in dispute.**

In the Layer-2 duplicate case, two sightings of one feature disagree about the
publication date. That disagreement is what produces the second update. Under
positional first-wins the winner is whichever sighting reports the **earlier**
date, because that is what `sort_items` puts first.

So the resolution rule would be *"the sighting claiming the earlier publication
date wins"* — deterministic, and semantically arbitrary. It also systematically
prefers the **staler** of two dates, which is the opposite of what a reader
would expect, and it makes the outcome a function of the contested value itself.

This is the kind of dependency that is correct today, invisible in review, and
breaks the moment somebody reorders the sort key for an unrelated reason.

---

## 3. Source authority is **not** an existing precedence invariant

Checked before considering it, per instruction. **[measured]**

`source_authority` appears in exactly one behavioural place —
`models.py:954`, in the minting gate:

```python
return (self.identity_confidence is IdentityConfidence.HIGH
        and self.source_authority is SourceAuthority.OFFICIAL_MICROSOFT)
```

That is a **binary trust gate** ("may M2 mint without a human?"), not a ranking.
There is no defined ordering among authority values anywhere in the engine, and
the docstring beside it explicitly warns against folding the two concepts
together.

**So an authority-based tie-break would require inventing a precedence
invariant that does not exist.** Not proposed.

---

## 4. Selection invariant (approved)

Recorded as an **invariant**, not an implementation detail:

> **At most one update decision is applied to a stored object per run, regardless
> of which identity layer produced the match.**
>
> **When multiple decisions match the same stored object, the decision with the
> lowest `identity.key` is selected.**

### Why this tie-break

```
Among decisions matching the same stored object, apply the one whose
item has the lowest identity.key. Count the rest as `unchanged`.
```

| Property | |
|---|---|
| Deterministic | Yes — SHA-256, unique per item, content-derived |
| Depends on a disputed field | **No** — this is the point |
| Invents a new invariant | No — it is already `sort_items`' documented final tiebreaker |
| Stable across machines/runs | Yes |
| Changes existing behaviour | **No.** Two decisions sharing one identity key are already collapsed by the guard; the tie-break can only fire when two *different* identities match one object, which is exactly the case being fixed |

It is the simplest rule that is both deterministic and independent of the values
under dispute.

**Honest cost:** the winning sighting is chosen by a hash, which carries no
meaning. It is arbitrary — but *explicitly and stably* arbitrary, rather than
arbitrary via a hidden dependency on the contested date. If a meaningful
precedence is wanted later, it should be introduced deliberately as its own
invariant, with its own ADR.

---

## 5. Final fix expression

Not a one-liner after all. Keying `handled` on `decision.matched` alone would
still resolve positionally — the first decision for an object wins — so the
selection has to be explicit:

```python
def update_existing(ctx: HarvestContext) -> None:
    # One update per STORED OBJECT per run, not one per identity.
    #
    # Two items can carry two identity keys and resolve to one object: Layer 1
    # matches on identity, Layer 2 on content fingerprint, and an item reaching
    # Layer 2 necessarily has a different identity key from the stored object's.
    # Keying this guard on the item let both through, and the object took two
    # revisions in one run (M9-3a).
    #
    # The winner is the lowest identity key rather than the first decision:
    # `sort_items` orders by published_date first, which in exactly this case is
    # the field the two sightings disagree about, so positional order would make
    # the winner a function of the disputed value.
    by_object: dict[str, list[Decision]] = {}
    for decision in ctx.decisions:
        if decision.is_new or not decision.matched:
            continue
        by_object.setdefault(decision.matched, []).append(decision)

    for feature_id in sorted(by_object):
        candidates = by_object[feature_id]
        winner = min(candidates, key=lambda d: d.item.identity.key)
        ctx.report.unchanged += len(candidates) - 1
        _update_one(ctx, winner)
```

Still no schema change, no stored-file change, no Feature ID change, no CLI
change. `sorted(by_object)` keeps the processing order of objects deterministic
too.

---

## 6. Regression test design

Six tests. Every one mutation-verified before it is claimed as a guard.

**T1 — the reproduction, as specified**
Store an object. In **one** run, present two items: one matching by identity
(Layer 1), one matching the same object by content fingerprint from a different
URL (Layer 2), both differing from stored so both would record a change. Assert
**exactly one revision** is appended and **no `run_id` appears twice** on the
object. Fails on today's code.

**T2 — the second update is rejected, not silently dropped**
Same setup; assert `report.unchanged` is incremented, so the rejected sighting
is accounted for rather than vanishing.

**T3 — Layer-1 behaviour is unchanged**
Two items sharing one identity key both matching one object: still one update,
still one revision. Pins that the fix does not alter the case the original guard
already handled.

**T4 — order independence of the winner**
Present the same two decisions in both orders; assert the **same** sighting
wins. This is the test that would fail under positional first-wins, and it is
why the tie-break is explicit.

**T5 — the winner is the lowest identity key**
Asserts the rule itself, so a future change to it is visible rather than
incidental.

**T6 — a genuinely unrelated object is unaffected**
Two objects updated in one run still get one revision each. Guards against the
grouping collapsing across objects.

**Independence:** none of these reads REV002. All assert on `run_id` and
revision counts, per the M9-3a oracle.

---

## 7. The remaining historical unknown — exact wording for the record

To be used verbatim wherever this is referenced:

> **Confirmed:** the current engine contains a defect capable of producing the
> double-revision class — one run appending two revisions to one stored object.
> Reproduced against `main` @ `dce0840`.
>
> **Confirmed:** the guard in `update_existing` is expressed against the item's
> identity key rather than the matched stored object, and that is the mechanism
> which permits it.
>
> **Unknown:** whether this exact mechanism produced the 35 historical groups of
> 2026-08-01. The reproduced path necessarily rewrites `url_hash`/`source_url`,
> and **zero** of the 35 historical duplicate groups do **[measured]**.
>
> **Not permitted:** claiming the 2026-08-01 incident is fixed, explained or
> resolved because this reproduction is fixed. The historical trigger remains
> unidentified.
>
> **Consequence:** the 2026-08-01 findings must not be grandfathered, and
> `--strict` must not be enabled, on any assumption about their cause.

---

## 8. Approved decisions (2026-08-08)

| # | Decision |
|---|---|
| **O1** | **Ordering:** lowest `identity.key` as the explicit tie-break. Positional first-wins rejected — contractually deterministic, but it makes the winner depend on `published_date`, the disputed field, creating a hidden "earlier claimed date wins" rule. |
| **O2** | **Fix model:** classify → group by matched object → select lowest `identity.key` → suppress the rest under existing duplicate semantics → process objects in deterministic sorted order. Not a positional one-liner. |
| **O3** | **All six regression tests** proceed. T4 (order independence) and T3 (Layer-1 unchanged) called out specifically. Tests assert on `run_id` and revision counts — never on REV002 or revision identity. |
| **O4** | **§7 wording kept verbatim.** Not a blocker for the fix; **is** a blocker for declaring the 2026-08-01 incident explained. |
| **O5** | **Implementation boundary:** do not modify historical evidence, do not grandfather the 2026-08-01 groups, do not enable `--strict`, do not broaden the fix beyond the demonstrated invariant. |
