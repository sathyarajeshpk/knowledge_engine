# M9 Gate B — REV002 detection (work item M9-3)

**Status:** Proposed, for approval. **No implementation code written.**
**Baseline:** merged `main` at `dce0840` (post M9-2). 2 packs, 431 objects, 679 tests.

Every claim is labelled **[measured]**, **[derived]** or **[estimate]**.

---

## 0. The headline, because it changes the milestone

The ground-truth work did not confirm the story I had been telling. It
contradicted it.

**I have been describing these warnings as "residue from the M3 flip-flop bug,
fixed in M3, retained because history is never rewritten."** That sentence
appears in the M8 release notes, in PR #17, in the M8 PR review summary and in
the approved M9 plan. **It is wrong.**

**[measured]** Every one of the 315 oscillating revisions is dated
**2026-08-01** and was produced by seven harvest runs inside about 90 seconds.
None of them is M3-era. The M3 bug was real and was fixed; these revisions are
not it.

**[measured]** Four of those seven runs each appended **two revisions to the
same object**, across 35 objects:

```
run-2026-08-01T06-30-12Z   35 objects
run-2026-08-01T06-30-16Z   35 objects
run-2026-08-01T06-30-21Z   35 objects
run-2026-08-01T06-30-43Z   35 objects
```

One run appending two revisions to one object cannot be correct under any
reading of the revision contract.

So the question Gate B has to answer changed. It was *"how do we grandfather
historical residue so `--strict` can go on?"*. It is now *"what actually
happened on 2026-08-01, and is it still live?"* — because grandfathering a live
defect is how a warning becomes permanent noise.

---

## 1. Current measured facts

| Fact | Value |
|---|---|
| Objects in the repository | 431 **[measured]** |
| Objects with more than one revision | 180 **[measured]** |
| Revisions carrying `content_hash`, `title_snapshot`, `run_id` | 704 of 704 **[measured]** |
| REV002 warnings reported today | **6** **[measured]** |
| REV002 warnings at v0.8.0, before one weekly harvest | **35** **[measured]** |
| Objects where one run appended 2+ revisions | **35** **[measured]** |
| Distinct runs in revision history | 9, of which 4 offending **[measured]** |
| Date range of all oscillating revisions | 2026-08 only **[measured]** |

The 35 → 6 collapse happened with **nothing fixed**. REV002 requires the
*entire* post-initial chain to be uniform (`len(changes) >= 3 and
len(set(changes)) == 1`), so the single genuine `(content_hash, title)` edit
that the 2026-08-02 harvest appended to 29 objects masked them permanently.

`MSF-2026-05-002` is the worked example: eleven revisions on 2026-08-01 all
recording `(date_confidence, date_precision, published_date)`, then one real
edit on 2026-08-02 — and it now reports clean.

---

## 2. Independent definition of a genuine REV002 defect

REV002 exists to assert **"a value flip-flopped between runs rather than genuine
edits"**. To establish the expected result without using the sliding-window
query as its own oracle, I tested four candidate signals against the data. Three
are described here because two of them **failed**, and the failures are the
reason to trust the one that did not.

### Candidate A — `content_hash` revisits an earlier value ❌ rejected

My first attempt, and I reported it to you as confirming 35 "by strict A-B-A
alternation". **That description was wrong.** Inspecting an actual chain shows
revisions 1–11 all carry the *same* hash — `A,A,A,…,A`, not `A,B,A,B`. The test
`h[i] == h[i+2]` fires on constancy just as it does on alternation, and I
mislabelled which one I was seeing.

It also cannot work in principle: `content_hash` covers title and summary, and
the flip-flopping fields are dates, which are **not in the hash**. A constant
hash is the *expected* result of a date-only change, defect or not.

### Candidate B — a revision recording a change with no content movement ❌ too narrow

**[measured]** 18 objects — a strict subset of the 35, missing 17. Same root
cause as A: date-only changes legitimately leave the hash still.

### Candidate C — one run appended two or more revisions to the same object ✅ adopted

**[measured]** Exactly 35 objects, and **set-identical** to the sliding-window
query — not merely the same count. Verified by set comparison, both differences
empty.

This is the right oracle because:

* It reads **only `run_id`**. It never looks at `changed_fields`, so it cannot
  be circular with the proposed detection.
* It requires **no judgement about values**. One harvest run producing two
  revisions of one object is a contradiction of the revision contract on its
  face, whatever changed.
* It is **falsifiable and was nearly falsified** — candidates A and B both
  disagreed with 35, which is what makes C's agreement informative rather than
  assumed.

### The one caveat, stated rather than buried

**[estimate]** Candidate C identifies the objects damaged by the 2026-08-01
runs. It is *not* a general definition of "flip-flop" — a future genuine
oscillation spread across separate runs, one revision each, would not be caught
by C. C is the right instrument for establishing **this** repository's expected
count; it is not the detection rule to ship.

---

## 3. Expected result, derived independently

**35 objects [derived].**

Reached from `run_id` alone, with no reference to `changed_fields` or to the
sliding-window query, and confirmed set-identical rather than count-identical.
Current detection's 6 is a strict subset; **29 objects are missed**.

