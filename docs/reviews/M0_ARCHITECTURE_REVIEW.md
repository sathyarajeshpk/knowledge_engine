# M0 Architecture Review

**Reviewer perspective:** Principal Software Architect, first contact with the codebase
**Date:** 2026-07-31
**Scope:** PR #1 — M0 Foundation, Schema and Guardrails (v0.1.0)
**Method:** Read the code cold, then empirically tested the claims it makes about itself

**Verdict: mergeable, but two defects should be fixed inside this PR.** See §7.

Every defect below was reproduced, not inferred. Commands are included so the
findings can be independently verified.

---

## 1. Strengths

### 1.1 The field ownership model is the right solution to a real problem

Most projects would have handled "don't overwrite the user's notes" with a
comment and good intentions. This one made it a partition asserted at import
time, funnelled every write through a single gate, and proved it with tests.

The detail that shows this was thought through: `ownership_of()` raises
`KeyError` for unknown fields rather than returning a permissive default. A typo
in engine code cannot silently acquire write permission. That is a deliberate
choice most engineers would not have made.

### 1.2 Invalid states are made unrepresentable rather than validated against

`ObjectStatus` has no `deleted` member. There is no code path to audit, no rule
to remember, no test to write. The state cannot be expressed.

This pattern recurs — `FeatureId.__post_init__` validates through the same regex
used for parsing, so an ID constructed in code and one read from disk cannot
disagree. Cheaper and stronger than defensive checking.

### 1.3 Code and data separation is real, not aspirational

```bash
grep -ri "microsoft-fabric" engine/     # returns nothing
```

Verified. This is the property that makes M8 achievable and the eventual repo
split mechanical. It is also easy to lose accidentally, which is why §5.6
recommends enforcing it in CI.

### 1.4 Enforcing a rule before the code that could break it exists

The CI guard against `ke generate` in a scheduled workflow was written in M0,
though the weekly workflow arrives in M6. Writing it later would mean writing it
after the temptation to break it appears. This is unusually disciplined.

### 1.5 The test suite tests the right things

42 of 91 tests target the validator, which is correct weighting: a validator that
silently passes bad data is worse than no validator.

Two choices stand out. `conftest.py` builds test inputs with `yaml.safe_dump`
rather than engine code — so a future writer bug cannot produce a matching wrong
file and pass. And `test_a_counter_ahead_of_disk_is_allowed` pins *deliberate
non-behaviour*, stopping a future contributor "fixing" an intentional asymmetry.
Both are things experienced teams often miss.

### 1.6 Decisions are recorded with their rejected alternatives

Fourteen ADRs, each stating what was rejected and why. The rejections are the
valuable part — ADR-0006's explanation of why flat-file-with-promotion breaks
path stability is the kind of reasoning that otherwise evaporates and gets
relitigated.

### 1.7 Honest handling of the unverifiable

`sources: []` with candidates in comments, rather than pinning plausible-looking
URLs. Correct call. A pinned unverified URL is indistinguishable from a
validated one.

---

## 2. Weaknesses

### 2.1 CONFIRMED DEFECT — the object subdirectories will not survive Git

**Severity: high. Surfaces in M2. Cheap now, annoying later.**

Every knowledge object is specified to contain `artifacts/`, `images/` and
`references/`. Git cannot track empty directories.

```bash
mkdir -p /tmp/t/obj/{artifacts,images,references} && cd /tmp/t
git init -q . && touch obj/feature.md && git add -A && git status --short
# A  obj/feature.md          ← the three directories are simply absent
```

Consequence once M2 writes real objects: they are created locally, committed
without the empty directories, and **absent on every fresh clone and every CI
run**. `ke validate` then emits three `OBJ005` warnings *per object*. At a few
hundred objects that is a thousand warnings, which destroys the signal value of
the validator — and it permanently blocks the roadmap's plan to tighten CI to
`--strict`.

The design is sound (ADR-0006); the persistence mechanism was not thought
through. Options: commit a `.gitkeep` in each subdirectory, create subdirectories
lazily on first artifact (which does *not* violate path stability, since the
object directory itself never moves), or downgrade `OBJ005` to informational.
The middle option is probably correct and also removes the mostly-empty-directory
cost ADR-0006 accepted.

