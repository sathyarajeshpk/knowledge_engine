"""Writing knowledge objects to disk.

This is where knowledge becomes **permanent**. An object directory's path is
stable for its entire lifetime (ADR-0006), so getting the layout right matters
more than getting it quickly.

    knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
        feature.md      the human-readable article
        metadata.yaml   the machine-readable record

Subdirectories (`artifacts/`, `images/`, `references/`) are created on demand,
never up front: Git cannot track an empty directory, so creating them eagerly
would mean they vanish on the next clone (ADR-0015).

Two properties this module is responsible for:

* **Deterministic bytes.** Same object in, same file out, down to key order and
  the trailing newline. Without that, every harvest would show spurious diffs
  and a real change would be impossible to spot (ADR-0022).
* **Atomic writes.** A half-written `metadata.yaml` is worse than no file: it
  fails validation on the next run and looks like corruption rather than an
  interrupted job.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path

import yaml

from ke.models import (
    DateConfidence,
    FeatureId,
    KnowledgeObject,
    Lifecycle,
    RawItem,
    Revision,
)
from ke.normalize import canonical_url, content_hash, slugify, truncate_summary, url_hash
from ke.paths import ensure_contained

#: Words reserved in `feature.md` for everything that is not the summary -- the
#: heading is excluded from the count, but the source link line is not. Measured
#: rather than guessed: the link line is one whitespace-delimited token plus its
#: label, and this leaves comfortable headroom.
BODY_OVERHEAD_WORDS = 12


def build_object(
    item: RawItem,
    feature_id: FeatureId,
    *,
    needs_review: bool = False,
) -> KnowledgeObject:
    """Turn a graded `RawItem` into the object that will be stored.

    Only engine-owned fields are set here. Classification (`tier`, `category`,
    `difficulty`…) is left absent for M3 to propose, and user-owned fields are
    never touched -- an object arrives with empty learning state by design.
    """
    return KnowledgeObject(
        id=feature_id,
        slug=slugify(item.title),
        title=item.title,
        source_name=item.source_name,
        source_url=item.source_url,
        announcement_url=item.announcement_url,
        identity_confidence=item.identity_confidence,
        source_authority=item.source_authority,
        published_date=item.published_date,
        discovered_date=item.first_discovered_date or item.discovered_date,
        date_confidence=item.date_confidence,
        date_precision=item.date_precision,
        provenance=item.provenance,
        content_hash=content_hash(item.title, item.summary),
        url_hash=url_hash(item.source_url),
        lifecycle=Lifecycle.MINTED,
        needs_review=needs_review,
        revisions=(
            Revision(
                revision=1,
                date=item.first_discovered_date or item.discovered_date,
                changed_fields=(),
                summary="Initial ingestion",
                content_hash=content_hash(item.title, item.summary),
                title_snapshot=item.title,
                summary_snapshot=item.summary,
                run_id=item.provenance.run_id,
            ),
        ),
    )


def render_feature_document(obj: KnowledgeObject, summary: str, max_words: int) -> str:
    """The human-readable article: heading, summary, source link.

    Kept deliberately thin. ADR-0003 forbids storing the full text of a
    third-party article, and `ke validate` counts **every word below the
    heading** against the pack's limit -- including the source line. So the
    summary is re-truncated here against a budget that leaves room for it,
    rather than being written at full length and failing validation.
    """
    budget = max(max_words - BODY_OVERHEAD_WORDS, 20)
    body = truncate_summary(summary, budget)

    link = obj.announcement_url or obj.source_url
    lines = [
        f"# {obj.title}",
        "",
        body,
        "",
        f"Source: [{obj.source_name}]({link})",
        "",
    ]
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temporary file in the same directory, then rename.

    Same directory matters: `os.replace` is only atomic within a filesystem, and
    a temp dir may be on another one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    )
    try:
        with handle as stream:
            stream.write(text)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def render_metadata(obj: KnowledgeObject) -> str:
    """`metadata.yaml`, in a fixed key order.

    `sort_keys=False` preserves `to_dict`'s ordering, which groups fields the way
    a human reads them (identity, provenance, classification, learning state)
    rather than alphabetically. That ordering is part of the on-disk format.
    """
    # Aliases OFF. PyYAML emits `&id001` / `*id001` anchors when the same
    # object appears twice -- and it does, because a date is shared between
    # `discovered_date` and the revision that recorded it. The file must be
    # readable by a human in the GitHub UI without knowing YAML anchor syntax.
    class _NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data):  # noqa: ARG002
            return True

    return yaml.dump(
        obj.to_metadata_dict(),
        Dumper=_NoAliases,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )


def object_dir(pack_knowledge_dir: Path, obj: KnowledgeObject) -> Path:
    """Where an object lives. Every new-object write is rooted here.

    The containment check closes the case where the path is entirely
    engine-derived but a *component* of it is a symlink: `knowledge/` or
    `knowledge/2026` pointing elsewhere makes every path built under it well
    formed and wrong (see `ke.paths`).

    The boundary is the **pack root**, `pack_knowledge_dir.parent`, not the
    knowledge directory. Checking a path against the very argument it was built
    from proves only that `..` does not appear in a Feature ID -- which the ID
    grammar already guarantees -- and would pass a symlinked `knowledge/`
    unharmed. That was the first version of this guard, and the test above it
    caught it.

    It cannot catch a symlinked pack root, for the same reason one level up.
    `Pack.find_roots` refuses those, and `ke validate` reports both as SEC001.
    """
    return ensure_contained(
        pack_knowledge_dir
        / obj.id.knowledge_subpath
        / obj.id.directory_name(obj.slug),
        pack_knowledge_dir.parent,
        what=f"object directory for {obj.id}",
    )


def write_object(
    pack_knowledge_dir: Path,
    obj: KnowledgeObject,
    summary: str,
    *,
    max_summary_words: int,
) -> Path:
    """Write one knowledge object. Returns its directory.

    Refuses to overwrite an existing object: a Feature ID is permanent, so a
    second write to the same path means either a reused ID or a re-mint, and
    both are bugs worth stopping for rather than silently resolving.
    """
    directory = object_dir(pack_knowledge_dir, obj)
    metadata_path = directory / "metadata.yaml"
    feature_path = directory / "feature.md"
    if metadata_path.exists():
        raise FileExistsError(
            f"{metadata_path} already exists; refusing to overwrite a minted object"
        )

    # Render BOTH documents before writing EITHER. An object is a pair of files,
    # so a failure between them leaves a half-object sitting at a
    # permanent-looking path -- which is exactly what happened the first time
    # this ran: a serialisation error produced 222 orphaned `feature.md` files
    # with no metadata. Rendering first turns that class of failure into "no
    # files written" instead of "half an object written".
    feature_text = render_feature_document(obj, summary, max_summary_words)
    metadata_text = render_metadata(obj)

    written: list[Path] = []
    try:
        _atomic_write(feature_path, feature_text)
        written.append(feature_path)
        _atomic_write(metadata_path, metadata_text)
        written.append(metadata_path)
    except BaseException:
        # A disk-level failure between the two writes is still possible. Leave
        # nothing behind rather than a directory that looks like an object.
        for path in written:
            path.unlink(missing_ok=True)
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        raise
    return directory


def load_object(directory: Path) -> KnowledgeObject | None:
    """Read a stored object, or `None` if it is absent or unreadable.

    Returns `None` rather than raising so that one damaged object cannot stop a
    harvest. `ke validate` is what reports it; the pipeline's job is to keep
    going and leave the damage visible.
    """
    metadata_path = directory / "metadata.yaml"
    if not metadata_path.exists():
        return None
    try:
        raw = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
        return KnowledgeObject.from_metadata_dict(raw)
    except Exception:  # noqa: BLE001
        return None


def update_object(
    directory: Path,
    obj: KnowledgeObject,
    summary: str,
    *,
    max_summary_words: int,
) -> bool:
    """Rewrite an existing object in place. Returns whether bytes changed.

    Unlike `write_object` this **expects** the files to exist, and it compares
    rendered output against what is already on disk before writing. A run that
    changes nothing must leave the file untouched -- not rewrite identical
    bytes, which would still update the mtime and, more importantly, teach
    everyone to ignore the weekly diff.

    The object's path is never recomputed here: an update writes to where the
    object already lives, because a Feature ID's directory is permanent
    (ADR-0006).
    """
    feature_text = render_feature_document(obj, summary, max_summary_words)
    metadata_text = render_metadata(obj)

    changed = False
    for path, text in (
        (directory / "feature.md", feature_text),
        (directory / "metadata.yaml", metadata_text),
    ):
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        _atomic_write(path, text)
        changed = True
    return changed


def reading_time_minutes(text: str, words_per_minute: int = 200) -> int:
    """Rounded up, minimum 1. A zero-minute read would be a lie."""
    words = len(text.split())
    return max(1, -(-words // words_per_minute))


def with_reading_time(obj: KnowledgeObject, summary: str) -> KnowledgeObject:
    return replace(obj, reading_time=reading_time_minutes(f"{obj.title} {summary}"))


def dates_are_trustworthy(obj: KnowledgeObject) -> bool:
    """Whether this object's ID month came from a real publication date.

    Not a gate -- month-precision knowledge is still knowledge and mints
    normally. Exposed for the digest, so "how much of the pack is dated by
    inference?" is answerable.
    """
    return (
        obj.published_date is not None
        and obj.date_confidence is DateConfidence.EXACT
    )


def canonical_source(obj: KnowledgeObject) -> str:
    return canonical_url(obj.announcement_url or obj.source_url)
