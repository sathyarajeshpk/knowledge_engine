"""One review workflow over several kinds of pending decision.

By M4 the engine had produced two independent backlogs — 26 items held back from
minting and 45 flagged as unclassifiable — with no shared way to work them, and
M5 was about to add a third for revisions. Three queues growing in parallel is
how a review backlog becomes permanent: each one is somebody else's problem.

This module unifies the **workflow**, deliberately not the **storage**.

    ke review list        everything pending, from every source
    ke review next        the single most urgent item
    ke review show <id>   the full context for one decision
    ke review approve     act on one, or in bulk with a filter
    ke review archive
    ke review resolve

## Why storage stays separate

A queued item has no Feature ID and no knowledge object — it lives in
`state/review-queue.json` because there is nowhere else for it to live. A
flagged object *is* a knowledge object, and its `needs_review` flag belongs in
its own `metadata.yaml`, because the repository is the source of truth (ADR-0002)
and a fact about an object that lives somewhere else can go stale.

So the queues are different by necessity. What was missing was one **lens** over
them, and that is what a `ReviewTask` is: a uniform view assembled on demand,
never persisted, so it cannot drift from what it describes.

## Adding a kind

Write a provider returning `ReviewTask`s and add it to `PROVIDERS`. The listing,
filtering, bulk actions and `next` come for free. That is the property this
module exists to give M5's revision review — and M6's, and M7's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Callable

from ke.models import IdentityConfidence, KnowledgeObject
from ke.pack import Pack


class TaskKind(StrEnum):
    """What sort of decision is pending.

    Ordered by how expensive it is to leave undone, which is what `ke review
    next` sorts on.
    """

    #: Discovered but not minted: identity was not trusted enough (ADR-0028).
    #: Costly to defer — the knowledge is not in the pack at all yet.
    QUEUED = "queued"
    #: Minted, but no classification rule could place it (ADR-0010).
    #: The knowledge is safely stored; only its metadata is incomplete.
    UNCLASSIFIED = "unclassified"
    #: A stored object changed in a way worth a human look. Added in M5.
    REVISION = "revision"
    #: Two packs minted separate Feature IDs for what may be one feature.
    #: Added in M8, when a second pack made it possible for the first time.
    #: Nothing is merged or dropped — see `crosspack.py`.
    CROSS_PACK = "cross-pack"


#: Lower sorts first in `ke review next`.
KIND_URGENCY = {
    TaskKind.QUEUED: 0,
    # Above revisions: two permanent IDs for one feature is not lost knowledge,
    # but it is the kind of thing that gets harder to reason about the longer it
    # sits, because both objects keep accumulating revisions and artifacts.
    TaskKind.CROSS_PACK: 1,
    TaskKind.REVISION: 2,
    TaskKind.UNCLASSIFIED: 3,
}


class Action(StrEnum):
    """What a human can do with a task."""

    APPROVE = "approve"
    ARCHIVE = "archive"
    RESOLVE = "resolve"


@dataclass(frozen=True)
class ReviewTask:
    """One pending decision, in a form that reads the same whatever it came from.

    Assembled on demand and never stored. A persisted view of state that lives
    elsewhere is a second copy waiting to disagree with the first.
    """

    key: str
    kind: TaskKind
    title: str
    reason: str
    first_seen: date | None = None
    #: Feature ID when the task concerns a stored object; absent for queued items.
    feature_id: str | None = None
    actions: tuple[Action, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def short_key(self) -> str:
        """The identifier a human types back, digest prefix only."""
        return self.key.split(":", 1)[-1][:12]

    @property
    def urgency(self) -> int:
        return KIND_URGENCY.get(self.kind, 99)


# ---------------------------------------------------------------------------
# Providers — one per kind
# ---------------------------------------------------------------------------


def queued_tasks(pack: Pack) -> list[ReviewTask]:
    """Items held back from minting because identity was not trusted."""
    from ke.review import ReviewQueue

    queue = ReviewQueue.load(pack.state_dir / "review-queue.json")
    return [
        ReviewTask(
            key=entry.identity_key,
            kind=TaskKind.QUEUED,
            title=entry.title,
            reason=entry.reason,
            first_seen=entry.first_discovered_date,
            actions=(Action.APPROVE, Action.ARCHIVE),
            detail={
                "confidence": str(entry.confidence),
                "source": entry.source_name,
                "url": entry.announcement_url or entry.source_url,
                "summary": entry.summary[:300],
            },
        )
        for entry in queue.pending
    ]


def unclassified_tasks(pack: Pack) -> list[ReviewTask]:
    """Stored objects that no classification rule could place."""
    from ke.harvest import load_existing_objects

    tasks = []
    for obj, _ in load_existing_objects(pack):
        if not obj.needs_review:
            continue
        tasks.append(
            ReviewTask(
                key=str(obj.id),
                kind=TaskKind.UNCLASSIFIED,
                title=obj.title,
                reason=_why_unclassified(obj),
                first_seen=obj.discovered_date,
                feature_id=str(obj.id),
                actions=(Action.RESOLVE,),
                detail={
                    "tier": str(obj.tier),
                    "category": str(obj.category),
                    "source": obj.source_name,
                    "url": obj.announcement_url or obj.source_url,
                },
            )
        )
    return tasks


def _why_unclassified(obj: KnowledgeObject) -> str:
    missing = [name for name in ("category",) if getattr(obj, name) in (None, "")]
    if missing:
        return f"no rule matched: {', '.join(missing)} is unset"
    return "flagged for review"


def revision_tasks(pack: Pack) -> list[ReviewTask]:
    """Objects whose source changed in a way worth a human look.

    A revision is normally recorded and forgotten -- most are a corrected date or
    a reworded sentence. What lands here is the minority worth reading: a
    retitling, which can mean the source repurposed the announcement rather than
    editing it.
    """
    from ke.harvest import load_existing_objects

    tasks = []
    for obj, _ in load_existing_objects(pack):
        latest = obj.revisions[-1] if obj.revisions else None
        if latest is None or latest.revision == 1:
            continue
        if "title" not in latest.changed_fields:
            continue
        previous = obj.revisions[-2]
        tasks.append(
            ReviewTask(
                key=f"{obj.id}@{latest.revision}",
                kind=TaskKind.REVISION,
                title=obj.title,
                reason=latest.summary or "the source changed this item",
                first_seen=latest.date,
                feature_id=str(obj.id),
                actions=(Action.RESOLVE,),
                detail={
                    "revision": f"{previous.revision} → {latest.revision}",
                    "was": previous.title_snapshot or "(not recorded)",
                    "now": latest.title_snapshot or obj.title,
                    "changed": ", ".join(latest.changed_fields),
                },
            )
        )
    return tasks




def cross_pack_tasks(pack: Pack) -> list[ReviewTask]:
    """Duplicates this pack shares with another pack (M8, ADR-0044).

    Deliberately surfaced from **both** sides: reviewing either pack shows the
    same pair, because a duplicate is a fact about two packs and hiding it from
    one of them would make the queue depend on which pack you happened to open.

    The resolution is stored once, repo-level and keyed on the canonical pair,
    so acknowledging from either side clears it for both.
    """
    from ke.crosspack import outstanding
    from ke.pack import find_repo_root

    repo_root = pack.root.parent.parent
    if not (repo_root / "domain-packs").is_dir():  # pragma: no cover - defensive
        repo_root = find_repo_root()

    others = [p for p in Pack.discover(repo_root)]
    if len(others) < 2:
        return []

    tasks = []
    for pair in outstanding(others, repo_root):
        if not pair.involves(pack.name):
            continue
        counterpart = pair.other_side(pack.name)
        mine = next(s for s in pair.sides if s.pack_name == pack.name)
        tasks.append(
            ReviewTask(
                key=pair.short_key,
                kind=TaskKind.CROSS_PACK,
                title=mine.title,
                reason=(
                    f"also in {counterpart.pack_name} as {counterpart.feature_id} "
                    f"(matched on {pair.basis})"
                ),
                first_seen=None,
                feature_id=mine.feature_id,
                actions=(Action.RESOLVE,),
                detail={
                    "basis": pair.basis,
                    "this": f"{mine.pack_name}:{mine.feature_id}",
                    "other": f"{counterpart.pack_name}:{counterpart.feature_id}",
                    "url": mine.url,
                    "other_url": counterpart.url,
                    "other_title": counterpart.title,
                },
            )
        )
    return tasks


#: Every provider. Adding a kind is one entry here plus one function.
PROVIDERS: tuple[Callable[[Pack], list[ReviewTask]], ...] = (
    queued_tasks,
    unclassified_tasks,
    revision_tasks,
    cross_pack_tasks,
)


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------


def collect(pack: Pack, kinds: set[TaskKind] | None = None) -> list[ReviewTask]:
    """Every pending task, most urgent first, deterministically ordered."""
    tasks: list[ReviewTask] = []
    for provider in PROVIDERS:
        try:
            tasks.extend(provider(pack))
        except Exception:  # noqa: BLE001 - one broken provider must not hide the rest
            continue
    if kinds:
        tasks = [t for t in tasks if t.kind in kinds]
    return sorted(tasks, key=lambda t: (t.urgency, t.first_seen or date.min, t.key))


def find(pack: Pack, needle: str) -> ReviewTask:
    """Resolve a key the way a human typed it, or explain why it did not match."""
    wanted = needle.split(":", 1)[-1].strip().lower()
    matches = [
        task
        for task in collect(pack)
        if task.key.split(":", 1)[-1].lower().startswith(wanted)
        or task.key.lower() == needle.strip().lower()
    ]
    if not matches:
        raise KeyError(f"no review task matching {needle!r}")
    if len(matches) > 1:
        raise KeyError(
            f"{needle!r} matches {len(matches)} tasks; use more characters"
        )
    return matches[0]


def counts(pack: Pack, tasks: list[ReviewTask] | None = None) -> dict[TaskKind, int]:
    """Tally by kind, over `tasks` if the caller already has them.

    The parameter exists because counting is never the only thing a caller
    wants. `render_report` needs both the list and the tally, and calling
    `collect()` twice ran every provider twice -- including the cross-pack one,
    which reads every object in every pack. On a ten-pack repository that was
    half of a 147-second index rebuild, spent recomputing an identical answer.
    """
    tally = {kind: 0 for kind in TaskKind}
    for task in collect(pack) if tasks is None else tasks:
        tally[task.kind] += 1
    return tally


def apply_action(pack: Pack, task: ReviewTask, action: Action) -> str:
    """Carry out one decision. Returns a line describing what happened.

    Dispatches by kind because the *storage* differs even though the workflow
    does not -- a queued item is edited in the queue file, a flagged object in
    its own `metadata.yaml`.
    """
    if action not in task.actions:
        raise ValueError(
            f"{action} is not available for a {task.kind} task "
            f"(try: {', '.join(task.actions)})"
        )

    if task.kind is TaskKind.QUEUED:
        return _act_on_queued(pack, task, action)
    if task.kind is TaskKind.CROSS_PACK:
        return _act_on_cross_pack(pack, task)
    return _act_on_object(pack, task, action)


def _act_on_cross_pack(pack: Pack, task: ReviewTask) -> str:
    """Acknowledge a cross-pack duplicate. **Modifies neither object.**

    The whole point of this kind is that the engine does not choose. Resolving
    records that a human looked, so it stops being surfaced weekly; it does not
    merge, drop, supersede or rewrite anything. If the human decides one
    genuinely replaces the other, that is `ke supersede`, which is a separate
    and explicit act.
    """
    from ke.clock import SystemClock
    from ke.crosspack import Resolutions, find_duplicates

    repo_root = pack.root.parent.parent
    packs = Pack.discover(repo_root)
    pair = next((p for p in find_duplicates(packs) if p.short_key == task.key), None)
    if pair is None:
        raise KeyError(f"{task.key} is no longer a cross-pack duplicate")

    resolutions = Resolutions.load(repo_root)
    resolutions.acknowledge(pair, today=SystemClock().today())
    resolutions.save(repo_root)
    return (
        f"acknowledged: {pair.short_key}\n"
        "  both objects are unchanged and both are kept\n"
        "  it will not be surfaced again"
    )


def _act_on_queued(pack: Pack, task: ReviewTask, action: Action) -> str:
    from ke.review import ReviewQueue

    path = pack.state_dir / "review-queue.json"
    queue = ReviewQueue.load(path)
    entry = (
        queue.approve(task.key) if action is Action.APPROVE else queue.archive(task.key)
    )
    queue.save(path)
    if action is Action.APPROVE:
        return (
            f"approved: {entry.title}\n"
            f"  mints on the next harvest, dated {entry.first_discovered_date}"
        )
    return f"archived: {entry.title} (retained, never deleted)"


def _act_on_object(pack: Pack, task: ReviewTask, action: Action) -> str:
    """Clear `needs_review` on a stored object.

    Goes through `with_engine_fields`, so this cannot become a back door for
    editing anything the user owns.
    """
    from ke.store import load_object, update_object

    if not task.feature_id:
        raise ValueError("task has no Feature ID")

    from ke.ids import IdRegistry

    registry = IdRegistry.load(pack.registry_path, pack.id_prefix)
    subpath = registry.path_for(task.feature_id)
    if subpath is None:
        raise KeyError(f"{task.feature_id} is not in the registry")

    directory = pack.knowledge_dir / subpath
    obj = load_object(directory)
    if obj is None:
        raise KeyError(f"cannot read the object at {subpath}")

    resolved = obj.with_engine_fields(needs_review=False)
    latest = obj.revisions[-1] if obj.revisions else None
    update_object(
        directory,
        resolved,
        latest.summary_snapshot if latest else obj.title,
        max_summary_words=pack.max_summary_words,
    )
    return f"resolved: {task.feature_id} {obj.title[:56]}"


def render_report(pack: Pack) -> str:
    """The queue as Markdown, so it is visible in the GitHub UI.

    A backlog only gets worked if somebody can see it without running a command.
    """
    tasks = collect(pack)
    tally = counts(pack, tasks)
    lines = [
        f"# {pack.name} — review queue",
        "",
        "<!-- Generated by `ke review`. Rebuilt on every harvest. -->",
        "",
        f"**{len(tasks)} item(s) awaiting review.**",
        "",
        "| Kind | Count | Means |",
        "|---|---|---|",
        f"| queued | {tally[TaskKind.QUEUED]} | Discovered but not minted — identity not trusted enough |",
        f"| revision | {tally[TaskKind.REVISION]} | A stored item was retitled at source |",
        f"| cross-pack | {tally[TaskKind.CROSS_PACK]} | Two packs hold what may be the same feature |",
        f"| unclassified | {tally[TaskKind.UNCLASSIFIED]} | Minted, but no rule could categorise it |",
        "",
        "Work them with `ke review next`, or act directly:",
        "`ke review approve <key>` · `ke review archive <key>` · `ke review resolve <key>`",
        "",
    ]
    if not tasks:
        lines += ["_Nothing pending._", ""]
        return "\n".join(lines)

    lines += ["| Key | Kind | Title | First seen | Why |", "|---|---|---|---|---|"]
    for task in tasks:
        seen = task.first_seen.isoformat() if task.first_seen else "—"
        lines.append(
            f"| `{task.short_key}` | {task.kind} | {task.title[:60]} | {seen} "
            f"| {task.reason[:90]} |"
        )
    lines.append("")
    return "\n".join(lines)