### 2.2 CONFIRMED DEFECT — `with_engine_fields()` does not copy what it claims to

**Severity: high. Latent until M5, then subtle and hard to debug.**

`engine/ke/models.py:537` — the docstring promises "Return a copy". It uses
`dataclasses.replace()`, which is **shallow**. The `generation` dict is shared
between original and copy:

```python
o2 = o1.with_engine_fields(title="Renamed")
o1.generation is o2.generation          # True
o2.generation[ArtifactType.QUIZ] = ...  # also mutates o1
```

Verified. Every other field is a scalar, tuple, or frozen dataclass, so
`generation` is the sole exposure — but it is precisely the field M5 will mutate
when marking artifacts stale.

This directly undermines the PR's own claim that "a rejected write cannot
half-succeed": a *permitted* write returns an object entangled with its
predecessor. Fix is one line (`replace(self, generation=dict(self.generation),
**updates)`) plus a test. It should not be discovered in M5.

### 2.3 CONFIRMED DEFECT — findings are ambiguous across packs

**Severity: medium. Surfaces in M8.**

`Finding.location` is documented at `engine/ke/validate.py:51` as
"repository-relative". It is produced by `Pack.relative()`
(`engine/ke/pack.py:147`), which is **pack**-relative. With two packs:

```
ERROR   OBJ002  knowledge/2026/04/BAD-2026-04-001-x: missing metadata.yaml
ERROR   OBJ002  knowledge/2026/04/BAD-2026-04-001-x: missing metadata.yaml
```

Verified. Two different files, indistinguishable output. Worse, `_report()`
groups by `location`, so findings from different packs are merged into one group
— actively misleading rather than merely incomplete.

The docstring states the correct intent; the implementation does not honour it.
Making `location` repo-relative fixes both symptoms.

### 2.4 CONFIRMED DEFECT — one malformed `pack.yml` aborts validation of every pack

**Severity: medium. Surfaces in M8.**

`Pack.discover()` calls `Pack.load()` eagerly and lets `PackError` propagate.
`validate_repo()` does not isolate it, so the CLI exits `2` having validated
nothing:

```
error: /tmp/packtest/domain-packs/beta/pack.yml is not valid YAML: ...
exit=2                     # pack 'alpha' was never checked
```

Verified. With nine packs, a typo in one blocks all feedback on the other eight.
Per-pack failures should be isolated into a `PACK00x` finding — the same "fail
fast within a unit, continue across units" discipline already applied correctly
to knowledge objects.

### 2.5 Requiring all 32 fields conflicts with the hand-editing design

`ke validate` requires every field present, even when null. A human editing
learning state who deletes a `notes: null` line gets an error:

```
ERROR   SCHEMA002  ...: missing required field 'notes'
```

Verified. The architecture deliberately optimises for hand-editing — that is the
justification for YAML over JSON (ADR-0007) and the reason user-owned fields
exist at all. Then it punishes the most natural hand edit there is.

