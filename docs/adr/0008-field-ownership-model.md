# ADR-0008: Field ownership model

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

`metadata.yaml` mixes two categories of information in one file:

- **Facts derived from the source** — title, URL, publication date, content hash.
  The engine must be free to refresh these whenever the source changes.
- **The user's own work** — `learning_status`, `notes`, curated relationships,
  and any classification judgement they disagreed with and corrected.

A scheduled job rewrites these files every Sunday. Without an explicit rule, the
first time Microsoft edits an article, the job overwrites the file and the user's
notes are gone. Git would hold the history, but nobody would know to look — the
loss is silent, which is the worst property a data-loss bug can have.

This risk did not exist in the original design. It was created by the decision to
store learning metadata alongside engine metadata, which is otherwise clearly
correct. So the risk has to be engineered away rather than avoided.

## Decision

Every metadata field belongs to **exactly one ownership class**, declared in
`engine/ke/models.py`:

| Class | Engine behaviour | Examples |
|---|---|---|
| **Engine-owned** | Rewritten freely on every run | `title`, `source_url`, `content_hash`, `reading_time`, `revisions` |
| **Engine-proposed** | Written only if absent, or if not locked | `tier`, `learning_priority`, `category`, `tags`, `difficulty`, `workload`, `version` |
| **User-owned** | **Never written by the engine** | `learning_status`, `notes`, `prerequisites`, `builds_on`, `related_topics`, `replaced_by`, `replaces`, `overrides` |

Everything under `artifacts/`, `images/` and `references/` is user-owned. The
engine writes there only via an explicit `ke generate --attach`.

Three mechanisms enforce this:

1. **Import-time assertions** prove the three classes are disjoint. Overlapping
   classes break the package import.
2. **`KnowledgeObject.with_engine_fields()`** is the only path for automated
   writes. It raises `PermissionError` rather than writing a field it does not
   own, and returns a copy, so a rejected write cannot partially apply.
3. **`ke validate`** checks that `overrides` names only engine-proposed fields
   (`OWN001`, `OWN002`).

A user locks a proposed field by naming it in `overrides`:

```yaml
difficulty: advanced
overrides: [difficulty]
```

## Consequences

### Positive
- **The dominant data-loss risk is eliminated structurally.** Not by convention,
  not by code review — by an exception.
- **New fields are protected automatically.** `ownership_of()` raises `KeyError`
  for undeclared fields, so a field cannot reach serialisation without an owner.
  `test_every_serialised_field_has_a_declared_owner` proves this for fields not
  yet written.
- **The user can disagree with the engine and make it stick.** `overrides` turns
  a one-off correction into a permanent one.
- **The rule is discoverable.** The three frozensets are the documentation.
- **Errors arrive at the earliest possible moment** — import time for a
  registry mistake, test time for a serialisation mistake, never at 3am on a
  Sunday.

### Negative
- **Adding a field means touching four places**: the dataclass, one ownership
  set, both serialisation methods, and `docs/SCHEMA.md`. Deliberate friction on
  exactly the operation that could otherwise create a silent hole.
- **`overrides` is a concept the user has to learn.** Mitigated by documenting it
  in `docs/SCHEMA.md` §3 and the README.
- **Locking is per-field, not per-value.** Locking `tags` locks the whole list;
  you cannot lock one tag and let the engine manage the rest. Acceptable for now;
  revisit if it bites.
- **`overrides` is itself user-owned**, so the engine cannot lock a field on the
  user's behalf. That is correct but occasionally inconvenient.

### Neutral
- Slightly more code than trusting the pipeline to behave.
- The classes are a design decision that will need revisiting as fields are
  added — for example, whether `category` should be proposed or user-owned once
  M3 has real classification rules.

## Alternatives considered

**Convention plus code review.** "Remember not to write user fields." Rejected:
this fails the first time someone adds a field in a hurry, and the failure is
silent and permanent-feeling.

**Separate files for user data** — `metadata.yaml` engine-owned,
`user.yaml` user-owned. Genuinely appealing: the boundary becomes physical and
unmissable. Rejected because it splits one object's state across two files that
must be read together, doubles the drift surface already accepted in ADR-0007,
and makes a single field's ownership change a file migration. The registry
approach gives the same guarantee with one file. **This remains the most
plausible alternative if the current model proves fragile.**

**Git-based recovery.** "The history has it, restore if needed." Rejected: silent
loss you never notice is not recoverable in practice. Prevention beats forensics.

**A `readonly:` marker per field in the file.** Rejected: puts the rule in the
data where each object could disagree, rather than in the schema where it is
uniform and checkable.

**Making `KnowledgeObject` frozen entirely.** Rejected: it is built up in stages
during harvest, and immutability alone would not say *who* may write *what*.
