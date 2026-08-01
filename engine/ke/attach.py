"""Storing what a model produced, and recording where it came from.

The other half of `ke generate`. The pack goes out, you paste it into a model,
and the answer comes back here.

## The ownership split, at its sharpest

This module writes two things into the same object directory, and they belong to
different people:

* **The artifact file** — `artifacts/tutorial.md` and its siblings. **Yours.**
  Written once by this command and never touched again by anything automated.
  Edit it, rewrite it, delete half of it; the engine will not care and will not
  notice (ADR-0008).
* **The `generation` block** in `metadata.yaml` — status, path, date, source
  revision, model, prompt version. **The engine's.** It is bookkeeping *about*
  the artifact, not the artifact, which is why staleness can be computed without
  anything ever reading your prose.

That split is what makes `generated_from_revision` trustworthy. The engine knows
which revision an artifact was built from because it recorded it at the moment
of attachment, not because it inferred it later from a file it does not
understand.

## Why the model name is recorded and never read

`model` is provenance. Nothing in the engine branches on it, no behaviour
depends on it, and a pack full of artifacts from four different vendors works
exactly as well as one from a single vendor. It exists so that when an artifact
reads oddly in six months you can see what produced it.

Recording it is not a dependency. Reading it would be.

## Overwriting

Attaching over an existing artifact **replaces the file**. This is the one place
the engine will overwrite user-owned content, it only happens because a human
typed the command, and it refuses without `--force` when the existing artifact
is current. Regenerating something stale is the normal path; silently discarding
an artifact you have since edited is not.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ke.generate import Template
from ke.models import GenerationEntry, GenerationStatus, KnowledgeObject
from ke.pack import Pack


class AttachError(Exception):
    """The artifact could not be stored."""


def read_content(source: str) -> str:
    """Read the model's answer from a file, or from stdin when given `-`.

    Stdin matters more than it looks: it is what makes
    `pbpaste | ke generate ... --attach -` a single step, and a two-step
    workflow that requires saving a temporary file first is a workflow people
    stop using.
    """
    import sys

    text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    if not text.strip():
        raise AttachError(
            "the artifact is empty; nothing was attached "
            "(did the paste land, or was the file still open?)"
        )
    return text


def attach(
    pack: Pack,
    obj: KnowledgeObject,
    directory: Path,
    template: Template,
    content: str,
    *,
    today: date,
    model: str | None = None,
    force: bool = False,
) -> tuple[KnowledgeObject, Path]:
    """Write an artifact and record it. Returns the updated object and the path.

    The order is deliberate: the file lands first, then the metadata that claims
    it exists. A `generation` block pointing at a file that was never written is
    a lie the engine would then repeat in every index and digest; a file with no
    metadata entry is merely untracked, and the next attach fixes it.
    """
    from ke.store import update_object

    existing = obj.generation.get(template.artifact_type)
    if (
        existing is not None
        and existing.status is GenerationStatus.GENERATED
        and not existing.is_stale_against(obj.current_revision)
        and not force
    ):
        raise AttachError(
            f"{obj.id} already has a current {template.artifact_type} "
            f"at {existing.path}. Use --force to replace it. "
            "(Artifacts are yours; the engine will not discard one silently.)"
        )

    target = Path(directory) / template.output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_ensure_trailing_newline(content), encoding="utf-8")

    updated = obj.with_engine_fields(
        generation={
            **obj.generation,
            template.artifact_type: GenerationEntry(
                status=GenerationStatus.GENERATED,
                path=template.output,
                generated_at=today,
                generated_from_revision=obj.current_revision,
                model=model,
                prompt_version=template.prompt_version,
            ),
        }
    )

    latest = obj.revisions[-1] if obj.revisions else None
    update_object(
        Path(directory),
        updated,
        (latest.summary_snapshot if latest else "") or obj.title,
        max_summary_words=pack.max_summary_words,
    )
    return updated, target


def request(
    pack: Pack,
    obj: KnowledgeObject,
    directory: Path,
    template: Template,
) -> KnowledgeObject:
    """Mark an artifact as wanted without generating it.

    The gap between "I should write a tutorial for this" and actually doing it
    is where intentions go to die. Recording the intention makes it show up in
    `ke status` and the weekly digest, which is the only mechanism this system
    has for making a backlog visible.
    """
    from ke.store import update_object

    existing = obj.generation.get(template.artifact_type)
    if existing is not None and existing.status is not GenerationStatus.NONE:
        raise AttachError(
            f"{obj.id} already records {template.artifact_type} "
            f"as {existing.status}"
        )

    updated = obj.with_engine_fields(
        generation={
            **obj.generation,
            template.artifact_type: GenerationEntry(status=GenerationStatus.REQUESTED),
        }
    )
    latest = obj.revisions[-1] if obj.revisions else None
    update_object(
        Path(directory),
        updated,
        (latest.summary_snapshot if latest else "") or obj.title,
        max_summary_words=pack.max_summary_words,
    )
    return updated


def refresh_staleness(pack: Pack, obj: KnowledgeObject, directory: Path) -> bool:
    """Move `generated` entries to `stale` where the knowledge has moved on.

    Staleness is **computed, never guessed**: an artifact is stale exactly when
    `generated_from_revision` is behind the object's current revision. This
    function only writes down what that comparison already implies, so that the
    status in `metadata.yaml` agrees with the status a reader would derive.

    Nothing is ever regenerated and nothing is ever deleted. The pipeline
    detects and reports; a human decides (CLAUDE.md).
    """
    from ke.store import update_object

    current = obj.current_revision
    changes = {
        artifact_type: GenerationEntry(
            status=GenerationStatus.STALE,
            path=entry.path,
            generated_at=entry.generated_at,
            generated_from_revision=entry.generated_from_revision,
            model=entry.model,
            prompt_version=entry.prompt_version,
        )
        for artifact_type, entry in obj.generation.items()
        if entry.status is GenerationStatus.GENERATED
        and entry.is_stale_against(current)
    }
    if not changes:
        return False

    updated = obj.with_engine_fields(generation={**obj.generation, **changes})
    latest = obj.revisions[-1] if obj.revisions else None
    return update_object(
        Path(directory),
        updated,
        (latest.summary_snapshot if latest else "") or obj.title,
        max_summary_words=pack.max_summary_words,
    )


def _ensure_trailing_newline(text: str) -> str:
    """A file without a final newline is a diff that says `\\ No newline`."""
    return text if text.endswith("\n") else text + "\n"