The original reasoning ("the engine always writes the full set, so absence means
corruption") is sound for engine-owned fields and wrong for user-owned ones,
which have safe defaults and are exactly what humans touch. Splitting the
requirement by ownership class would resolve the tension cleanly.

### 2.6 The documentation-to-code ratio is a maintenance liability

| | Lines |
|---|---|
| Documentation | 4,662 |
| Engine code | 1,530 |
| Tests | 974 |

**Roughly 3 lines of prose per line of code.** For a milestone that ships no
pipeline, this is a lot of surface area that can drift out of sync — and §2.3
already demonstrates a docstring that describes behaviour the code does not have.

The documents are individually good and were explicitly requested, so this is not
an argument for deleting them. It is a warning that by M9 there will be ten
playbooks and ten learning guides describing code that has moved, and no
mechanism keeps them honest. Doc drift is silent, exactly like the data-loss risk
ADR-0008 was written to prevent — and it currently has no equivalent guardrail.

### 2.7 `models.py` mixes four concerns at 720 lines

Vocabularies, identity, the ownership registry, and the object model. Each is
coherent; together they are the file everyone edits, which will make it a merge
hotspot.

A related naming problem is already scheduled: M2 plans `ids.py` for minting,
while `FeatureId` lives in `models.py`. Two files owning "IDs" with no obvious
boundary. Splitting `models.py` into `vocab.py`, `identity.py`, `ownership.py`
and `objects.py` would resolve both — but this is churn against a stable file
and belongs *after* M1, not before.

### 2.8 The AI-in-pipeline guard is shallower than its importance warrants

It greps for `ke generate` in workflows containing `schedule:`. It does not catch:

- a scheduled workflow calling a shell script that calls `ke generate`
- a workflow using the quoted `"on":` form
- a reusable workflow invoked with `workflow_call` from a scheduled one

This is the mechanism protecting the project's central guarantee (ADR-0004). Its
limits should at minimum be documented in the workflow, and it would be stronger
as a runtime assertion — the generate command refusing to run when
`GITHUB_EVENT_NAME=schedule` — which no amount of workflow indirection can evade.

---

## 3. Risks

| # | Risk | Likelihood | Impact | Notes |
|---|---|---|---|---|
| R1 | Warning fatigue from §2.1 makes `--strict` unreachable | **High** | High | Certain in M2 unless fixed |
| R2 | §2.2 aliasing corrupts generation state in M5 | Medium | High | Silent; would present as "staleness flags appear on the wrong object" |
| R3 | Fabric/Power BI ID boundary decided late | **High** | **Very high** | Already flagged. IDs are permanent — a wrong call is unfixable |
| R4 | Documentation drifts from code | **High** | Medium | No mechanism prevents it; §2.6 |
| R5 | `engine/` acquires pack-specific knowledge | Medium | High | Nothing enforces it; §5.6 |
| R6 | Source feeds unverified | **Certain** | Medium | Known and scheduled as M1's first task |
| R7 | Git performance with directory-per-object | Low | Low | ~4 entries/object; `git status` slows near ~10k objects, well beyond realistic scale |
| R8 | Unpinned GitHub Actions | Low | Medium | `@v4` is a moving tag; supply-chain exposure |

**R3 remains the highest-severity item in the project** and is not addressed by
anything in M0. It is a decision, not code, and it must be made before M2 mints
its first ID.

---

## 4. Technical debt

Debt deliberately taken, with the interest rate attached:

| Item | Interest rate | When it comes due |
|---|---|---|
| Empty subdirectories not persisted (§2.1) | **High** — compounds per object | M2 |
| Shallow copy in `with_engine_fields` (§2.2) | **High** — silent when it fails | M5 |
| Pack-ambiguous findings (§2.3) | Medium | M8 |
| No per-pack failure isolation (§2.4) | Medium | M8 |
| Version declared twice | Low | Never, realistically |
| No linter/formatter | Low, but rises with contributors | M1+ |
| `RunReport`/`SourceHealth` written but unused and untested | Low | M1 |
| No atomic-write helper for the two-file object | **Medium — not yet incurred** | M2 |

That last row is the one nobody has written down yet. ADR-0007 split
`feature.md` from `metadata.yaml`, which means M2 must write two files
atomically: a crash between them leaves an object whose title and metadata
disagree — precisely the drift `CONS002` detects but cannot repair. There is
currently no plan for this. It should be designed before `store.py` is written,
not after the first corrupted object.

---

## 5. Recommended improvements before M1

Ordered by value per unit of effort.

### 5.1 Fix the shallow copy (§2.2) — 15 minutes

One line plus one test. It makes a documented guarantee true. Do this inside
this PR.

### 5.2 Decide the empty-directory strategy (§2.1) — 30 minutes

A decision plus an ADR, not necessarily code. Recommendation: create
subdirectories lazily on first artifact and change `OBJ005` accordingly. This
removes the cost ADR-0006 accepted and makes `--strict` reachable. Do this inside
this PR — M2 depends on it.

### 5.3 Decide the Fabric/Power BI ID boundary (R3) — a conversation

No code. Highest-severity open item in the project and it must precede M1's
source configuration.

### 5.4 Make findings repo-relative and isolate per-pack failures (§2.3, §2.4) — 1 hour

Both are single-pack blind spots that only appear with two packs — which is
exactly why they went unnoticed. **Add a two-pack test fixture**; it is the
change most likely to catch the next bug of this class.

### 5.5 Close the two testing gaps that hid these defects — 1 hour

- Multi-pack validation (would have caught §2.3 and §2.4)
- `with_engine_fields` non-aliasing (would have caught §2.2)
- `RunReport`/`SourceHealth`: currently **zero** test references across all four
  test files. Verified.

### 5.6 Add a CI check that `engine/` names no pack — 10 minutes

The `grep` is already documented in three places and run by hand. Automating it
converts a convention into a mechanism, which is this project's own stated
standard.

### 5.7 Relax field requirements for user-owned fields (§2.5) — 30 minutes

Resolves a genuine conflict between the validator and the hand-editing design.

### 5.8 Pin GitHub Actions to SHAs, add a PR template — 20 minutes

`CONTRIBUTING.md` already specifies what a PR should contain; a template makes it
automatic. Both are cheap developer-experience wins.

---

## 6. Improvements that should intentionally wait until after v1

Recorded so they are visibly deferred rather than forgotten.

| Deferred | Until | Why waiting is correct |
|---|---|---|
| **Splitting `models.py`** (§2.7) | After M5 | Churn against a file every milestone touches. Split when the seams are proven, not predicted. |
| **Extracting `engine/` to its own repo** | After M9 | Structurally ready (ADR-0011). Zero benefit until there is a second consumer. |
| **Linter and formatter** | M1 | Right idea, but a codebase-wide reformat now would obscure this PR's diff. |
| **Runtime AI-guard assertion** (§2.8) | M6 | Belongs with the code it guards. Document the current limits now. |
| **Streaming/indexed validation** | Never, probably | Full materialisation is fine to ~50k objects. Optimising for a scale this project will not reach is the definition of premature. |
| **Embeddings / semantic search** | Post-v1, if ever | ADR-0003 is right: `grep` plus generated indexes first. Revisit only on demonstrated failure. |
| **Doc-drift automation** (§2.6) | Post-v1 | Real problem, no cheap solution. Manual review per milestone for now. |
| **Cross-pack relationship validation** | M8 | Nothing to validate until a second pack exists. |
| **Single-sourcing the version** | Post-v1 | Genuinely trivial. `importlib.metadata` adds indirection for one duplicated string. |

---

## 7. Verdict

**M0 is architecturally sound and should be merged — after §5.1 and §5.2.**

The foundation is better than most first milestones. The ownership model is the
right solution to a risk that was *created* by the design rather than inherent to
it, and catching that during architecture review rather than in production is the
single best thing that happened in this milestone. Making deletion
unrepresentable, enforcing the AI rule before the code that could break it, and
building test fixtures independently of the writer are all decisions that
experienced teams routinely get wrong.

The four confirmed defects are all **latent** — none breaks M0 today, because M0
has no data. That is precisely why they should be fixed now: each one is minutes
of work today and a debugging session inside a later milestone otherwise. Two of
them (§2.1, §2.2) directly threaten milestones that begin immediately.

The pattern worth naming: **three of the four defects are invisible with one pack
and zero objects** — the exact state M0 ships in. The test suite is strong but
tests the world as it is today, not as M2 and M8 will make it. A two-pack fixture
and one populated-object fixture would have caught all three.

Nothing in this review argues for changing the architecture. Every finding is an
implementation gap, a missing test, or a decision that has not been made yet. The
ADRs hold up under scrutiny; I did not find one I would overturn.

**Recommendation:** fix §5.1 and §5.2 in this PR, merge, then open a short
pre-M1 cleanup for §5.4–§5.8. §5.3 is a conversation to have before M1 starts and
is more important than any of the code changes above.
