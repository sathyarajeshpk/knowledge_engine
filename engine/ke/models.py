"""Core data models for Knowledge Engine.

This module is deliberately dependency-free (standard library only). It answers
one question: *what is a knowledge object?* Reading and writing objects on disk
belongs to `ke.store`; checking them belongs to `ke.validate`.

Three things defined here carry most of the design's weight:

1. `FeatureId` - the permanent, date-based identity of a knowledge object.
2. The controlled vocabularies (the enums) - so classification can never drift
   into free text.
3. The **field ownership registry** - the rule that stops the weekly automated
   run from overwriting knowledge the user maintains by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import IntEnum, StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------
# These are `StrEnum` so that a member compares equal to its string form and
# serialises to plain YAML scalars. That keeps metadata.yaml readable by a
# human and by any other tool, which matters because GitHub is the source of
# truth and the files must stay useful without this engine.


class DateConfidence(StrEnum):
    """How much we trust `published_date`.

    `EXACT` - the source stated a publication date and we parsed it.
    `INFERRED` - no reliable date was available; the Feature ID falls back to
    the discovery month and `published_date` is null.
    """

    EXACT = "exact"
    INFERRED = "inferred"


class SourceAuthority(StrEnum):
    """Where the knowledge came from. Purely a property of the source, so it
    needs no heuristics - it is configured per source in pack.yml."""

    OFFICIAL_MICROSOFT = "official-microsoft"
    MICROSOFT_COMMUNITY = "microsoft-community"
    THIRD_PARTY = "third-party"


class Tier(IntEnum):
    """Operational impact: how urgently this matters in real work.

    Independent of how *interesting* the item is to learn - that is
    `LearningPriority`.
    """

    ACT_NOW = 1  # GA, breaking change, deprecation, licensing, security
    LEARN_SOON = 2  # preview features, major capabilities, notable perf changes
    AWARENESS = 3  # minor improvements, docs refresh, community content, events


class LearningPriority(StrEnum):
    """Content value: whether this is worth a tutorial, interview question or
    LinkedIn post. A Tier 3 item can be high priority (a great deep dive) and a
    Tier 1 item can be low (a pricing tweak)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Difficulty(StrEnum):
    """How hard the material is to absorb."""

    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class Workload(StrEnum):
    """Hands-on effort to actually practise the material, as distinct from
    `reading_time`, which only covers reading it."""

    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"


class LearningStatus(StrEnum):
    """The user's own progress. Never written by the engine."""

    NOT_STARTED = "not-started"
    IN_PROGRESS = "in-progress"
    LEARNED = "learned"
    REVISIT = "revisit"


class ObjectStatus(StrEnum):
    """Lifecycle of the knowledge object itself.

    There is no "deleted" member, by design: CLAUDE.md forbids deleting
    knowledge. Superseded objects become `REPLACED` and stay in the repository.
    """

    ACTIVE = "active"
    REPLACED = "replaced"
    DEPRECATED = "deprecated"


class ArtifactType(StrEnum):
    """The on-demand outputs an AI model can produce from a knowledge object.

    The engine only ever tracks these; it never generates them during a
    scheduled run.
    """

    TUTORIAL = "tutorial"
    INTERVIEW_QUESTIONS = "interview-questions"
    LINKEDIN_POST = "linkedin-post"
    INFOGRAPHIC = "infographic"
    CODING_EXAMPLE = "coding-example"
    ARCHITECTURE_EXPLANATION = "architecture-explanation"
    QUIZ = "quiz"


class GenerationStatus(StrEnum):
    """Lifecycle of one generated artifact.

    `STALE` is computed, never guessed: an artifact is stale when the knowledge
    object has been revised since the artifact was generated. Artifacts are
    marked stale, never deleted.
    """

    NONE = "none"  # never requested
    REQUESTED = "requested"  # user wants it; not generated yet
    GENERATED = "generated"  # present and current
    STALE = "stale"  # present but the source knowledge has moved on
    REJECTED = "rejected"  # generated and deliberately discarded; do not re-offer


# ---------------------------------------------------------------------------
# Feature identity
# ---------------------------------------------------------------------------

#: `<PREFIX>-<YYYY>-<MM>-<NNN>`, e.g. `MSF-2026-04-001`.
#: The sequence is at least three digits and may widen to four if a single month
#: ever exceeds 999 items. Existing IDs are never rewritten when that happens.
FEATURE_ID_PATTERN = re.compile(
    r"^(?P<prefix>[A-Z]{2,4})-(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<sequence>\d{3,})$"
)

