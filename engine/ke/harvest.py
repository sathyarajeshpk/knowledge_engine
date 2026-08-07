"""The harvest entry point.

The pipeline itself lives in `pipeline.py` as an ordered list of stages. This
module keeps the public surface -- `harvest_pack` and `load_existing_objects` --
so callers and tests are unaffected by how the stages are arranged.
"""

from __future__ import annotations

from pathlib import Path

from ke.clock import Clock, SystemClock
from ke.models import KnowledgeObject
from ke.pack import Pack
from ke.report import HarvestReport

__all__ = [
    "HarvestReport",
    "harvest_pack",
    "load_existing_objects",
    "load_objects_with_dirs",
]


def _iter_stored(pack: Pack):
    """Every readable object on disk, with the directory it lives in.

    One walk, two callers. A malformed object is skipped rather than raised on:
    one damaged `metadata.yaml` must not cost the other 221 (ADR-0032).
    """
    if not pack.knowledge_dir.exists():
        return

    import yaml

    for metadata_path in sorted(pack.knowledge_dir.rglob("metadata.yaml")):
        try:
            raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            obj = KnowledgeObject.from_metadata_dict(raw)
        except Exception:  # noqa: BLE001 - a malformed object must not stop indexing
            continue
        yield obj, metadata_path.parent


def load_existing_objects(pack: Pack) -> list[tuple[KnowledgeObject, str]]:
    """Every stored object, with its **link path relative to `indexes/`**.

    The second element is a Markdown link target, not a filesystem directory —
    it starts with `../` and is meant to be written into an index page. Anything
    that needs to *read or write* the object wants `load_objects_with_dirs`
    instead.

    The distinction has bitten once already: M7 passed one of these strings to
    `update_object` and got `TypeError: unsupported operand type(s) for /`.
    That was the lucky version of the mistake — a string that happened to be a
    valid relative path would have written the object somewhere else entirely.

    Read from disk rather than tracked in state: the repository is the source of
    truth (ADR-0002), so an index rebuild reflects what is actually there --
    including anything a human added or edited by hand.
    """
    return [
        (obj, (Path("..") / directory.relative_to(pack.root)).as_posix())
        for obj, directory in _iter_stored(pack)
    ]


def load_objects_with_dirs(pack: Pack) -> list[tuple[KnowledgeObject, Path]]:
    """Every stored object, with the directory it actually lives in.

    What you want for reading `feature.md`, writing an artifact, or handing to
    `update_object`. See `load_existing_objects` for the index-link variant.
    """
    return list(_iter_stored(pack))


def harvest_pack(
    pack: Pack,
    *,
    clock: Clock | None = None,
    fetcher=None,
    dry_run: bool = False,
    notify: bool = False,
) -> HarvestReport:
    """Run the pipeline for one pack.

    Thin on purpose: the stages and their ordering constraints live in
    `pipeline.STAGES`, which is where a new stage is added.
    """
    from ke.pipeline import HarvestContext, run_stages

    return run_stages(
        HarvestContext(
            pack=pack,
            clock=clock or SystemClock(),
            report=HarvestReport(pack_name=pack.name),
            fetcher=fetcher,
            dry_run=dry_run,
            notify=notify,
        )
    )


def harvest_all(
    packs: list[Pack],
    *,
    clock: Clock | None = None,
    fetcher=None,
    dry_run: bool = False,
    notify: bool = False,
    on_error=None,
) -> list[HarvestReport]:
    """Harvest every pack, scanning for cross-pack duplicates exactly once.

    ## Why this exists

    `harvest_pack` is self-contained: it harvests a pack and publishes it. Run
    in a loop that is correct for one pack and quadratic for many, because
    publishing needs to know about *other* packs -- the review queue it writes
    includes cross-pack duplicates, and finding those means reading every pack.
    Once per pack. An N-pack repository performed N full-repository scans
    (M8 performance review, P-1; measured at exactly `packs²` reads).

    So this runs the pipeline in two phases:

        for each pack:  HARVEST_STAGES    -- touches only that pack
        once:           scan for cross-pack duplicates
        for each pack:  PUBLISH_STAGES    -- given the scan, reads nobody

    ## The second thing this fixes

    Splitting the phases is not only cheaper, it is *more correct*. In the old
    loop, pack 1 published before packs 2..N had harvested, so its cross-pack
    list was computed against last week's version of every other pack -- every
    pack but the last reported a duplicate one run late (M8 readiness, O-3).
    Scanning between the phases means every pack sees the same finished state.

    ## Failure isolation is preserved

    One pack failing must not cost the others their run (M8 readiness, O-2). A
    pack that fails during harvest is dropped from the publish phase rather than
    half-published, and `on_error` is called so the caller can report it.
    """
    from ke.pipeline import HARVEST_STAGES, PUBLISH_STAGES, HarvestContext, run_stages

    clock = clock or SystemClock()
    contexts: list[HarvestContext] = []

    for pack in packs:
        ctx = HarvestContext(
            pack=pack,
            clock=clock,
            report=HarvestReport(pack_name=pack.name),
            fetcher=fetcher,
            dry_run=dry_run,
            notify=notify,
        )
        try:
            run_stages(ctx, HARVEST_STAGES)
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            if on_error is not None:
                on_error(pack, exc)
            continue
        contexts.append(ctx)

    # The one scan. Everything above has finished writing; nothing below writes
    # knowledge, so this sees the state every pack will be published against.
    cross_pack = _scan_cross_pack([c.pack for c in contexts], dry_run=dry_run)

    reports: list[HarvestReport] = []
    for ctx in contexts:
        ctx.cross_pack = cross_pack
        try:
            run_stages(ctx, PUBLISH_STAGES)
        except Exception as exc:  # noqa: BLE001
            if on_error is not None:
                on_error(ctx.pack, exc)
            continue
        reports.append(ctx.report)
    return reports


def _scan_cross_pack(packs: list[Pack], *, dry_run: bool) -> list | None:
    """Outstanding cross-pack duplicates, or `None` if there can be none.

    `None` rather than `[]` is deliberate and load-bearing: `[]` means "scanned,
    found nothing" and suppresses the fallback scan downstream, while `None`
    means "not computed" and lets `rebuild_indexes` behave exactly as it always
    has. With fewer than two packs there is nothing cross-pack by definition, so
    either would do -- but a dry run writes nothing and must not silently claim
    a clean scan.
    """
    if dry_run or len(packs) < 2:
        return None
    from ke.crosspack import outstanding

    repo_root = packs[0].root.parent.parent
    return outstanding(packs, repo_root)


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
            "found nothing.\n\n| Run | Discovered | Minted | Updated | Queued | Known |\n"
            "|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(
            f"| {clock.run_id()} | {report.discovered} | {len(report.minted)} | "
            f"{len(report.updated)} | {report.queued} | {report.already_known} |\n"
        )
