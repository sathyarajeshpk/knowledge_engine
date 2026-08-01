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
