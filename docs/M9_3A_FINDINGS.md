# M9-3a — Reproduce or refute the double-revision defect

**Status:** Investigation complete. **No production implementation.**
**Engine under test:** merged `main` at `dce0840`
**Date:** 2026-08-08

**Outcome: REPRODUCED** — with one important qualification in §5 that the reader
should not skip.

The objective, as approved, was not to prove the defect still exists but to
determine **whether the current system can still produce the same class of
corruption.** It can.

---

## 1. Method, and why it is independent of REV002

The REV002 detector was not used, read, or executed at any point in this
investigation. The signal throughout is **`run_id`**: two revisions on one
object bearing the same `run_id` mean one run wrote twice.

That is sound because a run holds one view of the source and one view of
storage, so it can observe a given object change **at most once**. A second
revision from the same run cannot describe a real-world change — nothing outside
the process moved between them.

---

## 2. Timeline — the historical damage predates both guards

**[measured, from git]**

| Time (UTC, 2026-08-01) | Event |
|---|---|
| 06:16:14 | `0f67f59` M2 — the pipeline runs end to end |
| **06:30:12 – 06:30:43** | **the four damaging runs** |
| 06:32:36 | `5c4f271` M3 — *"the update path"* |
| 08:29:50 | `8b29f1f` M4 — introduces the `handled` guard in `update_existing` |

The damage was done **before** the update path was committed and **before** the
guard existed. That explains why it happened. It does **not** establish that it
cannot happen now, which is what M9-3a was asked to determine — so chronology
was treated as context, not as evidence.

---

## 3. The guard, and the hole in it

`update_existing` carries a guard whose docstring describes this exact symptom:

```python
handled: set[str] = set()
for decision in ctx.decisions:
    if decision.is_new or not decision.matched:
        continue
    key = decision.item.identity.key      # <-- keyed on the ITEM
    if key in handled:
        ctx.report.unchanged += 1
        continue
    handled.add(key)
    _update_one(ctx, decision)
```

> *"One sighting per identity: the same feature is legitimately listed by two
> sources with different metadata, and letting both update made the object flip
> between their renderings twice per harvest."*

**The guard keys on the item's identity, but dedupe can match one stored object
from two different identities.** `dedupe.classify` has two matching layers:

* **Layer 1 — `KNOWN_IDENTITY`**, matched by `item.identity.key`
* **Layer 2 — `KNOWN_CONTENT`**, matched by content fingerprint

An item matching via Layer 2 has, by construction, a *different* identity key
from the stored object's — that is why it fell through to Layer 2. So two items
can carry two identity keys and resolve to one object, and the guard sees two
distinct keys and admits both.

**[measured]** Demonstrated directly against the current engine:

```
verdict=known-identity   identity.key=sha256:6e4b3842ff5   matched=PP-2026-05-001
verdict=known-content    identity.key=sha256:f075b408aad   matched=PP-2026-05-001

distinct identity.key (what the guard keys on): 2
distinct matched target (the object updated)  : 1
```

---

## 4. Reproduction against the current engine

**[measured]** Two harvests with **distinct** run IDs. Run 2 discovers the same
feature from two sources reporting different dates — one at the stored URL
(Layer 1), one at a new URL with identical title and summary (Layer 2):

```
rev1  run=run-2026-08-02T06-00-00Z  (initial)
rev2  run=run-2026-08-03T06-00-00Z  published_date
rev3  run=run-2026-08-03T06-00-00Z  announcement_url,published_date,source_url,url_hash

ONE RUN, 2 REVISIONS: run-2026-08-03T06-00-00Z
```

**The current engine still writes two revisions to one object in one run.**

### A false positive I caught and discarded

The first reproduction attempt reported the defect and was **wrong**. Both
harvests used the same `FrozenClock`, so both runs shared a `run_id` and the
"two revisions from one run" count included run 1's initial revision. Rerun with
distinct clocks it did not reproduce, and the real reproduction needed a third
construction. Recorded because the first result looked exactly like a finding.

---

## 5. The qualification: this is the defect *class*, not the historical *instance*

**[measured]** Across all 35 historically damaged objects, the duplicated
revisions carry these `changed_fields`:

```
144  date_confidence,date_precision,published_date
112  content_hash,date_confidence,date_precision,published_date
 16  content_hash
  8  content_hash,date_confidence,date_precision,published_date,title

duplicate groups involving a URL change: 0
```

**Not one historical duplicate involves `url_hash` or `source_url`.** The
reproduction in §4 necessarily does — a Layer-2 match from a different URL
rewrites those fields, as `rev3` above shows.

So the mechanism reproduced here is **a** live path to the defect class, and is
**not demonstrably the path that caused the 2026-08-01 damage**. The historical
mechanism remains unidentified. Most likely it is the pre-guard code that ran at
06:30 — but "most likely" is an inference, and inferring a convenient root cause
without checking it is precisely the error recorded as C-1 in
`docs/CORRECTIONS.md`.

Against the approved outcome definitions this is **Reproduced** — the objective
was whether the current system can still produce the same class of corruption,
and it can. The unexplained historical instance is carried as a known unknown
rather than quietly closed.

---

## 6. Smallest safe fix — proposed, **not implemented**

Key the guard on **the object being updated** rather than the item that matched
it:

```python
key = decision.matched          # instead of decision.item.identity.key
```

`decision.matched` is the Feature ID of the stored object, and the loop already
skips decisions where it is absent, so the change is one expression.

**Why this is the smallest safe fix**

* It corrects the guard's *subject*. The invariant intended all along is "one
  update per object per run"; the code expressed "one update per identity per
  run", which is the same thing only when identities map one-to-one onto
  objects.
* No schema change, no stored-file change, no Feature ID change, no CLI change.
* It cannot under-collapse: two decisions matching one object are exactly the
  case to suppress.

**What it changes in behaviour, stated plainly**

Which sighting wins. Today the first decision *per identity* is applied; after
the fix, the first decision *per object* is applied and later ones are counted
`unchanged`. Both are "first wins", but over different orderings.

**One thing to verify before implementing** — decision order comes from
discovery order, so "first wins" is only deterministic if discovery order is.
ADR-0022 requires byte-identical output across runs, so this needs checking
rather than assuming; if discovery order is not stable, the guard should pick
deterministically (highest source authority, or lowest identity key) instead of
positionally.

**Not proposed:** widening the guard to compare values, or deleting the
duplicate revisions already on disk. The first re-introduces judgement the
oracle deliberately avoids; the second rewrites history, which CLAUDE.md
forbids.

---

## 7. What this means for grandfathering and `--strict`

Per the approved sequencing, **Reproduced ⇒ fix the underlying defect before
grandfathering**. Grandfathering now would bless a class of corruption the
engine can still produce, and the next occurrence would be indistinguishable
from the blessed baseline.

Unchanged and still blocked behind this: the grandfather baseline, and
`--strict`.

Carried forward as a known unknown: the specific mechanism of the 2026-08-01
damage (§5). It does not block the fix — the fix closes a real hole regardless —
but it should not be written up as solved.

---

## 8. For approval

1. **Approve the §6 fix** (guard keyed on `decision.matched`), or amend it.
2. **Decide on the ordering question** in §6 — confirm discovery order is
   deterministic, or choose an explicit tie-break.
3. **Confirm the §5 qualification is acceptable** as a carried unknown, rather
   than requiring the historical mechanism be identified before proceeding.

No implementation until approved.
