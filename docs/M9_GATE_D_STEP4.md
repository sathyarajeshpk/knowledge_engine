# M9 Gate D, step 4 — the grandfather baseline, proposed

**Status:** Proposal, for approval. **No baseline created. No implementation.**
**Baseline state:** merged `main` at `1761468` (post PR #22). 431 objects, 696 tests.

---

## 1. What the baseline means

> **These 35 historical findings are known, bounded and characterised, and are
> being accepted as a historical baseline.**

It does **not** mean the mechanism that produced the 2026-08-01 incident has been
explained. That unknown stays open (`docs/CORRECTIONS.md` C-1). The baseline file
itself will carry this sentence, so nobody reading it later infers more than was
claimed.

---

## 2. Proposed structure

`state/rev002-baseline.json`, at the repository root beside `cross-pack.json`,
because it is a fact about the repository's history rather than about one pack.

```json
{
  "_comment": "Historical REV002 findings accepted as baseline. These are known, bounded and characterised. This does NOT mean the 2026-08-01 incident has been explained — see docs/CORRECTIONS.md C-1.",
  "generated_at": "2026-08-08",
  "generated_from_commit": "1761468",
  "incident": {
    "runs": [
      "run-2026-08-01T06-30-12Z",
      "run-2026-08-01T06-30-16Z",
      "run-2026-08-01T06-30-21Z",
      "run-2026-08-01T06-30-43Z"
    ],
    "note": "All 35 findings are attributable to these four runs, identified by the independent run_id audit oracle. The mechanism that caused them remains unidentified."
  },
  "findings": [
    {
      "feature_id": "MSF-2026-05-002",
      "pack": "microsoft-fabric",
      "first_revision": 2,
      "last_revision": 11,
      "changed_fields": ["date_confidence", "date_precision", "published_date"]
    }
  ]
}
```

**The key is the four fields** `feature_id`, `first_revision`, `last_revision`,
`changed_fields`. Everything else — `pack`, `incident`, `generated_*` — is
evidence for a human, never matching criteria.

`changed_fields` is stored as a **sorted array**, matching `canonical_fields`
exactly, so the detector and the baseline cannot disagree about what a
change-set is. Entries are sorted by `(feature_id, first_revision)` so the file
is byte-stable on regeneration (ADR-0022).

---

## 3. A downgrade, not a deletion — and what it costs

A baselined finding is **still reported**, as `INFO`. It does not vanish from
`ke validate` output; it stops failing `--strict`.

Hiding a finding entirely would be the same mistake as the whole-chain detector:
a silent suppression nobody can audit. Downgrading keeps it visible and keeps
the count honest.

### Two implementation consequences, surfaced now rather than discovered later

**[measured]** `Level` currently has only `ERROR` and `WARNING`:

```python
class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"
```

**[measured]** `has_errors` under `--strict` fails on *any* finding:

```python
if strict:
    return bool(findings)
```

So the downgrade requires **two** changes, and without the second the baseline
would achieve nothing:

1. Add `Level.INFO`.
2. `has_errors(strict=True)` must fail on `ERROR` and `WARNING`, **not** on
   `INFO`.

Both are small, and both are load-bearing. Flagging them because "downgrade to
INFO" reads like a one-line change and is not.

---

## 4. The 35 keys

All 35 are in `microsoft-fabric`. `azure` has none. Every finding spans
**revisions 2–11** — a uniformity that is itself evidence of one incident rather
than scattered history.

| Feature ID | Revisions | `changed_fields` |
|---|---|---|
| MSF-2026-05-002 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-006 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-008 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-009 | 2–11 | content_hash, date_confidence, date_precision, published_date, title |
| MSF-2026-05-011 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-012 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-015 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-016 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-021 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-022 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-023 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-026 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-027 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-05-028 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-030 | 2–11 | content_hash |
| MSF-2026-05-032 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-05-033 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-06-002 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-003 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-06-007 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-012 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-06-018 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-020 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-021 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-024 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-06-025 | 2–11 | content_hash |
| MSF-2026-06-028 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-031 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-06-045 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-07-002 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-07-010 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-07-011 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-07-013 | 2–11 | date_confidence, date_precision, published_date |
| MSF-2026-07-019 | 2–11 | content_hash, date_confidence, date_precision, published_date |
| MSF-2026-07-021 | 2–11 | content_hash, date_confidence, date_precision, published_date |

**[measured]** All 35 are attributable to the same four runs
(`06-30-12Z`, `06-30-16Z`, `06-30-21Z`, `06-30-43Z`) — one distinct run set
across every affected object.

---

## 5. Validation rules

Nine rules. Each becomes a mutation-verified test. V1–V5 are the
non-suppression proof.

### V1 — Exact match only

A finding is downgraded **iff all four key components match**. Any difference in
`feature_id`, `first_revision`, `last_revision` or `changed_fields` means no
match and the finding stays a WARNING.

### V2 — Set equality

With the baseline applied, **exactly 35** findings are downgraded and **exactly
0** others. Asserted on both set differences, never on counts — the same
criterion Gate C used.

### V3 — A new run on a baselined object is not suppressed

The core proof. Append a new qualifying run to a baselined object; it produces a
finding with a **different revision range**, matches no entry, and stays a
WARNING.

This is only possible because PR #22 made the detector report *every* run.
Against the previous detector no keying scheme could satisfy V3.

**Why the range key is permanently safe:** revisions are append-only, so
revision numbers only increase and an existing range can never be re-issued. The
baselined ranges all end at 11; every future run starts at 12 or later.

### V4 — Same range, different fields, is not suppressed

Guards against the key degenerating to `(feature_id, range)`.

### V5 — A run on a non-baselined object is not suppressed

Guards against the key degenerating to something object-independent.

### V6 — A stale baseline entry is reported, not ignored

An entry matching **no** current finding is itself reported as a WARNING.

Causes are all worth knowing: the object was deleted (forbidden), its history
was rewritten (forbidden), or the entry was wrong. Silently ignoring stale
entries lets the baseline rot into a set of stale matchers that could
accidentally match something later.

### V7 — The audit oracle is neither consulted nor suppressed

`ke.audit` must not import the baseline, and the baseline must not affect its
output. A post-M9-3 duplicate write remains visible through the oracle even if
the affected object carries a grandfathered REV002 finding.

Asserted the way Gate C asserted independence: at runtime and by AST, not by
grep.

### V8 — The baseline is closed

Nothing in the weekly pipeline writes to it. Adding an entry is a reviewable
pull-request diff. Asserted by a source check, in the same spirit as the
"pipeline never invokes a model" test.

### V9 — Deterministic and byte-stable

Regenerating from unchanged data produces a byte-identical file: sorted entries,
canonical `changed_fields`, no timestamps beyond the recorded `generated_at`
(ADR-0022).

---

## 6. What this step does **not** do

- Does **not** enable `--strict`. That remains behind Gate D conditions 6 and 7,
  including genuine post-M9-3 harvest evidence.
- Does **not** modify any knowledge object, revision history, or
  `domain-packs/` file.
- Does **not** claim the 2026-08-01 incident is explained.
- Does **not** delete or hide findings — they become INFO and stay visible.

---

## 7. For approval

1. **The structure** in §2, including storing `changed_fields` canonically.
2. **The INFO downgrade** in §3, and the two consequent changes to `Level` and
   `has_errors`.
3. **The 35 keys** in §4.
4. **The nine validation rules** in §5, particularly V3 (non-suppression), V6
   (stale entries reported) and V7 (oracle untouched).

No implementation until approved.
