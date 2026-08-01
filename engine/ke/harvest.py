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

__all__ = ["HarvestReport", "harvest_pack", "load_existing_objects"]


def load_existing_objects(pack: Pack) -> list[tuple[KnowledgeObject, str]]:
    """Every stored object, with its path relative to the indexes directory.

    Read from disk rather than tracked in state: the repository is the source of
    truth (ADR-0002), so an index rebuild reflects what is actually there --
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
