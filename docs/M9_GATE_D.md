# M9 Gate D — the historical REV002 grandfather baseline

**Status:** Proposal, for approval. **No implementation code written.**
**Baseline:** merged `main` at `dfb7888` (post Gate C). 431 objects, 692 tests.

Every claim is labelled **[measured]**, **[derived]** or **[estimate]**.

---

## 0. The finding that shapes this gate

**A safe baseline is not possible against the detector as shipped.** Found by
analysis, not assumed.

`_longest_identical_run` reports **only the longest run** in a chain. Simulated
against a real object **[measured]**:

```
MSF-2026-05-002
  runs today                : [(date_confidence.., 10, rev2)]
  after appending a NEW run : [(date_confidence.., 10, rev2), (summary.., 3, rev14)]

  detector reports (today)  : 10 long, starting rev 2
  detector reports (after)  : 10 long, starting rev 2
```

A new three-revision flip-flop appended to an already-flagged object **does not
change what the detector reports**. Baseline that finding and the object goes
permanently quiet for any future run shorter than its historical one — which for
all 35 objects means shorter than 10.

That is precisely the failure this gate is supposed to prevent, so it is a
**prerequisite**, not a footnote.

### The prerequisite, and why it is free

**The detector must report every qualifying run, not just the longest.**

Measured consequence on today's data **[measured]**:

| | Findings | Objects |
|---|---:|---:|
| Longest-run only (shipped) | 35 | 35 |
| All qualifying runs (proposed) | **35** | **35** |

**Identical.** No object in the repository has more than one qualifying run
**[measured: 0 of 35]**. So the change is **provably set-neutral today** and
**necessary for safety tomorrow** — it cannot disturb Gate C's result, and
without it no keying scheme can be made safe.

---

## 1. Exactly which findings are being grandfathered

**35 findings, one per object, across 35 objects — all in `microsoft-fabric`.**

Characterised **[measured]**:

| Property | Value |
|---|---|
| Findings | 35 |
| Distinct objects | 35 (one finding each) |
| Run length | **10 revisions, every one of them** — no variation |
| Revision range | **revisions 2–11**, every one |
| Packs affected | `microsoft-fabric` only; `azure` has none |
| Attributable to the same four runs | **Yes — all 35** |

`changed_fields` distribution:

| Count | Fields |
|---:|---|
| 18 | `date_confidence, date_precision, published_date` |
| 14 | `content_hash, date_confidence, date_precision, published_date` |
| 2 | `content_hash` |
| 1 | `content_hash, date_confidence, date_precision, published_date, title` |

Every affected object shares **the same offending run set** — the four
2026-08-01 runs identified by the audit oracle **[measured: 1 distinct run set
across all 35 objects]**.

That uniformity is itself evidence: 35 objects, all with exactly 10 revisions
over exactly revisions 2–11, all from the same four runs, is one incident and
not a scatter of unrelated history.

---

## 2. How the baseline is keyed

**Keyed on the finding, never on the object and never on a count.**

```
(feature_id, first_revision, last_revision, changed_fields)
```

for example

```
MSF-2026-05-002 | 2 | 11 | date_confidence,date_precision,published_date
```

Stored at repository root — `state/rev002-baseline.json` — alongside
`state/cross-pack.json`, because it is a fact about the repository's history
rather than about one pack.

### Why each component is load-bearing

| Component | Without it |
|---|---|
| `feature_id` | The baseline would be a count. "35 warnings" accepts a *different* 35 silently — the failure mode named in the Gate C approval. |
| `first`/`last` revision | A **new** run on the same object with the same fields would match and be suppressed. This is the §0 hole; the range is what closes it. |
| `changed_fields` | A new run on the same object over the same revisions with *different* fields would be suppressed. |

A baselined finding is downgraded to **INFO**; anything not matching all four
components remains a **WARNING** and fails `--strict`.

### What the baseline records alongside the key

Not part of the key, but stored so the record still means something in two
years: the four `run_id`s, the date the baseline was taken, the commit it was
taken at, and a pointer to `docs/CORRECTIONS.md` C-1. Evidence, not matching
criteria.

---

## 3. How we prove it cannot hide a future duplicate-write defect

Three independent arguments. The first is the strongest.

### 3.1 The audit oracle is never baselined — by construction

`ke.audit.duplicate_write_objects` detects the duplicate-write **mechanism** via
`run_id` collision. It is not part of REV002, emits no `Finding`, and **is not
imported during a validate run at all** (verified at runtime in Gate C).

**The baseline cannot suppress it, because the baseline is a REV002 concept and
the oracle is not REV002.** A future duplicate write is visible through the
oracle whatever REV002 does.

This is the structural answer, and it is why the Gate C approval insisted the
two mechanisms stay separate. That separation is what makes a REV002 baseline
safe to have at all.

### 3.2 The range key means a new run is a new finding

With the §0 prerequisite in place, every qualifying run is reported separately.
A new run produces a finding with a **different revision range**, which matches
no baseline entry and therefore stays a WARNING.

