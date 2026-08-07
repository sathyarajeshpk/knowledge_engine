# M8 — Architecture Review (including Cross-Pack Architecture)

**Milestone:** M8 — the second Domain Pack
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** whether the pack abstraction is real, and what having two of them
exposed

---

## The claim under test

M8 exists to test one sentence written in M0 and repeated in every review since:

> A Domain Pack is pure data. The engine addresses packs by path and holds no
> knowledge of any specific pack. (ADR-0016, ADR-0011)

Nine milestones of code were written with exactly one pack in the repository. A
claim like that, unexercised, is a design intention rather than a property.

The instruction for this milestone was explicit: *if any engine code must change
specifically to support the Azure pack, stop and explain why.*

---

## Result: zero engine files changed for the Azure pack

```
$ git show --stat --name-only 2a3c9e9 -- engine/ | wc -l
0
```

Commit `2a3c9e9` adds the Azure pack: `pack.yml` with 10 categories and 29
classification rules across 6 axes, one RSS source with a manual fallback, and
200 harvested knowledge objects with their indexes and first digest. **No file
under `engine/` appears in it.**

Nothing was stopped for, because nothing needed to be.

### What M8 *did* change in the engine, and why none of it is Azure

Nine engine files changed across the milestone. Every one is a pack-agnostic
capability or a defect fix, and every one would have been needed identically if
the second pack had been Snowflake, AWS or Personal Knowledge:

| File | Why | Azure-specific? |
|---|---|---|
| `crosspack.py` (new) | Cross-pack duplicate detection and referential integrity | No — a property of *having* two packs |
| `paths.py` (new) | Symlink containment | No — a security boundary, from the review |
| `validate.py` | REF001, XPK001, SEC001, SEC002 | No |
| `pack.py` | Refuse a symlinked pack root | No |
| `store.py` | Refuse a symlinked knowledge tree | No |
| `reviewq.py` | Cross-pack task provider; stop double-scanning | No |
| `acquisition/sources/base.py` | URL scheme allowlist | No |
| `generate.py` | Data boundary in context packs | No |
| `__main__.py` | Wire `--kind cross-pack` | No |

The distinction that matters: **adding a pack cost nothing; having two packs cost
a feature.** Those are different claims, and only the first one was being tested.

### Where the abstraction held under real pressure

Azure is not a cosmetic second pack. It differs from Fabric in every dimension the
engine could plausibly have hard-coded:

* **Different vocabulary.** Azure phrases previews as *"now available in public
  preview"*; Fabric says *"(preview)"*. Handled by rules in `pack.yml`, not code.
* **Different taxonomy.** 10 Azure categories with no overlap with Fabric's.
* **Different ID namespace.** `AZ-2026-07-038` from its own registry and its own
  per-month counters.
* **Different date behaviour.** Azure's feed carries retirement announcements
  dated years back; `AZ-2025-09-001` sits correctly in a 2025 directory.
* **Different source shape.** One RSS endpoint plus a manual fallback, against
  Fabric's multi-source chain.

The one place a pack-specific lesson had to be learned twice is worth recording
honestly: Azure's GA rule initially lacked `none: [preview]`, so *"now generally
available in public preview"* classified as tier 1. The Fabric pack already
carried that guard. This is a **pack authoring** failure, not an engine one — the
vocabulary was correctly treated as pack knowledge — but it is the sort of thing
`docs/ADDING-A-PACK.md` must warn about, because the rule engine will happily
match a substring inside a phrase that negates it.

---

## Cross-Pack Architecture Review

The ten questions asked of this milestone, answered with evidence.

### 1. Can a pack be added without modifying `engine/`?

**Yes, demonstrated.** Zero engine files in the Azure commit. See above.

### 2. Cross-pack relationships and dependency validation

Relationships may point across packs — an Azure object may legitimately be a
prerequisite for a Fabric one. `known_feature_ids` resolves a reference against
the **whole repository**, so a cross-pack link is valid and a typo is still
caught as `REF001` (ERROR).

This required a real change in stance: per-pack validation would have reported
every cross-pack reference as dangling. Whole-repository checks run only when the
whole repository was validated — under `--pack` the other packs were deliberately
not loaded, and reporting a filtered-out target as missing would be a false alarm
caused by the flag rather than by the data.

### 3. Pack isolation

Each pack keeps its own `seen.json`, `id-registry.json`, `run-log.md`,
`source-health.json`, `events.jsonl`, indexes and digests. Nothing in the harvest
path for pack A reads or writes anything under pack B.
`test_each_pack_mints_from_its_own_registry` pins this.

### 4. Failure isolation

`Pack.find_roots` never parses anything, so callers load each root themselves and
a single malformed `pack.yml` becomes a `PACK005` finding while every other pack
still validates (`test_a_broken_pack_does_not_hide_another_packs_duplicates`,
`test_one_pack_failing_does_not_stop_another`).

