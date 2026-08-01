"""Detecting real change, and updating an object without destroying its owner's work.

This is the module the field ownership model (ADR-0008) was written for. Until
now the engine only ever *created* objects, so "the weekly job must not clobber
your notes" was a promise. From here it is executable.

Three rules, in order of how expensive they are to get wrong:

1. **User-owned fields are never written.** Learning state, notes, relationships
   and everything under `artifacts/`, `images/`, `references/` belong to the
   user. Every write here goes through `KnowledgeObject.with_engine_fields`,
   which raises rather than touching them.
2. **A revision is appended only on real change.** A run that finds nothing
   different must write nothing at all -- not a re-serialised file with an
   identical body, which would produce a diff every week and make real changes
   invisible.
3. **The Feature ID never changes.** Not on retitling, not on a moved URL, not
   on supersession. It is the one field an update may not touch.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from ke.models import (
    ENGINE_OWNED_FIELDS,
    KnowledgeObject,
    RawItem,
    Revision,
)
from ke.normalize import content_hash, url_hash

#: Engine-owned fields an update may refresh. Deliberately a subset of
#: `ENGINE_OWNED_FIELDS`: identity, provenance of first discovery, and the
#: revision history itself are engine-owned but **not** re-derivable from a
#: later sighting, so they are excluded rather than trusted not to change.
UPDATABLE_FIELDS = frozenset(
    {
        "title",
        "source_url",
        "announcement_url",
        "published_date",
        "date_confidence",
        "date_precision",
        "content_hash",
        "url_hash",
        "identity_confidence",
        "reading_time",
    }
)

#: Never updated, even though the engine owns them. `id` and `slug` define the
#: object's permanent path; `discovered_date` records when knowledge first
#: appeared; `revisions` is append-only and handled explicitly.
FROZEN_AFTER_MINT = frozenset({"id", "slug", "discovered_date", "revisions", "provenance"})

assert UPDATABLE_FIELDS <= ENGINE_OWNED_FIELDS, (
    "an updatable field must be engine-owned"
)
assert not (UPDATABLE_FIELDS & FROZEN_AFTER_MINT), (
    "a field cannot be both updatable and frozen"
)


def detect_changes(existing: KnowledgeObject, item: RawItem) -> dict[str, object]:
    """Which updatable fields this sighting would change.

    Returns an empty dict when nothing meaningful differs -- which is the common
    case, and the one that must produce no write at all.

    `content_hash` is the primary signal: it is computed over normalised title
    and summary, so reflowed whitespace does not register as a change (ADR-0009).
    The other fields are compared directly because each can move independently:
    a source can correct a date without touching the prose, or move an article
    without rewording it.
    """
    incoming = {
        "title": item.title,
        "source_url": item.source_url,
        "announcement_url": item.announcement_url,
        "published_date": item.published_date,
        "date_confidence": item.date_confidence,
        "date_precision": item.date_precision,
        "content_hash": content_hash(item.title, item.summary),
        "url_hash": url_hash(item.source_url),
        "identity_confidence": item.identity_confidence,
    }
    return {
        name: value
        for name, value in incoming.items()
        if getattr(existing, name) != value
    }


def is_material(changes: dict[str, object]) -> bool:
    """Whether a change deserves a revision entry.

    `identity_confidence` alone is not material: it is a per-run assessment
    (ADR-0028) and can legitimately move between runs when an unrelated item
    stops sharing an announcement. Recording a revision for that would fill the
    history with entries that say nothing about the knowledge.
    """
    return bool(set(changes) - {"identity_confidence"})


def apply_update(
    existing: KnowledgeObject,
    item: RawItem,
    changes: dict[str, object],
    *,
    today: date,
    run_id: str | None,
) -> KnowledgeObject:
    """Return the updated object, with a revision appended if the change is real.

    Goes through `with_engine_fields`, so an attempt to write a user-owned field
    raises `PermissionError` rather than silently succeeding. That is the whole
    safety property, and it is enforced by the model rather than by this
    function's good intentions.
    """
    if not changes:
        return existing

    updates = dict(changes)
    if is_material(changes):
        next_revision = Revision(
            revision=len(existing.revisions) + 1,
            date=today,
            changed_fields=tuple(sorted(changes)),
            summary=_describe(changes),
            content_hash=content_hash(item.title, item.summary),
            title_snapshot=item.title,
            summary_snapshot=item.summary,
            run_id=run_id,
        )
        updates["revisions"] = (*existing.revisions, next_revision)

    return existing.with_engine_fields(**updates)


def _describe(changes: dict[str, object]) -> str:
    """A one-line human summary of what moved.

    Written for someone reading `metadata.yaml` in the GitHub UI months later,
    so it names the fields rather than quoting values -- the values are already
    in the snapshots.
    """
    names = sorted(changes)
    if "title" in names and "content_hash" in names:
        return "Source retitled and rewrote the summary"
    if "title" in names:
        return "Source retitled this item"
    if "content_hash" in names:
        return "Source rewrote the summary"
    if "published_date" in names:
        return "Source corrected the publication date"
    if {"source_url", "url_hash"} & set(names):
        return "Source moved this item to a new URL"
    return "Updated: " + ", ".join(names)


def user_owned_snapshot(obj: KnowledgeObject) -> dict[str, object]:
    """Everything the engine must not have touched, for a before/after check.

    Used by the preservation test rather than by the pipeline. Comparing this
    dict across an update is a stronger assertion than checking individual
    fields, because it fails when a *new* user-owned field is added and the
    update path forgets about it.
    """
    from ke.models import USER_OWNED_FIELDS

    return {name: getattr(obj, name) for name in sorted(USER_OWNED_FIELDS)}
