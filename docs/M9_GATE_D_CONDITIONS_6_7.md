# M9 Gate D — Conditions 6 and 7

**Status:** Investigation complete. **`--strict` NOT enabled and NOT recommended yet.**
**Engine:** merged `main` at `4457492` (post PR #23).

**Outcome: Condition 6 is not yet satisfiable. Condition 7 is NOT satisfied and
has a blocker.**

---

## Condition 6 — genuine post-M9-3 harvest evidence

> The audit oracle must report nothing for a run postdating the M9-3
> duplicate-write fix.

### Status: **not yet obtainable**

**[measured, from git]**

| Event | Time (UTC) |
|---|---|
| Last harvest of any kind | `1566c59` **2026-08-02 08:00:30** |
| M9-3 duplicate-write fix merged | `4a21cb1` **2026-08-08 05:18** |
| Harvests since the fix | **none** |

Every harvest in the repository's entire history predates the fix. There is no
post-fix run to examine, so the condition cannot be evaluated today — not
"passed with weak evidence", **not evaluable at all**.

### What would satisfy it

The weekly workflow runs `cron: "0 6 * * 0"` — 06:00 UTC on Sundays. Today is
Saturday 2026-08-08, so the next scheduled harvest is **Sunday 2026-08-09**.

After it lands, the check is:

```
ke.audit.duplicate_write_objects(packs)  →  no entry whose run_id is the new run
```

The existing 35 objects will still appear in that mapping — they carry the
historical 2026-08-01 runs forever. What matters is that **no new `run_id`
appears**.

### What would not satisfy it

**Manufacturing a harvest.** I could run `ke harvest` against the live packs
right now, and it would almost certainly come back clean. It would also be
evidence of very little: the four damaging runs were seconds apart during
development, and a single deliberate run under my own control does not
reproduce the conditions under suspicion. It would produce a green tick without
producing knowledge.

Per M9-3a's standing ruling, and repeated in the Gate D approval: **one clean
weekly harvest is the first required real-world evidence, not proof.** A
manufactured one is not even that.

**If the post-fix harvest produces an audit finding**, that is a new
duplicate-write defect. Stop and investigate. Do not grandfather it.

---

## Condition 7 — can any other warning fire?

> `--strict` fails on **any** WARNING, so every warning-producing path must be
> triaged before it can be enabled.

**[measured]** Five warning-producing code paths exist, found by walking the AST
of every engine module rather than by grep:

| Code | Location | Fires today | Can it fire? | Verdict |
|---|---|---:|---|---|
| `REV002` | `validate.py:676` | 0 (35 baselined → INFO) | Yes — a new flip-flop | ✅ **Correct to fail** |
| `REV003` | `validate.py:171` | 0 | Only if an object is deleted or history rewritten — both forbidden | ✅ Acceptable |
| `SCHEMA004` | `validate.py:484` | 0 | Yes — an unknown metadata field | ✅ Correct to fail |
| `SCHEMA005` | `validate.py:626` | 0 | Yes — a category not declared in `pack.yml` | ✅ **Desirable** to fail |
| `XPK001` | `validate.py:253` | 0 | **Yes — routinely** | ❌ **BLOCKER** |

Current state **[measured]**: 0 unknown metadata fields; 0 undeclared categories
(azure declares 10, fabric 9); 0 cross-pack duplicates; 35 baseline entries, all
matching.

### The four that are fine

`REV002`, `SCHEMA004` and `SCHEMA005` all represent genuine problems that *should*
break a build. `SCHEMA005` in particular is a plausible authoring mistake — adding
a classification rule with a new category value and forgetting to declare it —
and catching it in CI is exactly what `--strict` is for.

`REV003` requires an action CLAUDE.md forbids.

### `XPK001` — the blocker, and it is twofold

#### (a) It contradicts an accepted ADR

ADR-0044 says, in as many words:

> **`ke validate` warns, never errors** (XPK001). Failing CI over a cross-pack
> duplicate would make a judgement the engine is not entitled to make.

Under `--strict`, XPK001 **fails CI**. That is precisely the judgement ADR-0044
says the engine is not entitled to make, arrived at through a flag rather than a
decision.

And XPK001 is **expected to fire**. The M8 architecture review records that
Fabric and Azure overlap occasionally and legitimately — an Azure announcement
that also matters to Fabric is two genuine pieces of knowledge, not a mistake.
It is 0 today only because the two packs have not yet overlapped.

#### (b) An acknowledged duplicate is still reported — measured

This one is a defect independent of `--strict`.

`ke review` uses `outstanding()`, which filters out acknowledged pairs.
`ke validate` uses `find_duplicates()`, which does not. So acknowledging a
duplicate clears the review queue and leaves the validation warning in place:

```
duplicates found                : 1
XPK001 before acknowledging     : 1

after the equivalent of `ke review resolve`:
  outstanding()  [ke review]    : 0
  XPK001         [ke validate]  : 1     ← still reported
```

ADR-0044 states the intent plainly — *"a resolution is recorded so the same
duplicate is not repeatedly surfaced"* — and validation does not honour it.

**Together these are worse than either alone.** Under `--strict`, a legitimate
cross-pack duplicate would fail CI **with no way to clear it**: the acknowledgement
mechanism designed for exactly this case has no effect on the check that fails.
The only remedies would be editing the packs or disabling `--strict`.

---

## Conclusions

| Condition | Status |
|---|---|
| **6** — post-M9-3 harvest evidence | **Not yet obtainable.** No harvest since the fix. Next scheduled: Sunday 2026-08-09 06:00 UTC. |
| **7** — no other warning can fire | **NOT SATISFIED.** `XPK001` blocks it. |

**`--strict` must not be enabled.** Two independent reasons, either sufficient.

### Recommended resolution for XPK001 — proposed, not implemented

**Make `ke validate` honour the resolution store**: XPK001 reports
`outstanding()` rather than `find_duplicates()`.

This is desirable on its own merits, regardless of `--strict`:

* It makes ADR-0044's acknowledgement mechanism actually work end to end.
* It removes the inconsistency where two commands disagree about the same fact.
* It gives a cross-pack duplicate a defined path to resolution — acknowledge it,
  and both the queue and validation go quiet.

With that change, `--strict` and ADR-0044 stop contradicting each other: an
*unreviewed* duplicate blocks, a *reviewed* one does not, and the engine still
never decides which object wins.

It needs your approval as an ADR-0044-adjacent behavioural change, and it is not
implemented here.

### Alternative, if that is rejected

Enable `--strict` with XPK001 explicitly excluded, and record why. Weaker — it
leaves the acknowledgement inconsistency in place — but it does not contradict
ADR-0044.

---

## What was not done

- `--strict` not enabled, not modified. Still a comment at `ci.yml:49`.
- No fix implemented for the XPK001 findings. Investigation only.
- No harvest manufactured.
- The 2026-08-01 mechanism remains unexplained.
