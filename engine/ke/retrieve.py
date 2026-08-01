"""Finding things again.

Six milestones went into getting knowledge *in*. This is the first module about
getting it *out*, and the constraint that shapes it is one the plan settled
early: **GitHub Pages is unavailable for private repositories on the free plan**,
so there is no web UI and there will not be one. Retrieval is repo-native —
regenerated Markdown indexes for browsing, and this module for querying.

## Why there is no index to keep in sync

`search` walks the objects and filters them. There is no inverted index, no
cached query result, no `search.json`. At 222 objects a full scan takes
milliseconds, and a derived structure that can disagree with the objects it
describes is a bug waiting for a quiet week -- the same reasoning that makes
`indexer.py` rebuild in full rather than patch.

If a pack ever grows to the point where this is slow, the fix is a cache built
during `rebuild_indexes` and thrown away whenever it might be stale. That is a
different design and should be a different decision, made when there is evidence
for it rather than in anticipation.

## Why filters compose by AND

`--tier 1 --learning-status not-started --tag direct-lake` means all three.
There is no `--or`, no query language, and no precedence to remember. The
question a person actually asks is "what should I work on next", and that is a
conjunction every time.

`--text` is the one fuzzy filter: a substring over title, category and tags,
case-insensitive and punctuation-insensitive, so `direct lake`, `Direct-Lake`
and `directlake` all find the same objects.

It is deliberately not a ranked search. Ranking invents a notion of relevance
the engine cannot justify, and a wrong ranking is worse than none because it
hides things convincingly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Iterable

from ke.models import (
    Difficulty,
    GenerationStatus,
    KnowledgeObject,
    LearningPriority,
    LearningStatus,
    ObjectStatus,
    Tier,
)
from ke.pack import Pack


@dataclass(frozen=True)
class Query:
    """What to look for. Every field is optional; all supplied ones must match.

    A frozen dataclass rather than keyword arguments threaded through several
    functions, so a query can be built once, logged, and reused -- and so adding
    a filter is one field rather than one parameter in four signatures.
    """

    text: str | None = None
    tier: Tier | None = None
    learning_priority: LearningPriority | None = None
    difficulty: Difficulty | None = None
    learning_status: LearningStatus | None = None
    status: ObjectStatus | None = None
    category: str | None = None
    tag: str | None = None
    source: str | None = None
    since: date | None = None
    until: date | None = None
    needs_review: bool | None = None
    #: Objects with at least one stale artifact. The question "what have I
    #: generated that the source has since changed?" has no other answer.
    stale: bool | None = None

    @property
    def is_empty(self) -> bool:
        """Whether this query filters anything at all.

        Worth knowing explicitly: `ke search` with no arguments listing the whole
        pack is a reasonable default, but it should be a deliberate one rather
        than the accidental result of every filter being `None`.
        """
        return all(getattr(self, f) is None for f in self.__dataclass_fields__)


def _effective_date(obj: KnowledgeObject) -> date:
    """The date a human means when they say "when was this".

    Publication when known, discovery otherwise -- the same precedence the
    Feature ID uses (ADR-0005), so a search by date and an ID's month cannot
    disagree about which month an object belongs to.
    """
    return obj.published_date or obj.discovered_date


def _fold(text: str) -> str:
    """Lowercase, and reduce every run of punctuation to a single space.

    So that `--text "direct lake"` matches the tag `direct-lake` and the title
    "Direct Lake". Without this, the search silently depends on whether the
    person typing remembered the hyphen — and a filter that returns one result
    instead of three, with no indication anything was missed, is worse than one
    that returns nothing.
    """
    return " ".join(
        "".join(ch if ch.isalnum() else " " for ch in text).lower().split()
    )


def _haystack(obj: KnowledgeObject) -> str:
    """What `--text` searches: title, tags, category.

    Deliberately excludes the article body. The body is a short original summary
    (ADR-0003) and including it would make `--text` match on incidental prose,
    turning a precise filter into a vague one.
    """
    return _fold(" ".join([obj.title, obj.category or "", " ".join(obj.tags)]))


#: One predicate per filter. Keeping them in a table rather than a long `if`
#: chain means `matches` cannot accidentally skip one, and a new filter is a
#: single entry.
_PREDICATES: dict[str, Callable[[KnowledgeObject, object], bool]] = {
    "text": lambda obj, v: _fold(str(v)) in _haystack(obj),
    "tier": lambda obj, v: obj.tier is v,
    "learning_priority": lambda obj, v: obj.learning_priority is v,
    "difficulty": lambda obj, v: obj.difficulty is v,
    "learning_status": lambda obj, v: obj.learning_status is v,
    "status": lambda obj, v: obj.status is v,
    "category": lambda obj, v: (obj.category or "").lower() == str(v).lower(),
    "tag": lambda obj, v: str(v).lower() in {t.lower() for t in obj.tags},
    "source": lambda obj, v: obj.source_name.lower() == str(v).lower(),
    "since": lambda obj, v: _effective_date(obj) >= v,
    "until": lambda obj, v: _effective_date(obj) <= v,
    "needs_review": lambda obj, v: obj.needs_review is bool(v),
    "stale": lambda obj, v: bool(obj.stale_artifacts) is bool(v),
}


def matches(obj: KnowledgeObject, query: Query) -> bool:
    """Whether one object satisfies every supplied filter."""
    for name in query.__dataclass_fields__:
        value = getattr(query, name)
        if value is None:
            continue
        if not _PREDICATES[name](obj, value):
            return False
    return True


def sort_key(obj: KnowledgeObject) -> tuple:
    """Most useful first, then deterministic.

    Tier ascending (act-now before awareness), then newest first, then Feature ID
    as a final tiebreak so two objects can never swap places between runs --
    ADR-0022 applies to anything a human might diff, and search output ends up
    pasted into notes.
    """
    return (int(obj.tier), -_effective_date(obj).toordinal(), str(obj.id))


def search(
    pack: Pack, query: Query, *, limit: int | None = None
) -> list[KnowledgeObject]:
    """Every object matching the query, most useful first."""
    from ke.harvest import load_existing_objects

    found = [obj for obj, _ in load_existing_objects(pack) if matches(obj, query)]
    found.sort(key=sort_key)
    return found[:limit] if limit else found


def get(pack: Pack, feature_id: str) -> tuple[KnowledgeObject, object]:
    """One object by Feature ID, via the registry.

    Raises `KeyError` rather than returning `None`: `ke get` on a typo'd ID has
    no useful empty answer, and silently printing nothing is the least helpful
    thing it could do.
    """
    from ke.history import HistoryError, find_object

    try:
        return find_object(pack, feature_id)
    except HistoryError as exc:
        raise KeyError(str(exc)) from exc


def resolve(packs: Iterable[Pack], feature_id: str) -> tuple[Pack, KnowledgeObject, object]:
    """Find an object across several packs, by ID prefix where possible.

    M8 adds a second pack and cross-pack references with it. Resolving by prefix
    first means `ke get PBI-2026-01-001` does not scan the Fabric pack, and the
    fallback scan means an ID whose prefix does not match any pack still
    resolves rather than mysteriously not existing.
    """
    packs = list(packs)
    prefix = feature_id.split("-", 1)[0].upper()
    ordered = sorted(packs, key=lambda p: (p.id_prefix or "").upper() != prefix)
    last: KeyError | None = None
    for pack in ordered:
        try:
            obj, directory = get(pack, feature_id)
            return pack, obj, directory
        except KeyError as exc:
            last = exc
    raise last or KeyError(f"{feature_id} was not found in any pack")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_results(objects: list[KnowledgeObject], *, total: int | None = None) -> str:
    """Search results as an aligned table.

    Terminal output, not Markdown: this is read in a shell, and a Markdown table
    with pipes is harder to scan than aligned columns. The Markdown view of the
    pack already exists, rebuilt every run, in `indexes/`.
    """
    if not objects:
        return "No objects matched.\n"

    lines = []
    for obj in objects:
        marker = "*" if obj.stale_artifacts else " "
        lines.append(
            f"{marker} {obj.id}  t{int(obj.tier)}  "
            f"{str(obj.learning_status):<12}  {obj.title[:64]}"
        )

    shown = len(objects)
    footer = f"\n{shown} object(s)"
    if total is not None and total > shown:
        footer += f" of {total} (use --limit to see more)"
    if any(obj.stale_artifacts for obj in objects):
        footer += "\n* has at least one stale artifact — see `ke status`"
    return "\n".join(lines) + footer + "\n"


def render_object(obj: KnowledgeObject, directory: object) -> str:
    """One object in full, for `ke get`.

    Shows what the object *is* and where it lives, not its article text -- the
    article is a file the reader can open, and duplicating it here would make
    `ke get` output that disagrees with the file as soon as either changes.
    """
    lines = [
        "",
        f"{obj.id}  {obj.title}",
        f"  {directory}",
        "",
        # `Tier` is an IntEnum, so `str(tier)` is the number — printing
        # "2 (2)" told the reader nothing twice.
        f"  tier            {int(obj.tier)} ({obj.tier.name.lower().replace('_', '-')})",
        f"  category        {obj.category or '—'}",
        f"  tags            {', '.join(obj.tags) or '—'}",
        f"  difficulty      {obj.difficulty}",
        f"  priority        {obj.learning_priority}",
        "",
        f"  status          {obj.status}   lifecycle: {obj.lifecycle}",
        f"  learning        {obj.learning_status}",
        f"  needs review    {'yes' if obj.needs_review else 'no'}",
        "",
        f"  published       {obj.published_date or '—'} "
        f"({obj.date_confidence}, {obj.date_precision})",
        f"  discovered      {obj.discovered_date}",
        f"  source          {obj.source_name} ({obj.source_authority})",
        f"  url             {obj.announcement_url or obj.source_url}",
        f"  revision        {obj.current_revision}",
    ]
    if obj.overrides:
        lines.append(f"  locked fields   {', '.join(obj.overrides)}")
    if obj.replaced_by:
        lines.append(f"  replaced by     {obj.replaced_by}")
    if obj.replaces:
        lines.append(f"  replaces        {obj.replaces}")

    # An entry at `none` is the absence of an artifact, not an artifact. A
    # generation block full of `none` means the same thing as no block at all,
    # and listing seven "none" rows would bury the one that says `requested`.
    tracked = [
        (artifact_type, entry)
        for artifact_type, entry in sorted(obj.generation.items())
        if entry.status is not GenerationStatus.NONE
    ]
    if tracked:
        lines += ["", "  artifacts"]
        for artifact_type, entry in tracked:
            stale = " (stale)" if entry.is_stale_against(obj.current_revision) else ""
            lines.append(f"    {str(artifact_type):<26} {entry.status}{stale}")
    else:
        lines += ["", "  artifacts       none generated — see `ke generate --help`"]

    if obj.notes:
        lines += ["", "  notes", *(f"    {line}" for line in obj.notes.splitlines())]
    lines.append("")
    return "\n".join(lines)
