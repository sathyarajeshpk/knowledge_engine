# ADR-0015: Create object and pack subdirectories on demand

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0
**Amends:** [ADR-0006](0006-directory-per-knowledge-object.md)

## Context

ADR-0006 established that every knowledge object is a directory, and specified
that `artifacts/`, `images/` and `references/` are "created up front, so that
attaching the first artifact never has to create structure or move anything."

That reasoning was sound. The persistence mechanism was not considered.

**Git cannot track an empty directory.** Verified:

```bash
mkdir -p /tmp/t/obj/{artifacts,images,references} && cd /tmp/t
git init -q . && touch obj/feature.md && git add -A && git status --short
# A  obj/feature.md          ← the three directories are simply absent
```

The consequences, once M2 writes real objects:

1. The subdirectories are created locally, committed without them, and **absent
   on every fresh clone and every CI run**.
2. `ke validate` emits three `OBJ005` warnings *per object*. At a few hundred
   objects that is a thousand warnings, which destroys the validator's signal
   value.
3. The roadmap's plan to tighten CI to `--strict` becomes permanently
   unreachable, because every object carries three warnings that cannot be
   fixed.

The same flaw applied one level up, and worse. `PACK004` **required**
`knowledge/`, `indexes/`, `digests/` and `state/`. Three of those are empty in a
new pack, so a pack created by the recipe in `CONTRIBUTING.md` fails validation
with hard **errors** after a clone. The Microsoft Fabric pack only survived
because `.gitkeep` files had been added to it — a prop, not a design.

## Decision

**Directories are created when something is written into them, not in advance.**

- A knowledge object directory contains only `feature.md` and `metadata.yaml`
  until an artifact exists. `artifacts/`, `images/` and `references/` are created
  by `ke generate --attach` (M7) at the moment they are first needed.
- Within a pack, only **`state/`** is required, because it is the only pack
  directory that always holds committed files (`id-registry.json`, `seen.json`,
  `run-log.md`). `knowledge/`, `indexes/` and `digests/` are created on demand by
  the code that writes into them.
- The `.gitkeep` files propping up the old requirement are removed.

Validation changes accordingly. The `OBJ005` "missing standard subdirectory"
check is **retired** — it checked for empty scaffolding, which told us nothing.
It is replaced by checks on state that actually exists:

| Code | Check |
|---|---|
| `GEN001` | An artifact marked `generated`/`stale` records no path |
| `GEN002` | An artifact path is outside `artifacts/`, `images/` or `references/` |
| `GEN003` | An artifact marked `generated`/`stale` whose file is missing |

`GEN003` is the check that was actually wanted: artifacts are marked stale, never
deleted, so a missing artifact file is a genuine integrity failure.

**ADR-0006's core decision is unchanged.** An object is still a directory, and
its path is still stable for its entire lifetime. Only the "created up front"
detail is amended.

## Consequences

### Positive
- **The repository state on disk matches the state in Git.** No divergence
  between what a developer sees locally and what CI sees.
- **`--strict` becomes reachable**, which the roadmap depends on.
- **The empty-directory cost ADR-0006 explicitly accepted disappears.** That
  trade no longer has to be made at all.
- **`.gitkeep` files are gone**, along with the class of bug where forgetting one
  breaks a pack.
- **Validation moved from checking scaffolding to checking integrity.** `GEN003`
  catches a real failure; `OBJ005` could only ever catch a self-inflicted one.
- **Creating a new pack is genuinely just `pack.yml` plus `state/`**, which is
  simpler to document and harder to get wrong.

### Negative
- **Writers must create parent directories.** `store.py` (M2) and
  `generate.py` (M7) need `mkdir(parents=True, exist_ok=True)` before writing.
  One line each, and `Path.write_text` failing loudly on a missing parent is a
  well-understood failure mode.
- **The object layout is less self-documenting on disk.** You cannot `ls` an
  object and see where artifacts would go. Mitigated by `docs/SCHEMA.md`.
- **`OBJECT_SUBDIRS` becomes a validation allow-list rather than a creation
  list.** Its meaning changed; its name did not.

### Neutral
- `iter_object_dirs()` already tolerated a missing `knowledge/` directory, so no
  change was needed there.
- Retiring `OBJ005` leaves a gap in the `OBJ*` code sequence. Codes are never
  reused, so the gap is correct and intentional.

## Alternatives considered

**Commit a `.gitkeep` in every subdirectory.** The conventional workaround, and
what the repository was accidentally already doing at pack level. Rejected: three
extra files per object — thousands of meaningless files — and it only works while
every writer remembers to create them. It treats the symptom (Git will not store
this) rather than the cause (we are storing nothing).

**Keep `OBJ005` but downgrade it to informational.** Rejected: a check that fires
on every object and means nothing is noise, and noise trains people to ignore the
validator.

**Store artifacts in a single file per object** (e.g. all artifacts inside
`metadata.yaml`). Rejected by ADR-0006 already — cannot hold binaries, and
conflates curated knowledge with generated content.

**Keep creating the directories locally and accept that Git drops them.**
Rejected: it guarantees local and CI behaviour differ, which is the worst
property a validation system can have.
