"""Reading an object's history back, and superseding one feature with another.

M2 through M4 wrote revision snapshots and nothing ever read them. A data model
nobody reads is a data model nobody has validated -- so this module exists as
much to *check* the Time Machine as to use it.

Two capabilities:

**Time travel.** `ke history <id>` reconstructs what an object looked like at any
revision, from the snapshots stored inside it. No Git archaeology, no AI, no
network -- the object carries its own past (ADR-0020).

**Supersession.** `ke supersede <old> --by <new>` records that one feature
replaced another. Nothing is deleted: the old object keeps its Feature ID, its
history and its place in the repository, and gains a pointer forward.

## Why supersession does not use the lifecycle enum

`Lifecycle.SUPERSEDED` was defined speculatively in M3 and ADR-0029 flagged the
name as the softest part of that design. Building the feature settled it: an
object whose *feature* was replaced has still been fully **acquired** -- its
lifecycle is `minted` and nothing about acquisition changed. Supersession is a
statement about the knowledge, so it belongs to `status`, which already had
`replaced`. The lifecycle value had no user and has been removed (ADR-0035).

## Why this may write user-owned fields

`replaced_by` and `replaces` are user-owned (ADR-0008), and the automated
pipeline must never touch them. `ke supersede` is not the pipeline: it is a human
issuing an explicit instruction. The distinction is *who asked*, not *which
process writes*, and it is enforced by this being the only path that bypasses
`with_engine_fields` -- deliberately, visibly, and only for the two relationship
fields the command exists to set.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from ke.ids import IdRegistry
from ke.models import KnowledgeObject, ObjectStatus, Revision
from ke.pack import Pack
from ke.store import load_object, update_object


class HistoryError(Exception):
    """The requested object or revision does not exist."""


@dataclass(frozen=True)
class Snapshot:
    """What an object looked like at one revision."""

    revision: int
    date: date
    title: str
    summary: str
    content_hash: str
    changed_fields: tuple[str, ...]
    note: str
    run_id: str | None

    @classmethod
    def from_revision(cls, rev: Revision) -> Snapshot:
        return cls(
            revision=rev.revision,
            date=rev.date,
            title=rev.title_snapshot or "",
            summary=rev.summary_snapshot or "",
            content_hash=rev.content_hash or "",
            changed_fields=tuple(rev.changed_fields),
            note=rev.summary or "",
            run_id=rev.run_id,
        )


def find_object(pack: Pack, feature_id: str) -> tuple[KnowledgeObject, object]:
    """Locate a stored object by Feature ID, via the registry."""
    registry = IdRegistry.load(pack.registry_path, pack.id_prefix)
    subpath = registry.path_for(feature_id)
    if subpath is None:
        raise HistoryError(f"{feature_id} is not in the registry")
    directory = pack.knowledge_dir / subpath
    obj = load_object(directory)
    if obj is None:
        raise HistoryError(f"cannot read the object at {subpath}")
    return obj, directory


def timeline(obj: KnowledgeObject) -> list[Snapshot]:
    """Every recorded state, oldest first."""
    return [Snapshot.from_revision(rev) for rev in obj.revisions]


def at_revision(obj: KnowledgeObject, revision: int) -> Snapshot:
    """What the object looked like at one revision.

    Raises rather than clamping to the nearest: silently answering a different
    question than the one asked is worse than saying the revision does not
    exist.
    """
    for snapshot in timeline(obj):
        if snapshot.revision == revision:
            return snapshot
    available = ", ".join(str(s.revision) for s in timeline(obj)) or "none"
    raise HistoryError(
        f"{obj.id} has no revision {revision} (available: {available})"
    )


def verify_chain(obj: KnowledgeObject) -> list[str]:
    """Check an object's history for the ways it can be wrong.

    Returns problems rather than raising, so `ke validate` can report every
    object's issues in one pass (ADR-0012).

    The chain is what makes time travel trustworthy. If revisions are missing,
    misnumbered or undated, the object can still be *read* -- it just cannot be
    *believed*, which is worse, because nothing about it looks broken.
    """
    problems: list[str] = []
    revisions = obj.revisions

    if not revisions:
        return [f"{obj.id}: has no revisions; even initial ingestion records one"]

    numbers = [rev.revision for rev in revisions]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append(
            f"{obj.id}: revision numbers are {numbers}, expected 1..{len(numbers)}"
        )

    if revisions[0].revision == 1 and revisions[0].changed_fields:
        problems.append(
            f"{obj.id}: revision 1 is initial ingestion and must list no changed fields"
        )

    previous_date = None
    for rev in revisions:
        if previous_date is not None and rev.date < previous_date:
            problems.append(
                f"{obj.id}: revision {rev.revision} is dated {rev.date}, "
                f"before revision {rev.revision - 1} ({previous_date})"
            )
        previous_date = rev.date

    latest = revisions[-1]
    if latest.content_hash and obj.content_hash != latest.content_hash:
        problems.append(
            f"{obj.id}: content_hash does not match the latest revision's snapshot; "
            "the object and its history disagree about its current state"
        )

    for rev in revisions[1:]:
        if not rev.changed_fields:
            problems.append(
                f"{obj.id}: revision {rev.revision} records no changed fields, "
                "so it should not exist -- a revision means something changed"
            )
    return problems


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------


def supersede(
    pack: Pack, old_id: str, new_id: str, *, today: date
) -> tuple[str, str]:
    """Record that `new_id` replaces `old_id`. Neither object is deleted.

    Writes both directions, because a one-way pointer degrades into a dangling
    reference the first time somebody reads from the other end. `ke validate`
    checks the pair stays consistent.
    """
    if old_id == new_id:
        raise HistoryError("an object cannot supersede itself")

    old_obj, old_dir = find_object(pack, old_id)
    new_obj, new_dir = find_object(pack, new_id)

    if old_obj.replaced_by and old_obj.replaced_by != new_id:
        raise HistoryError(
            f"{old_id} is already superseded by {old_obj.replaced_by}; "
            "supersession is recorded once and not rewritten"
        )

    # These are USER-OWNED fields, so this deliberately does not go through
    # `with_engine_fields`. A human asked for this explicitly; the automated
    # pipeline still cannot reach them. See this module's docstring.
    superseded = replace(
        old_obj,
        status=ObjectStatus.REPLACED,
        replaced_by=new_id,
        revisions=(
            *old_obj.revisions,
            Revision(
                revision=len(old_obj.revisions) + 1,
                date=today,
                changed_fields=("status", "replaced_by"),
                summary=f"Superseded by {new_id}",
                content_hash=old_obj.content_hash,
                title_snapshot=old_obj.title,
                summary_snapshot=_latest_summary(old_obj),
            ),
        ),
    )
    successor = replace(new_obj, replaces=old_id)

    update_object(
        old_dir, superseded, _latest_summary(old_obj),
        max_summary_words=pack.max_summary_words,
    )
    update_object(
        new_dir, successor, _latest_summary(new_obj),
        max_summary_words=pack.max_summary_words,
    )
    return (
        f"{old_id} is now replaced by {new_id} (status: replaced, retained)",
        f"{new_id} records that it replaces {old_id}",
    )


def _latest_summary(obj: KnowledgeObject) -> str:
    latest = obj.revisions[-1] if obj.revisions else None
    return (latest.summary_snapshot if latest else "") or obj.title


def render_timeline(obj: KnowledgeObject) -> str:
    """An object's history, for `ke history`."""
    lines = [
        f"\n{obj.id}  {obj.title}",
        f"  status: {obj.status} · lifecycle: {obj.lifecycle}",
    ]
    if obj.replaced_by:
        lines.append(f"  replaced by: {obj.replaced_by}")
    if obj.replaces:
        lines.append(f"  replaces: {obj.replaces}")
    lines.append("")

    snapshots = timeline(obj)
    for snapshot in snapshots:
        marker = "→" if snapshot.revision == snapshots[-1].revision else " "
        lines.append(f"  {marker} r{snapshot.revision}  {snapshot.date}  {snapshot.note}")
        if snapshot.changed_fields:
            lines.append(f"       changed: {', '.join(snapshot.changed_fields)}")
        if snapshot.title:
            lines.append(f"       title:   {snapshot.title[:70]}")
        if snapshot.run_id:
            lines.append(f"       run:     {snapshot.run_id}")
    lines.append("")

    problems = verify_chain(obj)
    if problems:
        lines.append("  ! history problems:")
        lines.extend(f"    {p}" for p in problems)
        lines.append("")
    return "\n".join(lines)
