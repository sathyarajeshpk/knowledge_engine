# M8 — PR Review Summary

**Milestone:** M8 — the second Domain Pack
**Release:** v0.9.0
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect

---

## What was built

M8 existed to test one sentence, written in M0 and repeated in every review
since:

> A Domain Pack is pure data. Adding one requires no engine change.

Nine milestones of code had been written with exactly one pack in the
repository. The instruction for this milestone was explicit: *if any engine code
must change specifically to support the second pack, stop and explain why.*

Nothing was stopped for.

```
$ git show --stat --name-only 2a3c9e9 -- engine/ | wc -l
0
```

The Azure pack adds 200 knowledge objects, 10 categories, 29 classification
rules across 6 axes, an independent RSS source with a manual fallback, and its
own permanent ID namespace (`AZ`). No file under `engine/` appears in that
commit.

Then having two of something exposed four defects that eight milestones with one
of it could not.

---

## Decisions made, and why

### Azure, not Power BI (ADR-0043)

The plan named a Power BI pack as the abstraction proof. By M8 that choice had a
problem the plan could not have seen: **Microsoft publishes Power BI
announcements inside the Fabric release feeds the first pack already harvests.**

A Power BI pack would have shared Fabric's sources almost entirely. Every
announcement would have been discovered twice and minted under two permanent
Feature IDs, and the cross-pack duplicate queue would have filled with pairs that
are not mistakes. That tests the duplicate machinery thoroughly and pack
independence not at all.

I asked rather than decided, and gathered the evidence first. Azure has genuinely
separate sources, taxonomy, vocabulary and release cadence — an independent
real-world domain rather than a slice of an existing one. ADR-0016 stands
unamended; Power BI remains a first-class category inside the Fabric pack.

### Cross-pack duplicates: detect and report, never resolve (ADR-0044)

Two objects sharing a canonical URL across packs is often *correct* — one
announcement filed under two taxonomies, useful to two different questions. The
engine cannot tell that from a true duplicate, so it does not try. Both objects
are minted, stored and indexed; neither is modified.

Three properties make that trustworthy, and all were requested explicitly:

* **Detection is not a pipeline stage.** It reads what is on disk *after* all
  packs have harvested. A naive implementation asking "is this already in another
  pack?" during minting would depend entirely on run order.
* **Output is order-independent.** Packs sorted before reading, objects already
  in sorted path order, pair sides sorted by Feature ID. Feature IDs are
  unaffected by pack order — each pack mints from its own registry.
* **Resolutions live at the repository root.** A fact about two packs stored
  inside one of them is a fact that will eventually disagree with its copy.

### The pack capability surface is closed (ADR-0045)

ADR-0016's claim is that a pack is data reviewed as data. The security review
found two things a pack could do that such a review would not catch. ADR-0045
enumerates what a pack may do — name web sources, match strings — and `ke
validate` enforces it on every pull request.

### What was deliberately *not* done

**The O(packs²) index rebuild was measured and left.** The fix — computing
cross-pack duplicates once per run instead of once per pack — is not difficult,
but it changes *when* detection runs relative to harvest, and it interacts with a
staleness question (O-3). CLAUDE.md says not to change the agreed architecture
unilaterally mid-implementation. Written up with measurements, projections and a
recommendation, for a decision before M9.

A factor-of-two waste *inside* it was fixed, because that was pure duplication
with no design implication.

---

## Defects found and fixed

All four were invisible with a single pack. Every one produced a clean,
successful run.

| # | Defect | Severity | How it was found |
|---|---|---|---|
| 1 | A pack source naming `file://` read local files into stored, committed knowledge | **Critical** | Reading the fetcher for the security review, then verifying against the real code |
| 2 | A symlink in a pack redirected every automated write | **High** | Asking what "reviewed as data" fails to cover |
| 3 | One failing pack took the whole harvest run down | **High** | Writing the test the readiness review said was missing |
| 4 | `ke review --kind cross-pack` rejected by its own CLI | Medium | Installation-level test |

Plus a fifth found *after* it had been fixed, tested and written up:

**The `file://` guard was unreachable from `ke validate`.** `Pack.source_definitions`
is a lazy property, so the allowlist fired only when something asked for the
sources — and validation never did. A hostile pack printed
`ok: 1 pack(s), 0 knowledge object(s), no findings` and would have failed at
03:00 on Sunday instead, inside the process holding the write token.

Found by an installation-level test running the real console script, after the
in-process tests were green and the security review already claimed CI caught it.
The claim was corrected in the same commit that fixed the code.

> **The lesson, recorded in ADR-0045 and the security review:** a guard that is
> never invoked and a guard that does not exist are the same guard. Testing the
> library cannot tell them apart.

And a sixth, in the measuring apparatus rather than the engine:

**The benchmark was wrong by 4–5×.** It timed everything with `tracemalloc`
running, which hooks the allocator. It reported 17 s to harvest 100 objects; the
real figure is 3.2 s. The run completed, its abort guard confirmed the objects
were on disk, and the numbers were internally consistent across every shape —
consistently inflated. Caught only by asking whether 13 ms to parse one small
YAML file is believable.

---

## Test summary

**671 passed, 1 skipped.** Up from 600 at v0.8.0.