#: Minimum zero-padding for the sequence segment.
SEQUENCE_WIDTH = 3


@dataclass(frozen=True, order=True)
class FeatureId:
    """The permanent identity of a knowledge object.

    The month segment comes from the **publication** month when a reliable date
    was parsed, and from the **discovery** month otherwise. `DateConfidence`
    records which. Once minted, a Feature ID never changes and is never reused,
    including for replaced objects.
    """

    prefix: str
    year: int
    month: int
    sequence: int

    def __post_init__(self) -> None:
        # Validate through the same pattern used for parsing, so constructing an
        # ID in code and reading one from disk can never disagree.
        if not FEATURE_ID_PATTERN.match(str(self)):
            raise ValueError(f"invalid Feature ID components: {self!r}")

    @classmethod
    def parse(cls, raw: str) -> FeatureId:
        """Parse `MSF-2026-04-001`. Raises `ValueError` if malformed."""
        match = FEATURE_ID_PATTERN.match(raw.strip())
        if match is None:
            raise ValueError(
                f"malformed Feature ID {raw!r}; expected <PREFIX>-<YYYY>-<MM>-<NNN>"
            )
        return cls(
            prefix=match["prefix"],
            year=int(match["year"]),
            month=int(match["month"]),
            sequence=int(match["sequence"]),
        )

    @classmethod
    def is_valid(cls, raw: str) -> bool:
        return FEATURE_ID_PATTERN.match(raw.strip()) is not None

    def __str__(self) -> str:
        return (
            f"{self.prefix}-{self.year:04d}-{self.month:02d}-"
            f"{self.sequence:0{SEQUENCE_WIDTH}d}"
        )

    @property
    def month_key(self) -> str:
        """The `id-registry.json` bucket for this ID, e.g. `2026-04`.

        Counters are per month so that backfilling an old month mints correctly
        dated IDs without disturbing the current month.
        """
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def knowledge_subpath(self) -> str:
        """Where the object lives under `knowledge/`, e.g. `2026/04`."""
        return f"{self.year:04d}/{self.month:02d}"

    def directory_name(self, slug: str) -> str:
        """The object's directory name, e.g. `MSF-2026-04-001-direct-lake-ga`."""
        return f"{self}-{slug}"


# ---------------------------------------------------------------------------
# Field ownership - the central safety property
# ---------------------------------------------------------------------------
# Adding user-maintained learning state to files that an automated weekly job
# rewrites creates one dominant risk: the job silently destroying the user's own
# work. The registry below makes that risk structural rather than a matter of
# convention, and `ke.validate` enforces it in CI.


class Ownership(StrEnum):
    ENGINE = "engine-owned"  # rewritten freely on every run
    PROPOSED = "engine-proposed"  # written only if absent or not locked
    USER = "user-owned"  # never written by the engine


#: Facts the engine derives from the source. Safe to recompute at any time.
ENGINE_OWNED_FIELDS = frozenset(
    {
        "schema_version",
        "id",
        "slug",
        "title",
        "source_name",
        "source_url",
        "source_authority",
        "published_date",
        "discovered_date",
        "date_confidence",
        "content_hash",
        "url_hash",
        "reading_time",
        "status",
        "needs_review",
        "revisions",
        "generation",
    }
)

#: Judgements the engine makes on the user's behalf. The engine writes these
#: only when they are absent, or when the user has not locked them by naming
#: them in `overrides`.
ENGINE_PROPOSED_FIELDS = frozenset(
    {
        "tier",
        "learning_priority",
        "category",
        "tags",
        "difficulty",
        "workload",
        "version",
    }
)

#: The user's own work. The engine must never write these.
USER_OWNED_FIELDS = frozenset(
    {
        "learning_status",
        "notes",
        "prerequisites",
        "builds_on",
        "related_topics",
        "replaced_by",
        "replaces",
        "overrides",
    }
)

#: Every field a valid metadata.yaml may contain.
ALL_METADATA_FIELDS = ENGINE_OWNED_FIELDS | ENGINE_PROPOSED_FIELDS | USER_OWNED_FIELDS

# The three classes must partition the field set exactly. A field that belongs
# to two classes, or to none, is a bug that would let the engine write something
# it should not - so fail at import time rather than at 03:00 on a Sunday.
assert not (ENGINE_OWNED_FIELDS & ENGINE_PROPOSED_FIELDS)
assert not (ENGINE_OWNED_FIELDS & USER_OWNED_FIELDS)
assert not (ENGINE_PROPOSED_FIELDS & USER_OWNED_FIELDS)


