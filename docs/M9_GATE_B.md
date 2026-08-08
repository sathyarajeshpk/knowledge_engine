# M9 Gate B — REV002 detection (work item M9-3)

**Status:** **APPROVED 2026-08-08** (H-B, M9-3a, and the record correction).
Revised to record the approved criteria and sequencing. **No implementation code written.**
**Baseline:** merged `main` at `dce0840` (post M9-2). 2 packs, 431 objects, 679 tests.

Every claim is labelled **[measured]**, **[derived]** or **[estimate]**.

---

## 0. Approved decisions and sequencing

| # | Decision |
|---|---|
| **B1** | **H-B approved**, with the success criterion being **exact set equality**, not cardinality. |
| **B2** | **M9-3a approved** and inserted **before** grandfathering. Its objective is *not* to prove the defect still exists — it is to determine whether the current system can still produce the same class of corruption. |
| **B3** | **Record correction approved.** Filed as `docs/CORRECTIONS.md` entry C-1. No document was silently rewritten. |

### Approved sequence — no step may be skipped

```
M9-3a   reproduce / refute the double-revision defect
   |
M9-3    establish independent ground truth + run-based detection
   |
        verify detection against the independent oracle (set equality)
   |
        establish the grandfather baseline — only once detection is trustworthy
   |
        only then consider enabling `--strict`
```

**No grandfathering and no `--strict` before that evidence exists.** No
production implementation beyond approved investigation and design work until
the relevant gate is approved.

---

## 0b. The headline, because it changes the milestone

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

### Why "one run + two revisions + one object" is sufficient to identify the defect

Preserved explicitly, because the whole validation rests on it.

The revision contract is that **a revision records a change observed by a run**.
A run reads the stored object once, compares it against what discovery produced,
and appends **at most one** revision describing the difference. There is no code
path in which a single run legitimately observes one object change twice: the
run holds one view of the source and one view of storage.

So two revisions bearing the same `run_id` on the same object mean the update
path executed twice within one run against the same object. Whatever
`changed_fields` says, the second revision cannot describe a real-world change —
nothing outside the process changed between them. It is a duplicate write, and
duplicate writes are the mechanism that produced the long uniform chains.

This is why the oracle needs **no judgement about values**, and why it avoids
the two traps that sank candidates A and B:

* It does not care whether `content_hash` moved (dates are not in the hash).
* It does not care what `changed_fields` claims — that is the thing under test.

It reads one field, `run_id`, whose meaning is fixed by the engine's run
identity and not by the detection logic being validated.

### Cardinality agreement is weak evidence. Set equality is the requirement.

| | |
|---|---|
| **Cardinality agreement** ("both give 35") | **Weak.** Two definitions can produce equal counts over disjoint or partly-overlapping sets. It is consistent with both being wrong. |
| **Exact set equality** ("both give the *same* 35") | **The required validation.** Verified: both differences empty, in both directions. |

**If the implementation later produces the same count but a different
object/revision set, that is a FAILURE**, not a pass. §6 encodes this.

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

## 8b. M9-3a — reproduce or refute the double-revision defect

**Approved (B2).** Runs *before* M9-3, and before any grandfathering.

**Objective, as approved:** not to prove the defect still exists, but to
determine whether the current system **can still produce the same class of
corruption**.

### Three outcomes, each with a defined consequence

| Outcome | Consequence |
|---|---|
| **Reproduced** | A live correctness defect. **Fix the underlying cause before grandfathering** — and before `--strict`, which ranks below a bug that corrupts revision history. |
| **Refuted** | Document **why the current engine can no longer produce it**, with evidence: the specific code path that prevents it, and what changed since 2026-08-01. Then proceed to M9-3. |
| **Inconclusive** | **Do not grandfather.** Define explicitly what additional evidence would be required, and treat that as a blocking work item rather than a caveat. |

### What is explicitly not sufficient evidence

**[measured]** The one clean scheduled harvest since — `run-2026-08-02T08-00-14Z`
— appended exactly one revision per object and is not in the offending list.

**A single clean weekly harvest does not refute the defect.** The four bad runs
were seconds apart; the weekly cron never reproduces that condition, so a clean
weekly run does not exercise the circumstance under suspicion. Refutation
requires identifying the mechanism, not observing its absence once.

### Candidate mechanisms to investigate

Listed now so the investigation is not shaped by whichever is found first:

* Repeated `ke harvest` invocations racing, under lock behaviour as it stood
  before M6's lock or before M8's isolation fix.
* The update path executing twice within one `run_stages` pass.
* A retry or fallback in the acquisition chain re-entering the update stage.
* Two packs' harvests interleaving over a shared object — implausible given pack
  isolation, but on the list rather than assumed away.

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

**M9-3a is approved and inserted before grandfathering** — see §8b for its
objective and its three defined outcomes.

Grandfathering a defect that can still occur would convert REV002 from a warning
into permanent noise, and the next real occurrence would be invisible — the
same failure mode as the current whole-chain check, arrived at deliberately.

The grandfathering mechanism itself is unchanged and still sound: a
content-derived baseline keyed on `(feature_id, revision_range, changed_fields)`
in a separate state file, never an ID list **[measured to rot: 83% stale in one
week]**, and never by editing `metadata.yaml` (CLAUDE.md forbids rewriting
history).

---

## 10. Status

All three decisions approved 2026-08-08 (§0). The record correction is filed as
`docs/CORRECTIONS.md` entry C-1, with inline notes added to every document that
carried the incorrect claim; no original wording was removed.

Next step is **M9-3a**, an investigation with no production implementation.