| File | Tests | Covers |
|---|---:|---|
| `test_discovery.py` | 91 | Adapters, normalisation, identity |
| `test_validate.py` | 57 | Schema, IDs, registry, cross-pack, **SEC002** |
| `test_models.py` | 54 | Field ownership, single-line enforcement |
| `test_security.py` | 49 | Redaction, injection, **URL scheme allowlist** |
| `test_generate.py` | 45 | Templates, context packs, **data boundary** |
| `test_pipeline.py` | 34 | Stage ordering, failure policies |
| `test_retrieve.py` | 28 | Search, filters, rendering |
| `test_operational.py` | 27 | Failure scenarios, **per-pack isolation** |
| `test_digest.py` | 27 | Weekly digest |
| `test_history.py` | 26 | Revisions, supersession |
| `test_classification.py` | 25 | Rules, overrides |
| `test_crosspack.py` | 23 | **Duplicate detection, order independence, resolutions** |
| `test_paths.py` | 21 | **Symlink containment, SEC001** |
| `test_update_path.py` | 21 | In-place updates, preservation |
| `test_regressions.py` | 11 | Every previously-fixed defect |
| `test_packaging.py` | 11 | **The installed package**, not the source tree |
| `test_pack.py`, `test_architecture.py`, `test_workflow_push.py`, `test_cli.py` | 34 | Structure, invariants, workflow, CLI |

### Mutation testing

Every new guard in this milestone was verified by reverting it and confirming a
test fails. It corrected me four times:

* `contained()` written with `startswith` passed every test but one — the
  sibling-with-a-shared-prefix case (`/a/repo-backup` is not inside `/a/repo`).
* The first write-time containment guard checked a path against the very argument
  it was built from, proving only that `..` does not appear in a Feature ID.
* A "case-insensitive scheme" test asserted `FILE://` is rejected — true under
  both spellings, pinning nothing. The real property is that `HTTPS://` is
  *accepted*.
* The order-independence tests were overstated: removing either single sorting
  guard left them passing. Their docstrings now say they pin the property, not
  either implementation.

---

## Technical debt

> **Note on numbering:** TD identifiers have collided across earlier milestones —
> `TD-11` and `TD-14` each refer to different things in different documents. This
> table is self-contained and uses the M7-era numbering for carried items. The
> numbering itself is worth cleaning up in M9.

| ID | Item | Severity | Status |
|---|---|---|---|
| TD-8 | Pin GitHub Actions to commit SHAs | Medium | **Still carried.** Needs repository access this session does not have |
| TD-10 | `ke repair --registry` for an ID gap after a crash | Medium | Carried to M9 |
| TD-11 | `docs/RUNBOOK.md` | Low | Carried to M9 |
| TD-12 | `load_existing_objects` is misnamed (returns index-relative paths) | Low | Carried |
| TD-14 | The packaging test installs unpinned in CI | Low | Carried |
| **TD-15** | **Index rebuild is O(packs²)** | **High at 5+ packs** | **New.** Measured; fix recommended for M9 |
| **TD-16** | Cross-pack detection sees other packs one run stale | Medium | **New.** Same fix as TD-15 |
| **TD-17** | Nothing validates that a pack's classification rules are *correct* | Medium | **New.** Process gap; `ADDING-A-PACK.md` warns |
| **TD-18** | TD identifiers have collided across milestones | Low | **New.** Meta |

Not debt, but worth restating: **35 `REV002` warnings** persist on Fabric objects.
They are residue from the M3 flip-flop bug, fixed in M3, and they remain because
history is never rewritten. Correct behaviour, permanently visible.

> **⚠ Corrected 2026-08-08 — see [`docs/CORRECTIONS.md`](../CORRECTIONS.md) entry C-1.** The attribution to the M3 flip-flop bug above is **wrong**. All of these revisions date from 2026-08-01 and were produced by four harvest runs that each appended two revisions to the same object. The original wording is left in place deliberately; the correction record explains what was believed, what the evidence shows, and why the conclusion failed.


---

## Risks for M9

| Risk | Impact | Mitigation |
|---|---|---|
| **The O(packs²) decision is deferred again** | The fifth pack discovers the cost the ninth would have made unbearable | It is the first item on the M9 roadmap, with measurements attached |
| **`ke migrate` is written against a schema change that never happened** | A migration path designed in the abstract, exercised for the first time on real knowledge | Design the first migration against a *concrete* proposed schema change, not a hypothetical one |
| **Pack three is added without reading its output** | The Azure GA rule classified previews as tier 1 through structurally valid YAML; only reading the knowledge caught it | `ADDING-A-PACK.md` makes "read what it produced" a numbered step; consider a first-harvest sanity report |
| **Repository size at 100,000 objects** | ~1.4 GB and 200,000 files; the repository breaks before the engine does | Not near it (422 objects). Splitting packs is the existing architectural answer |
| **`ke validate --strict` in CI** | 35 REV002 warnings would fail the build immediately | Decide whether to grandfather historical warnings before tightening (M9 roadmap item) |
| **TD-8 stays carried a third milestone** | Unpinned Actions remain a supply-chain surface | Needs a session with repository write access; flag it explicitly rather than carrying it silently again |

---

## Assessment

The milestone did what it was for. Pack-agnosticism is no longer a design
intention — it is a measured property, demonstrated against an independent
real-world domain with 200 objects and zero engine files.

The more useful result is the second-order one. Having two packs surfaced a
critical security defect, a high-severity one, an availability defect and a CLI
defect, all of which had existed for eight milestones and none of which could be
seen with one pack. That is the strongest argument available for the value of
this milestone happening *before* seven more packs, rather than after.

The one thing I would not carry further is **TD-15**. It is not a problem at two
packs and it is a real problem at nine, and the roadmap has nine.
