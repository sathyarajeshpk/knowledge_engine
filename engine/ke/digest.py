"""The weekly digest: what arrived, what changed, what needs you.

M6 is the first milestone where the engine runs **unattended**. Everything
before this was executed by hand with somebody watching the output. On a cron,
nobody is watching -- so the digest is not a nicety, it is the only thing that
makes a silent system observable.

Three rules it follows:

1. **A digest is written even when nothing was found.** "No updates this week"
   and "the harvest did not run" must be distinguishable, and the only way to
   tell them apart is that one produced a file.
2. **The review backlog is always in it.** A unified queue that nobody looks at
   is just a better-organised place to be ignored, so the count goes at the top
   where it cannot be missed.
3. **Failures are reported before successes.** A digest that leads with "12 new
   items" and buries a dead source four sections down has told you the wrong
   thing first.

No AI (ADR-0004). Everything here is counting and formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ke.models import Tier
from ke.pack import Pack
from ke.report import HarvestReport


def iso_week(day: date) -> str:
    """`2026-W31`. The digest filename and its identity."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class DigestData:
    """Everything a digest reports, gathered once."""

    pack_name: str
    week: str
    run_id: str
    report: HarvestReport
    new_objects: list[tuple[str, str, str]]      # (id, title, tier)
    review_total: int
    review_breakdown: dict[str, int]
    unhealthy_sources: list[str]

    @property
    def has_problems(self) -> bool:
        return bool(self.report.errors or self.report.review_items or self.unhealthy_sources)


def gather(pack: Pack, report: HarvestReport, run_id: str, today: date) -> DigestData:
    """Collect what the digest needs, from the pack as it now stands."""
    from ke.harvest import load_existing_objects
    from ke.reviewq import counts

    minted = set(report.minted)
    new_objects = [
        (str(obj.id), obj.title, str(obj.tier))
        for obj, _ in load_existing_objects(pack)
        if str(obj.id) in minted
    ]
    new_objects.sort(key=lambda row: (row[2], row[0]))

    tally = counts(pack)
    return DigestData(
        pack_name=pack.name,
        week=iso_week(today),
        run_id=run_id,
        report=report,
        new_objects=new_objects,
        review_total=sum(tally.values()),
        review_breakdown={str(k): v for k, v in tally.items() if v},
        unhealthy_sources=[m.split(":")[0] for m in report.review_items],
    )


def render(data: DigestData) -> str:
    """The digest, as Markdown."""
    report = data.report
    lines = [
        f"# {data.pack_name} — {data.week}",
        "",
        f"`{data.run_id}`",
        "",
    ]

    # Problems first. A digest that buries a dead source under a headline count
    # has told you the wrong thing first.
    if data.unhealthy_sources:
        lines += [
            "## ⚠️ Sources that could not be read",
            "",
            "Not the same as a quiet week — these produced nothing because they "
            "failed, and knowledge from them is missing rather than absent.",
            "",
        ]
        lines += [f"- **{name}**" for name in sorted(set(data.unhealthy_sources))]
        lines.append("")

    if report.errors:
        lines += ["## ⚠️ Errors", ""]
        lines += [f"- {message}" for message in report.errors[:20]]
        if len(report.errors) > 20:
            lines.append(f"- … and {len(report.errors) - 20} more")
        lines.append("")

    lines += [
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| Discovered | {report.discovered} |",
        f"| **New knowledge objects** | **{len(report.minted)}** |",
        f"| Updated | {len(report.updated)} |",
        f"| Unchanged | {report.unchanged} |",
        f"| Newly queued | {report.queued} |",
        f"| Classified | {len(report.classified)} |",
        "",
    ]

    # The backlog goes high, not buried. See rule 2.
    if data.review_total:
        breakdown = " · ".join(f"{k}: {v}" for k, v in sorted(data.review_breakdown.items()))
        lines += [
            f"## 📋 {data.review_total} item(s) awaiting review",
            "",
            f"{breakdown}",
            "",
            "Work them with `ke review next`. See `indexes/review-queue.md`.",
            "",
        ]

    if data.new_objects:
        lines += ["## New this week", "", "| ID | Tier | Title |", "|---|---|---|"]
        lines += [
            f"| {oid} | {tier} | {title[:70]} |" for oid, title, tier in data.new_objects
        ]
        lines.append("")
    elif not report.errors and not data.unhealthy_sources:
        lines += [
            "## New this week",
            "",
            "_Nothing new. Every source was read successfully and reported no "
            "updates the engine had not already stored._",
            "",
        ]

    if report.updated:
        lines += ["## Updated", ""]
        lines += [f"- {entry}" for entry in report.updated[:25]]
        if len(report.updated) > 25:
            lines.append(f"- … and {len(report.updated) - 25} more")
        lines.append("")

    return "\n".join(lines)


def write(pack: Pack, data: DigestData) -> "object":
    """Write the digest for its ISO week. Returns the path.

    One file per week, overwritten if the harvest runs twice in the same week —
    the digest describes the week, not the run, and two files for one week would
    be two answers to the same question.
    """
    pack.digests_dir.mkdir(parents=True, exist_ok=True)
    path = pack.digests_dir / f"{data.week}.md"
    path.write_text(render(data), encoding="utf-8")
    return path


def subject(data: DigestData) -> str:
    """A one-line summary for a notification title.

    Leads with the problem when there is one, because a subject line is often
    all that gets read.
    """
    if data.unhealthy_sources or data.report.errors:
        return f"[{data.pack_name}] {data.week}: needs attention"
    if data.report.minted:
        return f"[{data.pack_name}] {data.week}: {len(data.report.minted)} new"
    return f"[{data.pack_name}] {data.week}: no new knowledge"
