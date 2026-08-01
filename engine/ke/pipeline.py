"""The harvest, as a list of stages you can read in one screen.

`harvest_pack` grew a stage per milestone until it did seven things and no
longer fitted in one's head. This module keeps the *shape* of the pipeline
visible and makes inserting a stage a one-line change rather than a surgery.

    STAGES = (discover, load_state, deduplicate, update_existing,
              gate_and_mint, classify_objects, persist_state, rebuild_indexes,
              append_run_log)

Two rules hold the design together:

**A stage takes the context and mutates it.** No stage returns a new context, so
there is no chance of one being dropped by a caller that forgets to reassign.

**Order is a safety property, not a style choice** (ADR-0031). The list above is
the contract; a stage inserted in the wrong place can mint before deduplicating
or save the registry before the objects it points at exist. The reasons live on
each stage rather than in a comment far from the code.

The context is deliberately a plain mutable dataclass rather than anything
cleverer: a stage should be readable by someone who has never seen this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

from ke.acquisition import DiscoveryResult, discover_all
from ke.clock import Clock
from ke.dedupe import Decision, SeenIndex, Verdict
from ke.dedupe import classify as classify_duplicates
from ke.ids import IdRegistry
from ke.indexer import write_indexes
from ke.models import Lifecycle, RawItem
from ke.pack import Pack
from ke.report import HarvestReport
from ke.review import ReviewQueue
from ke.revisions import apply_update, detect_changes
from ke.store import (
    build_object,
    load_object,
    update_object,
    with_reading_time,
    write_object,
)


@dataclass
class HarvestContext:
    """Everything a stage may read or write. Passed down the whole pipeline."""

    pack: Pack
    clock: Clock
    report: HarvestReport
    fetcher: object | None = None
    dry_run: bool = False

    # Filled in by stages, in order.
    discovery: DiscoveryResult | None = None
    registry: IdRegistry | None = None
    seen: SeenIndex | None = None
    queue: ReviewQueue | None = None
    decisions: list[Decision] = field(default_factory=list)
    approved_dates: dict[str, object] = field(default_factory=dict)
    #: Objects created or updated this run, for stages that follow.
    touched: list[str] = field(default_factory=list)

    #: Set by a stage to end the run early without treating it as a failure.
    stop: bool = False

    @property
    def queue_path(self):
        return self.pack.state_dir / "review-queue.json"


Stage = Callable[[HarvestContext], None]


# ---------------------------------------------------------------------------
# Stages, in pipeline order
# ---------------------------------------------------------------------------


def discover(ctx: HarvestContext) -> None:
    """Fetch from every configured source and grade what comes back."""
    if not ctx.pack.id_prefix:
        ctx.report.errors.append("pack has no id_prefix; cannot mint")
        ctx.stop = True
        return

    ctx.discovery = discover_all(
        ctx.pack.source_definitions,
        clock=ctx.clock,
        fetcher=ctx.fetcher,
        max_summary_words=ctx.pack.max_summary_words,
    )
    ctx.report.discovered = len(ctx.discovery.items)
    ctx.report.review_items = [
        f"{r.source_name}: {r.reason}" for r in ctx.discovery.review_items
    ]


def load_state(ctx: HarvestContext) -> None:
    """Read the registry, the dedup index and the review queue.

    Failure policies differ per file and are deliberate (ADR-0032): a damaged
    dedup index degrades, a damaged registry or queue raises.
    """
    pack = ctx.pack
    ctx.registry = IdRegistry.load(pack.registry_path, pack.id_prefix)
    ctx.seen = SeenIndex.load(pack.seen_path)
    ctx.queue = ReviewQueue.load(ctx.queue_path)

    # An item approved in a previous run rejoins this one carrying its ORIGINAL
    # discovery date, so time spent waiting for a human cannot move its Feature
    # ID's month (ADR-0028).
    ctx.approved_dates = {
        entry.identity_key: entry.first_discovered_date
        for entry in ctx.queue.take_approved()
    }


def deduplicate(ctx: HarvestContext) -> None:
    """Decide which items are new, known, or possible duplicates."""
    ctx.decisions = classify_duplicates(
        ctx.discovery.items,
        ctx.seen,
        near_duplicate_threshold=ctx.pack.near_duplicate_jaccard,
    )
    ctx.report.already_known = sum(
        1
        for d in ctx.decisions
        if d.verdict in (Verdict.KNOWN_IDENTITY, Verdict.KNOWN_CONTENT)
    )
    ctx.report.near_duplicates = sum(1 for d in ctx.decisions if d.needs_review)


def update_existing(ctx: HarvestContext) -> None:
    """Refresh objects we already store.

    **Runs before minting**, so an item we already have can never reach the
    minting path. That ordering makes the guarantee structural rather than a
    matter of reading the dedupe verdict correctly.

    One sighting per identity: the same feature is legitimately listed by two
    sources with different metadata, and letting both update made the object
    flip between their renderings twice per harvest.
    """
    handled: set[str] = set()
    for decision in ctx.decisions:
        if decision.is_new or not decision.matched:
            continue
        key = decision.item.identity.key
        if key in handled:
            ctx.report.unchanged += 1
            continue
        handled.add(key)
        _update_one(ctx, decision)


def gate_and_mint(ctx: HarvestContext) -> None:
    """Mint permanent Feature IDs for items that clear the gate; queue the rest."""
    to_mint: list[tuple[RawItem, bool]] = []
    for decision in ctx.decisions:
        if not decision.is_new:
            continue
        item = decision.item
        was_approved = item.identity.key in ctx.approved_dates
        if was_approved:
            item = replace(
                item,
                lifecycle=Lifecycle.APPROVED,
                first_discovered_date=ctx.approved_dates[item.identity.key],
            )
        if item.mints_automatically or was_approved:
            to_mint.append((item, decision.needs_review))
        elif ctx.queue.enqueue(item):
            ctx.report.queued += 1

    for item, needs_review in to_mint:
        _mint_one(ctx, item, needs_review)


def persist_state(ctx: HarvestContext) -> None:
    """Write the registry, dedup index and queue.

    **After objects are on disk** (ADR-0031). A crash here leaves an ID gap,
    which is recoverable; the reverse order leaves an ID pointing at nothing,
    which is permanent.
    """
    if ctx.dry_run:
        return
    ctx.registry.save(ctx.pack.registry_path)
    ctx.seen.save(ctx.pack.seen_path)
    ctx.queue.save(ctx.queue_path)


def rebuild_indexes(ctx: HarvestContext) -> None:
    """Rebuild every index from what is actually stored, never from this run."""
    if ctx.dry_run:
        return
    from ke.harvest import load_existing_objects

    ctx.report.index_paths = [
        str(p.relative_to(ctx.pack.root))
        for p in write_indexes(
            ctx.pack.indexes_dir,
            load_existing_objects(ctx.pack),
            ctx.queue.pending,
            ctx.pack.name,
        )
    ]


def append_run_log(ctx: HarvestContext) -> None:
    """Append one line, **always** — including on a run that found nothing.

    GitHub disables a scheduled workflow after 60 days without commit activity,
    so a quiet week must still produce a commit or the engine dies of silence.
    """
    if ctx.dry_run:
        return
    from ke.harvest import _append_run_log

    _append_run_log(ctx.pack, ctx.clock, ctx.report)


#: The pipeline. Adding a stage is one entry here plus one function; the
#: ordering constraints are documented on the stages themselves.
STAGES: tuple[Stage, ...] = (
    discover,
    load_state,
    deduplicate,
    update_existing,
    gate_and_mint,
    persist_state,
    rebuild_indexes,
    append_run_log,
)


def run_stages(ctx: HarvestContext, stages: tuple[Stage, ...] = STAGES) -> HarvestReport:
    """Run each stage in order, stopping early if one asks to.

    A stage raising is a bug rather than an expected failure -- per-item errors
    are caught inside the stages that own them -- so it is recorded against the
    report and the run ends rather than continuing on a half-built context.
    """
    for stage in stages:
        if ctx.stop:
            break
        try:
            stage(ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.report.errors.append(
                f"stage {stage.__name__} failed: {type(exc).__name__}: {exc}"
            )
            ctx.stop = True
    return ctx.report


# ---------------------------------------------------------------------------
# Per-item work, kept out of the stage bodies so the stages stay readable
# ---------------------------------------------------------------------------


def _update_one(ctx: HarvestContext, decision: Decision) -> None:
    """Refresh one stored object if this sighting actually changed it."""
    feature_id = decision.matched
    if not feature_id or feature_id == "pending":
        return  # matched something minted earlier in this same run

    subpath = ctx.registry.path_for(feature_id)
    if subpath is None:
        return  # not registered; `ke validate` reports it

    directory = ctx.pack.knowledge_dir / subpath
    existing = load_object(directory)
    if existing is None:
        return  # unreadable; validation reports it, the harvest keeps going

    changes = detect_changes(existing, decision.item)
    if not changes:
        ctx.report.unchanged += 1
        return

    try:
        updated = with_reading_time(
            apply_update(
                existing,
                decision.item,
                changes,
                today=ctx.clock.today(),
                run_id=ctx.clock.run_id(),
            ),
            decision.item.summary,
        )
        wrote = ctx.dry_run or update_object(
            directory,
            updated,
            decision.item.summary,
            max_summary_words=ctx.pack.max_summary_words,
        )
        if wrote:
            ctx.report.updated.append(f"{feature_id} ({', '.join(sorted(changes))})")
            ctx.touched.append(feature_id)
    except PermissionError as exc:
        # The ownership model refused a write. That is the guard working, and it
        # must be loud rather than swallowed.
        ctx.report.errors.append(f"{feature_id}: ownership violation: {exc}")
    except Exception as exc:  # noqa: BLE001 - one object must not lose the rest
        ctx.report.errors.append(f"{feature_id}: {type(exc).__name__}: {exc}")


def _mint_one(ctx: HarvestContext, item: RawItem, needs_review: bool) -> None:
    """Mint a permanent Feature ID and write the object it names."""
    try:
        feature_id = ctx.registry.mint(item)
        obj = with_reading_time(
            build_object(item, feature_id, needs_review=needs_review), item.summary
        )
        if not ctx.dry_run:
            directory = write_object(
                ctx.pack.knowledge_dir,
                obj,
                item.summary,
                max_summary_words=ctx.pack.max_summary_words,
            )
            ctx.report.written_paths.append(str(directory))
        # `knowledge_subpath` is the canonical form the validator checks.
        # Computing it independently is how the two drifted apart in M2.
        ctx.registry.record(feature_id, obj.knowledge_subpath)
        ctx.seen.remember(item, str(feature_id))
        ctx.queue.forget(item.identity.key)
        ctx.report.minted.append(str(feature_id))
        ctx.touched.append(str(feature_id))
    except Exception as exc:  # noqa: BLE001 - one bad item must not lose the rest
        ctx.report.errors.append(f"{item.title[:60]}: {type(exc).__name__}: {exc}")