I want to be explicit that this is *not* "35 because the query said 35". Two of
the four candidate oracles gave different answers (18, 18) and were rejected on
their merits before C was adopted.

---

## 4. The current detection gap

```python
if len(changes) >= 3 and len(set(changes)) == 1:
```

Two distinct defects:

1. **Whole-chain uniformity.** One genuine edit anywhere after a defective run
   masks the whole chain, permanently. **[measured]** 29 of 35 are hidden this
   way today.
2. **Names, not values.** It compares `changed_fields` tuples. It cannot
   distinguish "the same field changed three times, to three different values"
   (possibly fine) from "the same field changed three times, to the same value"
   (never fine).

Defect 1 is why the count is unstable. Defect 2 is why it would remain a proxy
even after fixing 1.

---

## 5. Hypothesis

> **H-B:** Changing REV002 to detect a **run of ≥3 consecutive revisions
> recording identical `changed_fields`** anywhere in the chain reports exactly
> the 35 objects independently derived in §3, and the count does not fall when
> later genuine revisions are appended.

* First clause: **testable now.**
* Second clause: **[estimate].** No future harvest has tested it. It follows
  from the rule being append-insensitive — appending to the end cannot shorten
  an existing run — but that is reasoning, not evidence, and it stays labelled
  as such until a real harvest lands on top.

---

## 6. Falsification threshold — fixed now, before implementation

| Result | Verdict |
|---|---|
| Detection reports **exactly the 35 objects** of §3 (set equality, not count), **and** all existing REV002 tests pass, **and** appending a synthetic later revision to a flagged object does not clear it | **PROCEED** |
| Reports a **superset** — flags any object outside the 35 | **REVISE.** It is flagging healthy history; tighten before baselining |
| Reports a **subset** — misses any of the 35 | **REVISE.** Under-detection is the defect being fixed |
| No rule can separate the 35 from healthy chains without hard-coding identities | **ABANDON** → fallback §7 |

Set equality, not cardinality. A different 35 is a wrong 35.

---

## 7. Fallback if H-B fails

**F-B1: detect the defect by its actual signature — one run, two revisions.**

Ship candidate C as the check (`REV003`), reporting objects whose revision
history contains two revisions sharing a `run_id`. Advantages: it is exactly
what went wrong, it needs no threshold, and it is already measured to identify
precisely the 35. Cost: it detects *this* defect rather than flip-flopping in
general, so REV002 would stay as-is and weak, and a genuine slow oscillation
across separate runs would go uncaught.

F-B1 is a real fallback — measured, implementable, and narrower on purpose.

---

## 8. Decision gate B

| | |
|---|---|
| **What we know** | 6 reported; 35 damaged; sets differ by 29; all damage from four runs on 2026-08-01 **[measured]** |
| **What we believe** | Run-based detection reports exactly those 35 and stays stable **[hypothesis]** |
| **Unknown** | Stability across future harvests **[estimate]** — untested by construction |
| **Evidence to validate** | Set equality with §3's 35; existing tests pass; synthetic later revision does not clear a flag |
| **Decision point** | PROCEED / REVISE / ABANDON per §6 |
| **Fallback** | F-B1, run-collision detection as REV003 |

---

## 9. Impact on grandfathering and `--strict`

**This section is why I am bringing the proposal back rather than proceeding.**

The approved sequence was: fix detection → verify the count → grandfather →
enable `--strict`. The grandfathering step assumed the warnings were *historical
residue of a bug already fixed*. **[measured]** They are not: they are damage
from four runs on 2026-08-01, and I have not established whether the underlying
defect is still live.

**[measured]** The one clean scheduled harvest since — `run-2026-08-02T08-00-14Z`
— appended exactly one revision per object and is **not** in the offending list.
That is encouraging and it is a single sample. **[estimate]** One clean run is
not proof the defect is fixed; those four runs were seconds apart, which is a
condition the weekly cron does not reproduce and which I have not tried to
reproduce deliberately.

So I recommend **inserting a step before grandfathering**:

> **M9-3a — establish whether one run can still append two revisions to one
> object.** Attempt to reproduce it deliberately against the current engine. If
> it reproduces, it is a live correctness defect and takes priority over
> `--strict` entirely. If it does not, record what changed and *then*
> grandfather with confidence.

Grandfathering a defect that can still occur would convert REV002 from a warning
into permanent noise, and the next real occurrence would be invisible — the
same failure mode as the current whole-chain check, arrived at deliberately.

The grandfathering mechanism itself is unchanged and still sound: a
content-derived baseline keyed on `(feature_id, revision_range, changed_fields)`
in a separate state file, never an ID list **[measured to rot: 83% stale in one
week]**, and never by editing `metadata.yaml` (CLAUDE.md forbids rewriting
history).

---

## 10. What I need from you

1. **Approve H-B and the §6 thresholds**, or amend them.
2. **Approve inserting M9-3a** — reproduce-or-refute the double-revision defect
   — before grandfathering. This is a scope addition and it is your call.
3. **Note the correction.** The "M3 residue" claim in the M8 release notes, PR
   #17 and the M9 plan is wrong on the evidence. I propose correcting those
   documents as part of M9-3 rather than leaving the record wrong.

No implementation code will be written until you approve.
