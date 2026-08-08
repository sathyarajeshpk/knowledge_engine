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
    #: Off by default so tests and manual runs never send anything.
    notify: bool = False

    # Filled in by stages, in order.
    discovery: DiscoveryResult | None = None
    registry: IdRegistry | None = None
    seen: SeenIndex | None = None
    queue: ReviewQueue | None = None
    decisions: list[Decision] = field(default_factory=list)
    approved_dates: dict[str, object] = field(default_factory=dict)
    #: Objects created or updated this run, for stages that follow.
    touched: list[str] = field(default_factory=list)

    #: The digest, once `write_digest` has run. Notification reads it.
    digest: object | None = None

    #: Cross-pack duplicates for the whole repository, computed **once per run**
    #: by `harvest_all` after every pack has harvested (M9, TD-15/TD-16).
    #:
    #: `None` means "nobody computed this for me", and `rebuild_indexes` then
    #: falls back to computing it itself -- which is what a single-pack
    #: `harvest_pack()` does, and what every existing caller and test does.
    #: That fallback is why this change is additive rather than a rewrite.
    cross_pack: object | None = None

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


def classify_objects(ctx: HarvestContext) -> None:
    """Propose tier, priority, category, difficulty, workload and tags.

    **After minting and updating**, because it classifies what is on disk rather
    than what was discovered — so an object added by hand, or one whose rules
    have since been written, gets classified on the next run without needing to
    be re-discovered.

    Engine-*proposed* fields are written only when absent and never when locked
    by `overrides` (ADR-0008). That is what stops a rule tweak rewriting every
    object in the pack: classification lands once, and changing your mind later
    is a deliberate act rather than a side effect of editing `pack.yml`.

    A pack with **no** rules is not a reason to skip this stage. Returning early
    used to look like a harmless optimisation, but it meant every object in such
    a pack was stored with `category: None` and `needs_review: False` — silently
    unclassified, invisible in the review queue, and reported as a clean run.
    That is precisely the silent guess ADR-0010 forbids, and it would have hit
    the first new pack added with its `rules:` section not yet written. No rules
    means everything is unmatched, so everything gets flagged.
    """
    if ctx.dry_run:
        return
    rules = ctx.pack.classification_rules
    if not rules:
        # Said once, at run level. Flagging two hundred objects without naming
        # the cause tells the reader what, but never why.
        ctx.report.warnings.append(
            f"{ctx.pack.name}: no classification rules are defined in pack.yml, "
            "so every object will be flagged for review"
        )

    from ke.classify import applicable, propose, unmatched_fields
    from ke.harvest import load_existing_objects

    for obj, _ in load_existing_objects(ctx.pack):
        try:
            proposals = propose(obj, rules)
            updates = applicable(obj, proposals)
            missing = unmatched_fields(proposals)
            if missing and not obj.needs_review:
                # Never a silent guess: an object no rule could place is flagged
                # rather than given a plausible default (ADR-0010).
                updates["needs_review"] = True
            if not updates:
                continue

            classified = obj.with_engine_fields(**updates)
            directory = ctx.pack.knowledge_dir / obj.knowledge_subpath
            latest = obj.revisions[-1] if obj.revisions else None
            summary = latest.summary_snapshot if latest else obj.title
            if update_object(
                directory,
                classified,
                summary,
                max_summary_words=ctx.pack.max_summary_words,
            ):
                ctx.report.classified.append(
                    f"{obj.id} ({', '.join(sorted(updates))})"
                )
        except PermissionError as exc:
            ctx.report.errors.append(f"{obj.id}: classification violated ownership: {exc}")
        except Exception as exc:  # noqa: BLE001 - one object must not lose the rest
            ctx.report.errors.append(f"{obj.id}: {type(exc).__name__}: {exc}")