### 5. Pack-specific vs engine configuration

The line held without needing to be redrawn. Everything Azure needed —
vocabulary, taxonomy, thresholds, sources, prefix, limits — is in `pack.yml`.
The engine holds no default that is really a Fabric default.

ADR-0045 now enumerates the pack capability surface as a closed set, which turns
this from an observation into a contract.

### 6. Performance when multiple packs are harvested in one run

**Measured, and it is the milestone's main architectural finding.** Index rebuild
is O(packs²) in full-pack reads: 2,000 objects across ten packs rebuild in 82 s
against 23 s for the same 2,000 in one pack.

The cause is a chain that is individually reasonable at every link — index
rebuild writes the review queue, the review queue includes cross-pack duplicates,
cross-pack duplicates need every pack. Nothing is wrong; the cost is structural.
**A per-pack operation that depends on global state is quadratic in packs, and
cross-pack detection is inherently global.**

A factor-of-two waste inside it was fixed. The remaining `packs²` was
deliberately **not** fixed, because the fix changes *when* detection runs
relative to harvest — an architecture decision, not a cleanup, and CLAUDE.md says
not to make those unilaterally mid-milestone. Full analysis, projections and a
recommendation are in the M8 Performance Review.

### 7. Duplicate detection across packs

Detect and report, never merge, drop or block (ADR-0044). Verified against the
list requested:

| Property | Test |
|---|---|
| Symmetric — surfaced from both sides | `test_the_duplicate_is_surfaced_from_both_packs` |
| Canonical pair key regardless of order | `test_the_pair_key_is_canonical_whichever_pack_is_listed_first` |
| Feature IDs stable regardless of pack order | `test_harvest_order_does_not_change_feature_ids` |
| Report identical regardless of harvest order | `test_harvest_order_does_not_change_the_duplicate_report` |
| Byte-identical across repeated calls | `test_detection_is_byte_identical_across_repeated_calls` |
| Never modifies either object | `test_acknowledging_modifies_neither_object` |
| Enough evidence to decide without opening a file | `test_the_review_item_carries_enough_to_decide_without_opening_a_file` |
| A resolution stops weekly re-surfacing | `test_acknowledging_stops_it_being_surfaced` |
| Resolving either side clears both | `test_acknowledging_from_one_side_clears_both` |
| Both objects kept | `test_both_objects_are_minted_and_kept` |

Order independence is not accidental and is worth naming precisely: packs are
sorted by name before anything is read, objects already arrive in sorted path
order, and each pair's sides are sorted by Feature ID. Mutation testing corrected
an overstatement here — removing either single guard leaves the tests passing;
only removing both fails. The tests pin the *property*, not either
implementation, and their docstrings now say so.

### 8. Pack version compatibility

`schema_version` is per-pack and checked against `SUPPORTED_SCHEMA_VERSIONS`
(`PACK002`), so packs may sit at different schema versions and be migrated
independently. Both shipped packs are at version 1, so this is designed-for
rather than exercised — recorded as such rather than claimed as proven.

### 9. Migration when packs evolve independently

Untested, by construction: `ke migrate` is M9 work and no schema change has
happened yet. The structural precondition is in place — migration is per-pack
because `schema_version` is per-pack — but this is the weakest of the ten
answers and should not be read as more than that.

### 10. Long-term repository growth, memory and runtime scaling

Covered quantitatively in the M8 Performance Review. Summary: memory is
4.8 KB/object and does not multiply with pack count; runtime is linear in objects
and quadratic in packs; repository growth at ~14 KB/object means **the repository
becomes the binding constraint before the engine does**, at roughly 100,000
objects.

---

## What having a second pack revealed that one pack could not

Three things, all of which had been invisible:

1. **Repo-level state had no home.** Every piece of state until M8 belonged to a
   pack. A cross-pack acknowledgement belongs to neither, and storing it in both
   is how two copies come to disagree. `state/cross-pack.json` at the repository
   root is the first genuinely repo-level state the project has needed.

2. **`--pack` had become a correctness flag, not just a filter.** Whole-repository
   checks must not run under it (false dangling references) — except the security
   ones, which must (a flag that switches off a security check will be used to
   switch off a security check). That distinction did not exist with one pack.

3. **A per-pack loop over global state is quadratic.** Finding 6. With one pack,
   `packs²` and `packs` are the same number.

---

## Assessment

The pack abstraction is **real**, and now also **bounded**. M8 proved
pack-agnosticism in the strongest available way — an independent real-world
domain, 200 objects, zero engine files — and then found that the abstraction's
security premise ("data needs no engine review") was not yet safe to rely on,
and fixed that.

The one thing that would concern me carrying into M9 is finding 6. It is not a
problem at two packs and it is a real problem at nine, and the roadmap has nine.
It should be decided before the third pack lands, not after the tenth.