def ownership_of(field_name: str) -> Ownership:
    """Return which class a metadata field belongs to.

    Raises `KeyError` for unknown fields so that a typo in engine code cannot
    quietly acquire write permission it was never granted.
    """
    if field_name in ENGINE_OWNED_FIELDS:
        return Ownership.ENGINE
    if field_name in ENGINE_PROPOSED_FIELDS:
        return Ownership.PROPOSED
    if field_name in USER_OWNED_FIELDS:
        return Ownership.USER
    raise KeyError(f"unknown metadata field: {field_name!r}")


def is_engine_writable(field_name: str, overrides: set[str] | frozenset[str]) -> bool:
    """Whether the engine may write `field_name` given the object's lock list.

    This is the single question `ke.store` asks before touching any field.
    """
    ownership = ownership_of(field_name)
    if ownership is Ownership.ENGINE:
        return True
    if ownership is Ownership.USER:
        return False
    return field_name not in overrides


# ---------------------------------------------------------------------------
# Knowledge object components
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Revision:
    """One recorded change to a knowledge object's engine-owned fields.

    Revisions are append-only. Revision 1 is always the initial ingestion.
    """

    revision: int
    date: date
    changed_fields: tuple[str, ...] = ()
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "date": self.date,
            "changed_fields": list(self.changed_fields),
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Revision:
        return cls(
            revision=int(raw["revision"]),
            date=_coerce_date(raw["date"]),
            changed_fields=tuple(raw.get("changed_fields") or ()),
            summary=raw.get("summary") or "",
        )


