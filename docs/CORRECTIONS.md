# Corrections

Claims this project made, published, and later found to be wrong.

Entries are **append-only**. A correction never edits the original claim out of
existence — the documents that carried it get an inline note pointing here, and
the original wording stays legible. Rewriting a wrong conclusion into a right one
destroys the evidence that the reasoning failed, and the reasoning is the part
worth learning from.

This is the same rule the engine applies to knowledge (CLAUDE.md: never rewrite
history; corrections are revisions), applied to the project's own documentation.

---

## C-1 — "REV002 warnings are residue from the M3 flip-flop bug"

**Raised:** 2026-08-08, during M9 Gate B ground-truth work
**Severity:** High — the incorrect explanation shaped the M9 grandfathering plan

### What was previously believed

That the REV002 warnings on Fabric objects were historical residue of a
flip-flop defect introduced and fixed in **M3**, retained only because history
is never rewritten. The conclusion drawn from it was that the warnings were
inert, and therefore safe to grandfather so `--strict` could be enabled.

Stated in:

| Document | Where |
|---|---|
| `docs/releases/v0.9.0.md` | Known limitations |
| `docs/reviews/M8_PR_REVIEW_SUMMARY.md` | Technical debt |
| `docs/M9_PLAN.md` | §4, decision gate B |
| PR #17 description | Verification section |
| PR #19 / M9 discussion | Repeated verbally in status reports |

### What the new evidence shows

**[measured, against merged `main` at `dce0840`]**

* All 315 oscillating revisions are dated **2026-08-01**. None is from the M3
  era.
* They were produced by **seven harvest runs within roughly 90 seconds**.
* **Four of those runs each appended two revisions to the same object**, across
  35 objects:

  ```
  run-2026-08-01T06-30-12Z   35 objects
  run-2026-08-01T06-30-16Z   35 objects
  run-2026-08-01T06-30-21Z   35 objects
  run-2026-08-01T06-30-43Z   35 objects
  ```

One run appending two revisions to one object contradicts the revision contract
outright, whatever the fields say changed.

### Why the previous conclusion was incorrect

Three failures compounded:

1. **A plausible story was never checked.** M3 *did* have a flip-flop bug, and
   it *was* fixed. The warnings *looked* like its signature. The inference was
   never tested against the revision dates, which were available the whole time
   and take one query to read.
2. **`changed_fields` was read; `date` and `run_id` were not.** The analysis
   looked at *what* the revisions claimed changed and never at *when* they
   happened or *which run* produced them — the two fields that immediately
   falsify the M3 attribution.
3. **The conclusion was convenient.** "Inert historical residue" made
   grandfathering obviously safe. A live defect does not. The explanation that
   required less work went unexamined, and was then repeated across five
   documents until it read as established.

### Consequence

The M9 plan's grandfathering step rested on the warnings being inert. They may
not be. **M9-3a** was inserted before grandfathering to reproduce or refute the
double-revision defect against the current engine, with an explicit
*inconclusive* outcome that also blocks grandfathering.

### Related correction, same investigation

While establishing the independent oracle for Gate B, a second claim was made
and retracted within the same session: that `content_hash` "revisits an earlier
value" confirmed the expected set "by strict A-B-A alternation".

Inspecting a real chain showed eleven revisions carrying the **same** hash —
constancy, not alternation. The test `h[i] == h[i+2]` fires on both, and the two
were conflated. The signal also cannot work in principle: `content_hash` covers
title and summary, while the flip-flopping fields are dates, which are not in
the hash.

That candidate was rejected and replaced with the `run_id` oracle (see
`docs/M9_GATE_B.md` §2). Recorded here because it was stated to the project
owner as evidence before it was checked, which is the same failure as C-1 at
smaller scale.

### Documents corrected

Each carries an inline note pointing here; none had its original wording
removed.

- [x] `docs/releases/v0.9.0.md` — inline note added
- [x] `docs/reviews/M8_PR_REVIEW_SUMMARY.md` — inline note added
- [x] `docs/M9_GATE_B.md` — written after the correction; states it directly
- [ ] `docs/M9_PLAN.md` — **not on `main`.** It lives on the unmerged branch
      `claude/knowledge-engine-planning-0fu2z6`, so it cannot be annotated here.
      Its §4 rests on the incorrect explanation and must be corrected if that
      branch is ever merged. Recorded rather than quietly skipped.
- [x] PR #17 — annotated by **comment** on the merged PR, not by editing the
      description. Merged history is not rewritten; the original wording stays
      legible and the comment points here.

### Follow-up from M9-3a (2026-08-08)

`docs/M9_3A_FINDINGS.md` records the investigation this correction triggered.
Outcome: **Reproduced** — the current engine can still produce the defect class.

One part of C-1 remains **open**: the specific mechanism behind the 2026-08-01
damage is still unidentified. The reproduced path necessarily rewrites
`url_hash`/`source_url`, and **zero** of the 35 historical duplicate groups do
**[measured]**. The likely explanation is the pre-guard code that ran at 06:30,
but that is an inference and is deliberately not being recorded as fact — which
is the same trap this entry exists to document.
