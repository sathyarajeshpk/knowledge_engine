"""The end-to-end pipeline: discover, dedupe, mint, store, index.

This is where every milestone so far meets. One pass:

    discover ─▶ dedupe ─▶ gate ─┬─▶ mint ─▶ store ─▶ index
                                └─▶ queue

Ordering that matters, and why:

1. **Dedupe before minting.** Minting is irreversible; deduplication is the last
   chance to notice we already have something.
2. **Gate after dedupe.** No point grading an item we are not going to store.
3. **Registry saved last, after objects are on disk.** If the run dies midway,
   an ID recorded with no object is a permanent hole; an object with no registry
   entry is repairable by rescanning. Fail in the recoverable direction.
4. **The run log is always appended**, even on a zero-item run. GitHub disables
   a scheduled workflow after 60 days without commit activity, so a quiet week
   must still produce a commit or the engine silently stops (ADR-0019).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from ke.acquisition import DiscoveryResult, discover_all
from ke.clock import Clock, SystemClock
from ke.dedupe import Decision, SeenIndex, Verdict, classify
from ke.ids import IdRegistry
from ke.indexer import write_indexes
from ke.models import KnowledgeObject, Lifecycle, RawItem
from ke.pack import Pack
from ke.review import ReviewQueue
from ke.store import build_object, with_reading_time, write_object


@dataclass
class HarvestReport:
    """What one harvest did. Everything the CLI and the digest need."""

    pack_name: str
    discovered: int = 0
    minted: list[str] = field(default_factory=list)
    queued: int = 0
    already_known: int = 0
    near_duplicates: int = 0
    written_paths: list[str] = field(default_factory=list)
    index_paths: list[str] = field(default_factory=list)
    review_items: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.minted or self.index_paths)

    def summary_line(self) -> str:
        return (
            f"{self.pack_name}: {self.discovered} discovered, "
            f"{len(self.minted)} minted, {self.queued} queued, "
            f"{self.already_known} already known, "
            f"{self.near_duplicates} near-duplicate(s)"
        )


def load_existing_objects(pack: Pack) -> list[tuple[KnowledgeObject, str]]:
    """Every stored object, with its path relative to the indexes directory.

    Read from disk rather than tracked in state: the repository is the source of
    truth (ADR-0002), so an index rebuild must reflect what is actually there —
    including anything a human added or edited by hand.
    """
    found: list[tuple[KnowledgeObject, str]] = []
    if not pack.knowledge_dir.exists():
        return found

    import yaml

    for metadata_path in sorted(pack.knowledge_dir.rglob("metadata.yaml")):
        try:
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            obj = KnowledgeObject.from_metadata_dict(raw)
        except Exception:  # noqa: BLE001 - a malformed object must not stop indexing
            continue
        relative = Path("..") / metadata_path.parent.relative_to(pack.root)
        found.append((obj, relative.as_posix()))
    return found


def harvest_pack(
    pack: Pack,
    *,
    clock: Clock | None = None,
    fetcher=None,
    dry_run: bool = False,
) -> HarvestReport:
    """Run the whole pipeline for one pack."""
    clock = clock or SystemClock()
    report = HarvestReport(pack_name=pack.name)

    prefix = pack.id_prefix
    if not prefix:
        report.errors.append("pack has no id_prefix; cannot mint")
        return report

    # --- discover ------------------------------------------------------
    result: DiscoveryResult = discover_all(
        pack.source_definitions,
        clock=clock,
        fetcher=fetcher,
        max_summary_words=pack.max_summary_words,
    )
    report.discovered = len(result.items)
    report.review_items = [f"{r.source_name}: {r.reason}" for r in result.review_items]

    # --- load state ----------------------------------------------------
    registry = IdRegistry.load(pack.registry_path, prefix)
    seen = SeenIndex.load(pack.seen_path)
    queue = ReviewQueue.load(pack.state_dir / "review-queue.json")

    # An item approved in a previous run rejoins this one carrying its original
    # discovery date, so time spent queued cannot move its Feature ID's month.
    approved_keys = {entry.identity_key for entry in queue.take_approved()}
    approved_dates = {
        entry.identity_key: entry.first_discovered_date
        for entry in queue.take_approved()
    }

    # --- dedupe --------------------------------------------------------
    decisions: list[Decision] = classify(
        result.items, seen, near_duplicate_threshold=pack.near_duplicate_jaccard
    )
    report.already_known = sum(
        1 for d in decisions if d.verdict in (Verdict.KNOWN_IDENTITY, Verdict.KNOWN_CONTENT)
    )
    report.near_duplicates = sum(1 for d in decisions if d.needs_review)

    # --- gate, mint, store ---------------------------------------------
    to_mint: list[tuple[RawItem, bool]] = []
    for decision in decisions:
        if not decision.is_new:
            continue
        item = decision.item
        was_approved = item.identity.key in approved_keys
        if was_approved:
            item = replace(
                item,
                lifecycle=Lifecycle.APPROVED,
                first_discovered_date=approved_dates[item.identity.key],
            )
        if item.mints_automatically or was_approved:
            to_mint.append((item, decision.needs_review))
        else:
            if queue.enqueue(item):
                report.queued += 1

    for item, needs_review in to_mint:
        try:
            feature_id = registry.mint(item)
            obj = with_reading_time(
                build_object(item, feature_id, needs_review=needs_review), item.summary
            )
            if not dry_run:
                directory = write_object(
                    pack.knowledge_dir,
                    obj,
                    item.summary,
                    max_summary_words=pack.max_summary_words,
                )
                report.written_paths.append(str(directory.relative_to(pack.root.parent.parent)))
            # `KnowledgeObject.knowledge_subpath` is the canonical form the
            # validator checks against. Computing the path independently here is
            # how the two drift apart.
            registry.record(feature_id, obj.knowledge_subpath)
            seen.remember(item, str(feature_id))
            queue.forget(item.identity.key)
            report.minted.append(str(feature_id))
        except Exception as exc:  # noqa: BLE001 - one bad item must not lose the rest
            report.errors.append(f"{item.title[:60]}: {type(exc).__name__}: {exc}")

    if dry_run:
        return report

    # --- persist state -------------------------------------------------
    # Objects are already on disk. Recording the registry after them means a
    # crash leaves a repairable inconsistency rather than a permanent hole.
    registry.save(pack.registry_path)
    seen.save(pack.seen_path)
    queue.save(pack.state_dir / "review-queue.json")

    # --- indexes -------------------------------------------------------
    report.index_paths = [
        str(p.relative_to(pack.root))
        for p in write_indexes(
            pack.indexes_dir, load_existing_objects(pack), queue.pending, pack.name
        )
    ]

    _append_run_log(pack, clock, report)
    return report


def _append_run_log(pack: Pack, clock: Clock, report: HarvestReport) -> None:
    """Append one line per run. **Always**, even when nothing was found.

    This is what keeps the weekly cron alive: GitHub disables a scheduled
    workflow after 60 days without commit activity, and a pack that harvests
    nothing for two quiet months would otherwise stop being harvested at all.
    """
    log_path = pack.state_dir / "run-log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text(
            "# Run log\n\nAppend-only. One line per harvest, including runs that "
            "found nothing.\n\n| Run | Discovered | Minted | Queued | Known |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"| {clock.run_id()} | {report.discovered} | {len(report.minted)} | "
            f"{report.queued} | {report.already_known} |\n"
        )
