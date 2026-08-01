"""Artifact coverage: what exists, what was asked for, what has gone stale.

A knowledge object is only half the value. The other half is what you made from
it — the tutorial you wrote, the quiz you took, the post you published. This
module answers the three questions that go with that:

1. **What have I actually produced?** Coverage across the pack, by type.
2. **What did I say I would produce and never did?** The `requested` backlog,
   which is where good intentions accumulate silently.
3. **What have I produced that the source has since changed underneath?** The
   stale set — the only one of the three that can quietly become wrong.

## Staleness is computed, never stored as an opinion

An artifact is stale exactly when `generated_from_revision` is behind the
object's current revision. That comparison is the definition, and every reader
derives it independently — `retrieve`, `ke status`, the digest, the index.

`ke status --refresh` writes the derived state into `metadata.yaml` so a reader
browsing the repository on GitHub sees it too. It changes nothing about what is
true; it only makes the truth visible without running a command. Nothing is ever
regenerated and nothing is ever deleted: the engine detects and reports, a human
decides (CLAUDE.md, ADR-0004).

## Why this is a separate module from `generate` and `attach`

`generate` builds one pack. `attach` stores one answer. This reads the whole
pack and reports. Keeping the pack-wide read here means neither of those has to
know how to walk the knowledge directory, and the weekly digest can import this
without dragging in prompt-template loading.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ke.models import ArtifactType, GenerationStatus, KnowledgeObject
from ke.pack import Pack


@dataclass(frozen=True)
class ArtifactRow:
    """One artifact on one object."""

    feature_id: str
    title: str
    artifact_type: ArtifactType
    status: GenerationStatus
    path: str | None
    generated_from_revision: int | None
    current_revision: int
    model: str | None
    prompt_version: int | None

    @property
    def is_stale(self) -> bool:
        return (
            self.status in (GenerationStatus.GENERATED, GenerationStatus.STALE)
            and self.generated_from_revision is not None
            and self.generated_from_revision < self.current_revision
        )

    @property
    def revisions_behind(self) -> int:
        if not self.is_stale or self.generated_from_revision is None:
            return 0
        return self.current_revision - self.generated_from_revision


@dataclass
class Coverage:
    """Artifact state across a whole pack."""

    pack_name: str
    object_count: int
    rows: list[ArtifactRow] = field(default_factory=list)

    @classmethod
    def of(cls, pack: Pack) -> Coverage:
        from ke.harvest import load_existing_objects

        rows: list[ArtifactRow] = []
        objects = load_existing_objects(pack)
        for obj, _ in objects:
            rows.extend(_rows_for(obj))
        # Sorted so two runs over an unchanged pack print identical output
        # (ADR-0022) -- `ke status` output ends up pasted into notes.
        rows.sort(key=lambda r: (r.feature_id, str(r.artifact_type)))
        return cls(pack_name=pack.name, object_count=len(objects), rows=rows)

    # -- slices ----------------------------------------------------------

    @property
    def generated(self) -> list[ArtifactRow]:
        return [r for r in self.rows if r.status is GenerationStatus.GENERATED]

    @property
    def requested(self) -> list[ArtifactRow]:
        return [r for r in self.rows if r.status is GenerationStatus.REQUESTED]

    @property
    def stale(self) -> list[ArtifactRow]:
        return [r for r in self.rows if r.is_stale]

    @property
    def by_type(self) -> dict[ArtifactType, Counter]:
        """Per artifact type, a count of each status."""
        tally: dict[ArtifactType, Counter] = {t: Counter() for t in ArtifactType}
        for row in self.rows:
            tally[row.artifact_type][row.status] += 1
        return tally

    @property
    def objects_with_any_artifact(self) -> int:
        return len({
            r.feature_id
            for r in self.rows
            if r.status is not GenerationStatus.NONE
        })

    @property
    def has_anything_to_report(self) -> bool:
        return bool(self.generated or self.requested or self.stale)


def _rows_for(obj: KnowledgeObject) -> list[ArtifactRow]:
    """Every tracked artifact on one object.

    Entries at `none` are skipped: the absence of an artifact is not an artifact,
    and including them would put 222 × 7 empty rows into every report.
    """
    return [
        ArtifactRow(
            feature_id=str(obj.id),
            title=obj.title,
            artifact_type=artifact_type,
            status=entry.status,
            path=entry.path,
            generated_from_revision=entry.generated_from_revision,
            current_revision=obj.current_revision,
            model=entry.model,
            prompt_version=entry.prompt_version,
        )
        for artifact_type, entry in sorted(obj.generation.items())
        if entry.status is not GenerationStatus.NONE
    ]


def refresh_pack(pack: Pack) -> int:
    """Write computed staleness into every object that needs it.

    Returns how many objects changed. Idempotent by construction: the second run
    finds nothing to do because `refresh_staleness` compares before writing.
    """
    from ke.attach import refresh_staleness
    from ke.harvest import load_objects_with_dirs

    # `load_objects_with_dirs`, not `load_existing_objects`: the latter returns
    # an index-relative *link* string, and writing through it would put the
    # object somewhere other than where it lives.
    changed = 0
    for obj, directory in load_objects_with_dirs(pack):
        if refresh_staleness(pack, obj, directory):
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _percent(part: int, whole: int) -> str:
    """` (12%)`, or nothing at all when the figure would mislead.

    1 of 222 rounds to 0%, and "1 object has an artifact (0%)" reads as a
    contradiction. Below one percent the fraction is the honest number and the
    percentage adds nothing.
    """
    if not whole or part * 100 < whole:
        return ""
    return f" ({part * 100 // whole}%)"


def render_status(
    coverage: Coverage, *, stale_only: bool = False, requested_only: bool = False
) -> str:
    """`ke status` output, for a terminal."""
    if stale_only:
        return _render_rows("Stale artifacts", coverage.stale, show_behind=True)
    if requested_only:
        return _render_rows("Requested, not yet generated", coverage.requested)

    lines = [
        "",
        f"{coverage.pack_name}: {coverage.object_count} knowledge object(s)",
        "",
    ]

    if not coverage.has_anything_to_report:
        lines += [
            "  No artifacts yet.",
            "",
            "  Every object is knowledge you have stored but not yet turned into",
            "  anything. Start with:",
            "",
            "    ke search --tier 1 --learning-status not-started --limit 5",
            "    ke generate tutorial --id <the one you pick>",
            "",
        ]
        return "\n".join(lines)

    covered = coverage.objects_with_any_artifact
    lines += [
        f"  {covered} of {coverage.object_count} object(s) have at least one "
        f"artifact{_percent(covered, coverage.object_count)}",
        "",
        "  Type                        generated  requested  stale",
        "  " + "-" * 58,
    ]
    tally = coverage.by_type
    stale_per_type = Counter(r.artifact_type for r in coverage.stale)
    for artifact_type in ArtifactType:
        counts = tally[artifact_type]
        generated = counts[GenerationStatus.GENERATED]
        requested = counts[GenerationStatus.REQUESTED]
        stale = stale_per_type[artifact_type]
        if not (generated or requested or stale):
            continue
        lines.append(
            f"  {str(artifact_type):<26} {generated:>9}  {requested:>9}  {stale:>5}"
        )
    lines.append("")

    if coverage.stale:
        lines += [
            f"  ⚠️  {len(coverage.stale)} artifact(s) are stale — the source changed "
            "after they were made",
            "      ke status --stale        to list them",
            "      ke generate <type> --id <id> --force   to remake one",
            "",
        ]
    if coverage.requested:
        lines += [
            f"  📋 {len(coverage.requested)} artifact(s) requested and not yet made",
            "      ke status --requested   to list them",
            "",
        ]
    return "\n".join(lines)


def _render_rows(heading: str, rows: list[ArtifactRow], *, show_behind: bool = False) -> str:
    if not rows:
        return f"\n{heading}: none.\n"
    lines = ["", heading, ""]
    for row in rows:
        detail = ""
        if show_behind:
            detail = (
                f"  (r{row.generated_from_revision} → r{row.current_revision}, "
                f"{row.revisions_behind} behind)"
            )
        lines.append(
            f"  {row.feature_id}  {str(row.artifact_type):<26}"
            f"{row.title[:40]}{detail}"
        )
    lines.append("")
    return "\n".join(lines)


def render_index(coverage: Coverage) -> str:
    """`indexes/generation-status.md` — the same picture, for the GitHub UI.

    Markdown rather than the terminal layout, because this one is read in a
    browser by somebody who has not run a command and may not intend to.
    """
    lines = [
        f"# {coverage.pack_name} — artifact coverage",
        "",
        "<!-- Generated by `ke index`. Rebuilt on every harvest. -->",
        "",
    ]

    if not coverage.has_anything_to_report:
        lines += [
            f"**No artifacts yet**, across {coverage.object_count} knowledge object(s).",
            "",
            "Artifacts are produced on demand and never by the scheduled run "
            "(ADR-0004). See `ke generate list`.",
            "",
        ]
        return "\n".join(lines)

    covered = coverage.objects_with_any_artifact
    lines += [
        f"**{covered} of {coverage.object_count}** object(s) have at least one "
        f"artifact{_percent(covered, coverage.object_count)}.",
        "",
        "| Type | Generated | Requested | Stale |",
        "|---|---|---|---|",
    ]
    tally = coverage.by_type
    stale_per_type = Counter(r.artifact_type for r in coverage.stale)
    for artifact_type in ArtifactType:
        counts = tally[artifact_type]
        generated = counts[GenerationStatus.GENERATED]
        requested = counts[GenerationStatus.REQUESTED]
        stale = stale_per_type[artifact_type]
        if not (generated or requested or stale):
            continue
        lines.append(
            f"| {artifact_type} | {generated} | {requested} | {stale} |"
        )
    lines.append("")

    if coverage.stale:
        lines += [
            "## ⚠️ Stale",
            "",
            "The knowledge object was revised after these were made. They are "
            "**never deleted** — regenerate when you are ready.",
            "",
            "| ID | Type | Made from | Now at |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r.feature_id} | {r.artifact_type} | r{r.generated_from_revision} "
            f"| r{r.current_revision} |"
            for r in coverage.stale
        ]
        lines.append("")

    if coverage.requested:
        lines += [
            "## 📋 Requested",
            "",
            "| ID | Type | Title |",
            "|---|---|---|",
        ]
        lines += [
            f"| {r.feature_id} | {r.artifact_type} | {r.title[:60]} |"
            for r in coverage.requested
        ]
        lines.append("")

    return "\n".join(lines)