The historical entries are pinned to revisions 2–11 — a range that is now closed,
because revision numbers only increase.

### 3.3 The baseline is closed and cannot grow silently

The baseline is generated **once**, from the state at a named commit, and
committed as data. Nothing in the weekly pipeline writes to it. Adding an entry
is a reviewable pull-request diff, not an automatic consequence of a warning
appearing.

### What this deliberately does **not** claim

It does not claim the 2026-08-01 incident is explained. Per the wording carried
since M9-3a and preserved verbatim:

> **Unknown:** whether the mechanism fixed in M9-3 produced the 35 historical
> groups of 2026-08-01. The reproduced path necessarily rewrites
> `url_hash`/`source_url`, and **zero** of the 35 historical duplicate groups do.
>
> **Not permitted:** claiming the 2026-08-01 incident is fixed, explained or
> resolved because that reproduction is fixed.

Baselining these findings is **accepting a known, bounded, characterised piece
of history** — not declaring it understood. The baseline file should say so in
as many words.

---

## 4. What must be true before `--strict` can be enabled

Seven conditions. All are checkable; none is a judgement call.

| # | Condition | Status today |
|---|---|---|
| **1** | Detector reports **all** qualifying runs, not just the longest | ❌ **prerequisite, §0** |
| **2** | Reported set still exactly equals the audit oracle's 35, by set equality | ✅ verified for longest-run; must be re-verified after §0 |
| **3** | Baseline generated from a named commit, keyed per §2, reviewed as a diff | ❌ not yet built |
| **4** | With the baseline applied, `ke validate --strict` is **green** | ❌ |
| **5** | A **synthetic new** flip-flop on a baselined object still fails `--strict` | ❌ — the test that proves §3.2 |
| **6** | The audit oracle reports **nothing** for any run postdating M9-3 | ⚠️ **unverified** |
| **7** | No non-REV002 warnings remain, or each is separately triaged | ⚠️ **unverified** |

### On condition 6

The audit oracle currently reports 35 objects — all from the four 2026-08-01
runs, all predating the M9-3 fix. **[measured]**

What has *not* been established is that no run **after** the M9-3 fix produces a
duplicate write. The fix is tested, but the only production evidence available is
harvests that predate it. **[estimate]** — and M9-3a's ruling stands: a single
clean weekly harvest is not sufficient evidence.

**Recommendation:** condition 6 requires at least one real scheduled harvest to
run against the fixed engine and produce no duplicate writes, checked
explicitly. That is a *wait*, not a task, and it should gate `--strict` rather
than be waved through.

### On condition 7

`ke validate` reports 35 warnings today, all REV002 **[measured]**. But
`--strict` fails on *any* warning, so before enabling it we must confirm no
other code can warn on this repository — REV001, XPK001, and any other WARNING
finding. Currently unverified, and cheap to check.

---

## 5. Proposed sequence

```
D-1  Detector reports all qualifying runs      (prerequisite, §0; set-neutral today)
  ↓
D-2  Re-verify set equality against the oracle after D-1
  ↓
D-3  Generate the baseline from a named commit; review it as a diff
  ↓
D-4  Apply the baseline as an INFO downgrade; prove a synthetic new run still WARNs
  ↓
D-5  Verify conditions 6 and 7
  ↓
D-6  Enable `--strict` in CI
```

D-1 through D-4 are implementable now. **D-5 contains a wait** — at least one
real harvest against the fixed engine — and D-6 cannot honestly precede it.

---

## 6. Decision gate D

| | |
|---|---|
| **What we know** | 35 findings, one per object, all revisions 2–11, all length 10, all from the same four 2026-08-01 runs, all in `microsoft-fabric` **[measured]** |
| **What we believe** | Keying on `(feature_id, first_rev, last_rev, changed_fields)` suppresses exactly these 35 and nothing else **[hypothesis]** |
| **Unknown** | Whether any post-M9-3 run can still produce a duplicate write **[estimate]** — condition 6 |
| **Evidence to validate** | Baseline suppresses exactly 35, by set equality; a synthetic new run on a baselined object still WARNs; `--strict` green with the baseline and red without it |
| **PROCEED** | All seven conditions in §4 hold |
| **REVISE** | The baseline suppresses anything outside the 35, or a synthetic new run is suppressed |
| **ABANDON** | No keying separates historical from future findings → leave REV002 at WARNING and enable `--strict` with REV002 explicitly excluded, recording why |

---

## 7. For approval

1. **The §0 prerequisite** — detector reports all qualifying runs. Set-neutral
   today, necessary for a safe baseline.
2. **The §2 key** — `(feature_id, first_rev, last_rev, changed_fields)`, stored
   at repository root, INFO downgrade on exact match only.
3. **The §4 conditions**, particularly **condition 6**, which introduces a wait
   for real post-fix harvest evidence before `--strict`.
4. **The §5 sequence.**

No implementation until approved.