def persist_state(ctx: HarvestContext) -> None:
    """Write the registry, dedup index and queue.

    **After objects are on disk** (ADR-0031). A crash here leaves an ID gap,
    which is recoverable; the reverse order leaves an ID pointing at nothing,
    which is permanent.

    The failure mode is worth naming, because it is the one operational
    scenario in this engine that needs a human (M7 readiness review, O-1). If
    the registry cannot be written after objects were minted, the object exists
    on disk and the registry does not know about it. Nothing is lost and no
    Feature ID is duplicated -- `write_object` refuses to overwrite a minted
    object, which is what stops the next run reusing the number -- but the pack
    is internally inconsistent until somebody fixes it.

    `ke validate` reports it as `REG003`, and the weekly workflow validates
    before pushing, so the scheduled path never publishes this state: the runner
    is discarded and the next Sunday starts from the last good commit. A local
    `ke harvest` keeps the working tree, so there it needs a human.
    """
    if ctx.dry_run:
        return
    try:
        ctx.registry.save(ctx.pack.registry_path)
    except OSError as exc:
        # Re-raised with the recovery instruction attached. A bare
        # "No space left on device" tells an operator what happened but not
        # what it means or what to do about it, and this is the one failure
        # where those differ.
        raise OSError(
            f"could not write the ID registry ({exc}). "
            f"{len(ctx.report.minted)} object(s) were written this run and are "
            "NOT registered. Nothing is lost and no Feature ID was duplicated. "
            "Run `ke validate` to list them (REG003), free space or fix "
            "permissions, then re-run the harvest."
        ) from exc
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
            ctx.pack,
            cross_pack=ctx.cross_pack,
        )
    ]


def write_digest(ctx: HarvestContext) -> None:
    """Write the weekly digest, **even when nothing was found**.

    "No updates this week" and "the harvest did not run" must be
    distinguishable, and the only way to tell them apart is that one produced a
    file. On a cron, with nobody watching, that distinction is the whole point.
    """
    if ctx.dry_run:
        return
    from ke.digest import gather, write

    data = gather(ctx.pack, ctx.report, ctx.clock.run_id(), ctx.clock.today())
    ctx.digest = data
    ctx.report.digest_path = str(write(ctx.pack, data).relative_to(ctx.pack.root))


def append_run_log(ctx: HarvestContext) -> None:
    """Append one line, **always** — including on a run that found nothing.

    GitHub disables a scheduled workflow after 60 days without commit activity,
    so a quiet week must still produce a commit or the engine dies of silence.
    """
    if ctx.dry_run:
        return
    from ke.harvest import _append_run_log

    _append_run_log(ctx.pack, ctx.clock, ctx.report)


def send_notifications(ctx: HarvestContext) -> None:
    """Tell a human. **Last**, and unable to fail the run.

    Everything is already on disk by now, so a dead SMTP server or an expired
    token is an inconvenience rather than a lost harvest. `notify_all` never
    raises, and every failure message is redacted before it is recorded --
    an SMTP library will happily put the connection URL, password included, in
    an exception.
    """
    if ctx.dry_run or ctx.digest is None or not ctx.notify:
        return
    from ke.digest import render, subject
    from ke.notify import Notification, notify_all
    from ke.notify.github_issue import GitHubIssueNotifier
    from ke.notify.smtp_email import SmtpNotifier

    channels = [
        channel
        for channel in (
            GitHubIssueNotifier.from_environment(),
            SmtpNotifier.from_environment(),
        )
        if channel is not None
    ]
    if not channels:
        return

    delivered, failures = notify_all(
        channels,
        Notification(
            subject=subject(ctx.digest),
            body=render(ctx.digest),
            pack_name=ctx.pack.name,
        ),
    )
    ctx.report.notifications = delivered
    ctx.report.notification_failures = failures


#: The pipeline. Adding a stage is one entry here plus one function; the
#: ordering constraints are documented on the stages themselves.
#: Stages that read sources and write knowledge. Everything up to and including
#: `persist_state` touches **only this pack**.
HARVEST_STAGES: tuple[Stage, ...] = (
    discover,
    load_state,
    deduplicate,
    update_existing,
    gate_and_mint,
    classify_objects,
    persist_state,
)

#: Stages that publish what was harvested. `rebuild_indexes` is the first stage
#: that needs to know about **other packs**, because the review queue it writes
#: includes cross-pack duplicates.
#:
#: The split exists so a multi-pack run can harvest every pack first, compute
#: cross-pack duplicates once against the finished state, and only then publish
#: (M9, TD-15/TD-16). `STAGES` concatenates the two, so a single-pack
#: `harvest_pack()` behaves exactly as it did before.
PUBLISH_STAGES: tuple[Stage, ...] = (
    rebuild_indexes,
    write_digest,
    append_run_log,
    send_notifications,
)

STAGES: tuple[Stage, ...] = HARVEST_STAGES + PUBLISH_STAGES


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