@dataclass(frozen=True)
class GenerationEntry:
    """Tracking for one artifact type on one knowledge object.

    `model` is recorded for provenance only. Nothing in the engine reads it -
    that is what keeps the system AI-vendor-independent while still letting the
    user see which model produced a given artifact.
    """

    status: GenerationStatus = GenerationStatus.NONE
    path: str | None = None
    generated_at: date | None = None
    generated_from_revision: int | None = None
    model: str | None = None
    prompt_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        # Emit only what is set, so an untouched artifact stays a one-line entry
        # and metadata.yaml remains readable.
        out: dict[str, Any] = {"status": str(self.status)}
        for key in (
            "path",
            "generated_at",
            "generated_from_revision",
            "model",
            "prompt_version",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> GenerationEntry:
        generated_at = raw.get("generated_at")
        return cls(
            status=GenerationStatus(raw.get("status", GenerationStatus.NONE)),
            path=raw.get("path"),
            generated_at=_coerce_date(generated_at) if generated_at else None,
            generated_from_revision=raw.get("generated_from_revision"),
            model=raw.get("model"),
            prompt_version=raw.get("prompt_version"),
        )

    def is_stale_against(self, current_revision: int) -> bool:
        """Whether this artifact was generated from superseded knowledge.

        Only meaningful for artifacts that actually exist; a `NONE` or
        `REQUESTED` entry has nothing to go stale.
        """
        if self.status not in (GenerationStatus.GENERATED, GenerationStatus.STALE):
            return False
        if self.generated_from_revision is None:
            return False
        return self.generated_from_revision < current_revision


@dataclass(frozen=True)
class RawItem:
    """A normalised item from a source, before it has an identity.

    This is the hand-off between discovery (M1) and identity/dedupe (M2). It
    gains hash fields in M1 once `ke.normalize` exists.
    """

    source_name: str
    source_url: str
    source_authority: SourceAuthority
    title: str
    summary: str
    discovered_date: date
    published_date: date | None = None
    date_confidence: DateConfidence = DateConfidence.INFERRED
    raw_tags: tuple[str, ...] = ()

    @property
    def id_basis_date(self) -> date:
        """The date whose month the Feature ID is minted from.

        Publication month when we trust it, discovery month otherwise.
        """
        if self.published_date is not None and self.date_confidence is DateConfidence.EXACT:
            return self.published_date
        return self.discovered_date


@dataclass
class KnowledgeObject:
    """One unit of knowledge: everything in `metadata.yaml`.

    On disk this is a directory whose path is stable for the object's lifetime::

        knowledge/2026/04/MSF-2026-04-001-direct-lake-ga/
            feature.md      canonical knowledge article
            metadata.yaml   this model, serialised
            artifacts/      tutorials, posts, quizzes, code examples
            images/         infographics, diagrams, thumbnails
            references/     supporting notes and extra references
    """

    # --- Identity (engine-owned, immutable after minting) ---
    id: FeatureId
    slug: str
    title: str

    # --- Provenance (engine-owned) ---
    source_name: str
    source_url: str
    source_authority: SourceAuthority
    discovered_date: date
    date_confidence: DateConfidence
    content_hash: str
    url_hash: str
    published_date: date | None = None

    # --- Classification (engine-proposed, user-overridable) ---
    tier: Tier = Tier.AWARENESS
    learning_priority: LearningPriority = LearningPriority.LOW
    category: str | None = None
    tags: tuple[str, ...] = ()
    difficulty: Difficulty = Difficulty.INTERMEDIATE
    workload: Workload = Workload.LIGHT
    version: str | None = None

    # --- Computed (engine-owned) ---
    reading_time: int = 0  # minutes

    # --- Learning state (user-owned; the engine never writes these) ---
    learning_status: LearningStatus = LearningStatus.NOT_STARTED
    notes: str | None = None

    # --- Relationships (user-curated; validated as a DAG in M4) ---
    prerequisites: tuple[str, ...] = ()
    builds_on: tuple[str, ...] = ()
    related_topics: tuple[str, ...] = ()
    replaced_by: str | None = None
    replaces: str | None = None

    # --- Lifecycle ---
    status: ObjectStatus = ObjectStatus.ACTIVE
    needs_review: bool = False
    overrides: tuple[str, ...] = ()  # proposed-class fields the user has locked
    revisions: tuple[Revision, ...] = ()
    generation: dict[ArtifactType, GenerationEntry] = field(default_factory=dict)

    schema_version: int = 1

    # -- derived ---------------------------------------------------------

    @property
    def current_revision(self) -> int:
        """Highest recorded revision number, or 0 for an object with no history."""
        return max((rev.revision for rev in self.revisions), default=0)

    @property
    def directory_name(self) -> str:
        return self.id.directory_name(self.slug)

    @property
    def knowledge_subpath(self) -> str:
        return f"{self.id.knowledge_subpath}/{self.directory_name}"

    def stale_artifacts(self) -> tuple[ArtifactType, ...]:
        """Artifact types generated from a superseded revision."""
        current = self.current_revision
        return tuple(
            artifact_type
            for artifact_type, entry in sorted(self.generation.items())
            if entry.is_stale_against(current)
        )

    def with_engine_fields(self, **updates: Any) -> KnowledgeObject:
        """Return an independent copy with engine-writable updates applied.

        Any attempt to change a field the engine does not own raises
        `PermissionError`. `ke.store` (M5) routes every automated write through
        here, which is what makes "the weekly run cannot destroy your notes" a
        property of the code rather than a promise in the documentation.

        The returned object shares **no mutable state** with the original.
        `dataclasses.replace()` is shallow, so `generation` — the one mutable
        container on this class — is copied explicitly. Its values are frozen
        dataclasses, so a shallow dict copy is sufficient and a deep copy would
        only cost time.

        If a new mutable field is ever added, it must be copied here too;
        `test_no_mutable_state_is_shared_after_an_engine_write` walks every
        field and fails if one is missed.
        """
        locked = set(self.overrides)
        for name in updates:
            if not is_engine_writable(name, locked):
                raise PermissionError(
                    f"engine may not write {name!r} "
                    f"({ownership_of(name)}"
                    f"{', locked by overrides' if name in locked else ''})"
                )
        # An explicit `generation` update wins over the defensive copy.
        return replace(self, **{"generation": dict(self.generation), **updates})

    # -- serialisation ---------------------------------------------------

    def to_metadata_dict(self) -> dict[str, Any]:
        """Serialise to the plain dict written to `metadata.yaml`.

        Field order here is the order humans read it in, which is why this is
        written out explicitly rather than derived from `dataclasses.asdict`.
        """
        return {
            "schema_version": self.schema_version,
            "id": str(self.id),
            "slug": self.slug,
            "title": self.title,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "source_authority": str(self.source_authority),
            "published_date": self.published_date,
            "discovered_date": self.discovered_date,
            "date_confidence": str(self.date_confidence),
            "content_hash": self.content_hash,
            "url_hash": self.url_hash,
            "tier": int(self.tier),
            "learning_priority": str(self.learning_priority),
            "category": self.category,
            "tags": list(self.tags),
            "difficulty": str(self.difficulty),
            "workload": str(self.workload),
            "version": self.version,
            "reading_time": self.reading_time,
            "learning_status": str(self.learning_status),
            "notes": self.notes,
            "prerequisites": list(self.prerequisites),
            "builds_on": list(self.builds_on),
            "related_topics": list(self.related_topics),
            "replaced_by": self.replaced_by,
            "replaces": self.replaces,
            "status": str(self.status),
            "needs_review": self.needs_review,
            "overrides": list(self.overrides),
            "revisions": [rev.to_dict() for rev in self.revisions],
            "generation": {
                str(artifact_type): entry.to_dict()
                for artifact_type, entry in sorted(self.generation.items())
            },
        }

    @classmethod
    def from_metadata_dict(cls, raw: dict[str, Any]) -> KnowledgeObject:
        """Build an object from parsed `metadata.yaml`.

        Raises `ValueError` / `KeyError` on malformed input; `ke.validate`
        catches those and turns them into readable findings.
        """
        published = raw.get("published_date")
        return cls(
            schema_version=int(raw.get("schema_version", 1)),
            id=FeatureId.parse(raw["id"]),
            slug=raw["slug"],
            title=raw["title"],
            source_name=raw["source_name"],
            source_url=raw["source_url"],
            source_authority=SourceAuthority(raw["source_authority"]),
            published_date=_coerce_date(published) if published else None,
            discovered_date=_coerce_date(raw["discovered_date"]),
            date_confidence=DateConfidence(raw["date_confidence"]),
            content_hash=raw["content_hash"],
            url_hash=raw["url_hash"],
            tier=Tier(int(raw.get("tier", Tier.AWARENESS))),
            learning_priority=LearningPriority(
                raw.get("learning_priority", LearningPriority.LOW)
            ),
            category=raw.get("category"),
            tags=tuple(raw.get("tags") or ()),
            difficulty=Difficulty(raw.get("difficulty", Difficulty.INTERMEDIATE)),
            workload=Workload(raw.get("workload", Workload.LIGHT)),
            version=raw.get("version"),
            reading_time=int(raw.get("reading_time", 0)),
            learning_status=LearningStatus(
                raw.get("learning_status", LearningStatus.NOT_STARTED)
            ),
            notes=raw.get("notes"),
            prerequisites=tuple(raw.get("prerequisites") or ()),
            builds_on=tuple(raw.get("builds_on") or ()),
            related_topics=tuple(raw.get("related_topics") or ()),
            replaced_by=raw.get("replaced_by"),
            replaces=raw.get("replaces"),
            status=ObjectStatus(raw.get("status", ObjectStatus.ACTIVE)),
            needs_review=bool(raw.get("needs_review", False)),
            overrides=tuple(raw.get("overrides") or ()),
            revisions=tuple(
                Revision.from_dict(rev) for rev in (raw.get("revisions") or ())
            ),
            generation={
                ArtifactType(name): GenerationEntry.from_dict(entry or {})
                for name, entry in (raw.get("generation") or {}).items()
            },
        )


# ---------------------------------------------------------------------------
# Run reporting
# ---------------------------------------------------------------------------


@dataclass
class SourceHealth:
    """Outcome of polling one source during a run.

    Recorded per run so that a source which quietly stops returning items shows
    up in the weekly digest instead of causing silent data loss.
    """

    source_name: str
    ok: bool
    items_found: int = 0
    duration_seconds: float = 0.0
    error: str | None = None


@dataclass
class RunReport:
    """What one pipeline run did. Rendered into `state/run-log.md` and the
    weekly digest.

    The run log is appended to on *every* run, including runs that found
    nothing, because a weekly commit is what stops GitHub from auto-disabling
    the scheduled workflow after 60 days of inactivity.
    """

    pack: str
    started_at: datetime
    finished_at: datetime | None = None
    sources: list[SourceHealth] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    revised: list[str] = field(default_factory=list)
    duplicates_skipped: int = 0
    needs_review: list[str] = field(default_factory=list)

    @property
    def unhealthy_sources(self) -> list[SourceHealth]:
        return [source for source in self.sources if not source.ok]

    @property
    def is_empty(self) -> bool:
        return not self.created and not self.revised


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date:
    """Accept what PyYAML gives us for a date field.

    PyYAML parses unquoted ISO dates into `datetime.date` already, but a quoted
    value arrives as `str`, so handle both rather than depending on how someone
    hand-edited the file.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip())
    raise ValueError(f"cannot read {value!r} as a date")
