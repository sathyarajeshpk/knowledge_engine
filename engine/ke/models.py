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
from datetime import date, datetime, timezone
from enum import IntEnum, StrEnum
from typing import Any

from ke.identity import IdentityBasis, ItemIdentity

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


class DatePrecision(StrEnum):
    """How precise `published_date` actually is.

    Deliberately **independent of `DateConfidence`**. They answer different
    questions and overloading one to mean both loses information:

    * `date_confidence` - do we trust this date at all? (`exact` | `inferred`)
    * `date_precision`  - how precise is it? (`day` | `month` | `year`)

    The Microsoft Learn "What's New" page dates updates to a month, not a day.
    That is an *exactly known month*, so `confidence: exact` with
    `precision: month`. Recording it as `2026-07-01` with day precision would be
    quietly false; recording it as `inferred` would wrongly suggest we guessed.

    `published_date` always stores a real date so sorting stays deterministic -
    the first of the month for month precision, the first of January for year
    precision. `date_precision` says how much of it to believe.
    """

    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class SourceAuthority(StrEnum):
    """Where the knowledge came from. Purely a property of the source, so it
    needs no heuristics - it is configured per source in pack.yml."""

    OFFICIAL_MICROSOFT = "official-microsoft"
    MICROSOFT_COMMUNITY = "microsoft-community"
    THIRD_PARTY = "third-party"


class AdapterType(StrEnum):
    """Which kind of adapter produced an item.

    Recorded per item so that a parser break can be traced to the code that
    caused it, and so a source that changes format leaves an audit trail.
    """

    RSS = "rss"
    ATOM = "atom"
    HTML = "html"
    GITHUB_COMMITS = "github-commits"
    SITEMAP = "sitemap"
    MANUAL = "manual"


class ExtractionMethod(StrEnum):
    """*How* a field was pulled out of the source document.

    The difference between `html-table-row` and `html-heading` is the
    difference between two extraction strategies that break independently. When
    one of them stops returning items, this field says which.
    """

    FEED_ENTRY = "feed-entry"
    HTML_TABLE_ROW = "html-table-row"
    HTML_HEADING_SECTION = "html-heading-section"
    HTML_LIST_ITEM = "html-list-item"
    JSON_FIELD = "json-field"
    COMMIT_MESSAGE = "commit-message"
    MANUAL_ENTRY = "manual-entry"


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


class HealthState(StrEnum):
    """Operational state of one configured source.

    The engine must never silently stop collecting knowledge, so a source is
    always in exactly one of these and the state is always written down.

    `DEGRADED` is the important one. A source that returns *fewer items than its
    historical baseline* is not healthy just because it returned HTTP 200 - that
    is what a broken parser looks like from the outside. Treating "zero items"
    as "no news this week" is precisely how a pipeline dies quietly.
    """

    HEALTHY = "healthy"  # last run succeeded and item count looks normal
    DEGRADED = "degraded"  # reachable, but suspiciously few items, or fell back
    FAILED = "failed"  # last run could not fetch or parse it
    DISABLED = "disabled"  # taken out of rotation, by a human or by policy


class SourceStatus(StrEnum):
    """Lifecycle of a *source definition*, as distinct from its health.

    Source definitions are **immutable and permanent**. A source is never
    removed from `pack.yml`, because provenance on every object it ever produced
    points at it -- deleting the definition would make historical knowledge
    inexplicable. Same reasoning as Feature IDs (ADR-0005).

    Health says "is it working right now?". Status says "should we still be
    asking?".
    """

    ACTIVE = "active"  # in rotation
    DEPRECATED = "deprecated"  # still polled, but superseded; expect retirement
    DISABLED = "disabled"  # not polled; definition retained for provenance
    REPLACED = "replaced"  # superseded by a named successor source


class SourceRole(StrEnum):
    """Position of a source within its fallback chain.

    If the primary fails the engine tries the secondary. If both fail it raises
    a review item rather than recording "no updates" -- an empty result and a
    broken source must never look the same.
    """

    PRIMARY = "primary"
    SECONDARY = "secondary"
    MANUAL_REVIEW = "manual-review"


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
        "date_precision",
        "provenance",
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

    `content_hash` and `title_snapshot`/`summary_snapshot` are what make the
    **Knowledge Time Machine** possible. Without them a revision records only
    *that* something changed; with them the object carries its own history and
    "how did Direct Lake evolve over two years?" is answerable by reading one
    file, deterministically and without invoking Git or an AI model.

    The snapshots are cheap because ADR-0003 already caps stored summaries at a
    short original paragraph. We are keeping a bounded amount of text we were
    already allowed to store.
    """

    revision: int
    date: date
    changed_fields: tuple[str, ...] = ()
    summary: str = ""
    #: Content hash produced by this revision, forming a verifiable chain.
    content_hash: str | None = None
    #: Title and summary as of this revision, so earlier states are readable.
    title_snapshot: str | None = None
    summary_snapshot: str | None = None
    #: Run that produced this revision; correlates with the run and event logs.
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "revision": self.revision,
            "date": self.date,
            "changed_fields": list(self.changed_fields),
            "summary": self.summary,
        }
        for key in ("content_hash", "title_snapshot", "summary_snapshot", "run_id"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Revision:
        return cls(
            revision=int(raw["revision"]),
            date=_coerce_date(raw["date"]),
            changed_fields=tuple(raw.get("changed_fields") or ()),
            summary=raw.get("summary") or "",
            content_hash=raw.get("content_hash"),
            title_snapshot=raw.get("title_snapshot"),
            summary_snapshot=raw.get("summary_snapshot"),
            run_id=raw.get("run_id"),
        )


class EventType(StrEnum):
    """What happened to a knowledge object, for the append-only event log."""

    DISCOVERED = "discovered"  # first ingestion
    REVISED = "revised"  # source changed; engine fields updated
    RECLASSIFIED = "reclassified"  # tier / priority / category changed
    REPLACED = "replaced"  # superseded by a new object
    DEPRECATED = "deprecated"
    ARTIFACT_GENERATED = "artifact-generated"
    ARTIFACT_STALE = "artifact-stale"


@dataclass(frozen=True)
class KnowledgeEvent:
    """One entry in the pack's append-only event log.

    Knowledge objects answer "what is true now?". The event log answers "what
    happened, and when?" -- and it is the difference between a knowledge base
    and a **Knowledge Time Machine**:

    * *What changed in Fabric during July 2026?* → filter by month.
    * *Show everything since I last studied.* → filter by timestamp.
    * *Compare this month with last month.* → two ranges.
    * *What happened before feature X shipped?* → filter by timestamp < X.

    Answering these by walking every object and replaying its revisions would
    work but scales with the pack rather than with the answer. A single
    time-ordered log makes every one of those queries a scan of one file, in
    chronological order, with no index and no database (ADR-0003).

    Stored as JSON Lines in `state/events.jsonl`: append-only, diff-friendly,
    and readable one line at a time without parsing the whole file.
    """

    occurred_at: datetime
    event_type: EventType
    feature_id: str
    run_id: str
    revision: int | None = None
    changed_fields: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "occurred_at": self.occurred_at.isoformat(),
            "event_type": str(self.event_type),
            "feature_id": self.feature_id,
            "run_id": self.run_id,
        }
        if self.revision is not None:
            out["revision"] = self.revision
        if self.changed_fields:
            out["changed_fields"] = list(self.changed_fields)
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> KnowledgeEvent:
        return cls(
            occurred_at=_coerce_datetime(raw["occurred_at"]),
            event_type=EventType(raw["event_type"]),
            feature_id=raw["feature_id"],
            run_id=raw["run_id"],
            revision=raw.get("revision"),
            changed_fields=tuple(raw.get("changed_fields") or ()),
            detail=raw.get("detail") or "",
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
class Provenance:
    """Where an item came from and exactly how it was extracted.

    Recorded on **every** discovered item, and carried through to the stored
    knowledge object. It answers questions that are impossible to reconstruct
    afterwards:

    * Which adapter produced this, and which version of its parser?
    * Which selector inside the document did it come from?
    * Which run, at what moment?

    The practical payoff is parser-break forensics. When a source changes its
    markup, the items it produced afterwards are subtly wrong rather than
    absent. `parser_version` and `selector` make it possible to find every item
    a given parser produced and re-examine exactly those.
    """

    adapter_type: AdapterType
    source_name: str
    discovered_at: datetime  # UTC, always timezone-aware
    extraction_method: ExtractionMethod
    parser_version: int
    #: Which signal established this item's identity, and the key derived from
    #: it. Recorded because the first question when investigating a duplicate or
    #: a missed match is always "what were we matching on?".
    identity_basis: IdentityBasis = IdentityBasis.CONTENT_FINGERPRINT
    identity_key: str = ""
    #: The concrete selector, XPath, feed field or table column used. Free text
    #: because every adapter type addresses its document differently.
    selector: str | None = None
    #: Identifier of the run that produced this item; correlates the object with
    #: the run log and the event log.
    run_id: str | None = None
    #: Set when this item came from a fallback rather than the primary source.
    source_role: SourceRole = SourceRole.PRIMARY

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "adapter_type": str(self.adapter_type),
            "source_name": self.source_name,
            "discovered_at": self.discovered_at.isoformat(),
            "extraction_method": str(self.extraction_method),
            "parser_version": self.parser_version,
            "identity_basis": str(self.identity_basis),
            "identity_key": self.identity_key,
            "source_role": str(self.source_role),
        }
        for key in ("selector", "run_id"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Provenance:
        return cls(
            adapter_type=AdapterType(raw["adapter_type"]),
            source_name=raw["source_name"],
            discovered_at=_coerce_datetime(raw["discovered_at"]),
            extraction_method=ExtractionMethod(raw["extraction_method"]),
            parser_version=int(raw["parser_version"]),
            identity_basis=IdentityBasis(
                raw.get("identity_basis", IdentityBasis.CONTENT_FINGERPRINT)
            ),
            identity_key=raw.get("identity_key", ""),
            selector=raw.get("selector"),
            run_id=raw.get("run_id"),
            source_role=SourceRole(raw.get("source_role", SourceRole.PRIMARY)),
        )


@dataclass(frozen=True)
class RawItem:
    """A normalised item from a source, before it has an identity.

    This is the hand-off between discovery (M1) and identity/dedupe (M2), and
    the **only** type a discovery adapter may return. Every adapter implements
    the same `discover() -> list[RawItem]` signature, which is what keeps every
    downstream stage completely source-agnostic: dedupe, minting, storage and
    classification never learn whether an item came from RSS or a table row.
    """

    source_name: str
    source_url: str
    source_authority: SourceAuthority
    title: str
    summary: str
    discovered_date: date
    provenance: Provenance
    identity: ItemIdentity
    published_date: date | None = None
    date_confidence: DateConfidence = DateConfidence.INFERRED
    date_precision: DatePrecision = DatePrecision.DAY
    raw_tags: tuple[str, ...] = ()

    @property
    def id_basis_date(self) -> date:
        """The date whose month the Feature ID is minted from.

        Publication month when we trust it, discovery month otherwise.

        `date_precision` does not enter into this: month precision is *exactly*
        what minting needs (ADR-0005), so a month-precise date is fully usable
        here. Only `date_confidence` decides whether we trust it at all.
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
    provenance: Provenance
    published_date: date | None = None
    date_precision: DatePrecision = DatePrecision.DAY

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
            "date_precision": str(self.date_precision),
            "content_hash": self.content_hash,
            "provenance": self.provenance.to_dict(),
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
            date_precision=DatePrecision(raw.get("date_precision", DatePrecision.DAY)),
            content_hash=raw["content_hash"],
            provenance=Provenance.from_dict(raw["provenance"]),
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
class SourceAttempt:
    """One attempt to fetch one source during one run.

    This is the raw observation. `SourceHealth` is the running state derived
    from a series of these.
    """

    source_name: str
    run_id: str
    attempted_at: datetime
    ok: bool
    role: SourceRole = SourceRole.PRIMARY
    http_status: int | None = None
    response_ms: int = 0
    items_discovered: int = 0
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "run_id": self.run_id,
            "attempted_at": self.attempted_at.isoformat(),
            "ok": self.ok,
            "role": str(self.role),
            "http_status": self.http_status,
            "response_ms": self.response_ms,
            "items_discovered": self.items_discovered,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceAttempt:
        return cls(
            source_name=raw["source_name"],
            run_id=raw["run_id"],
            attempted_at=_coerce_datetime(raw["attempted_at"]),
            ok=bool(raw["ok"]),
            role=SourceRole(raw.get("role", SourceRole.PRIMARY)),
            http_status=raw.get("http_status"),
            response_ms=int(raw.get("response_ms", 0)),
            items_discovered=int(raw.get("items_discovered", 0)),
            failure_reason=raw.get("failure_reason"),
        )


#: Consecutive failures before a source is escalated to a GitHub Issue (M6).
FAILURE_ALERT_THRESHOLD = 3

#: A run returning fewer than this fraction of a source's historical median is
#: treated as a suspected parser break rather than "no news". Deliberately
#: generous: a false "possible parser break" costs a glance at the digest, while
#: a missed one costs weeks of silently lost knowledge.
PARSER_BREAK_RATIO = 0.34

#: Attempts needed before a baseline means anything. Below this the engine has
#: no opinion and will not cry parser break.
BASELINE_MIN_OBSERVATIONS = 3


@dataclass
class SourceHealth:
    """Running health state of one configured source.

    Persisted in `state/source-health.json` and updated every run, so a source
    that quietly stops producing knowledge is visible in the weekly digest, in
    `ke health`, and eventually in a GitHub Issue -- never silently absent.
    """

    source_name: str
    state: HealthState = HealthState.HEALTHY
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    last_http_status: int | None = None
    last_failure_reason: str | None = None
    last_items_discovered: int = 0
    #: Item counts from previous *successful* runs, oldest first. Bounded, since
    #: this file is committed on every run and must not grow without limit.
    recent_item_counts: tuple[int, ...] = ()
    #: Set when a human disables a source; the engine never clears it.
    disabled_reason: str | None = None
    #: Issue number of the currently-open health alert, so the engine does not
    #: open a duplicate while one remains open.
    open_alert_issue: int | None = None

    MAX_HISTORY = 26  # roughly six months of weekly runs

    @property
    def baseline_items(self) -> float | None:
        """Median item count over recent successful runs.

        Median rather than mean: one anomalous week should not move the
        baseline enough to mask a genuine break the following week.
        """
        counts = sorted(self.recent_item_counts)
        if len(counts) < BASELINE_MIN_OBSERVATIONS:
            return None
        middle = len(counts) // 2
        if len(counts) % 2:
            return float(counts[middle])
        return (counts[middle - 1] + counts[middle]) / 2

    def looks_like_parser_break(self, items_discovered: int) -> bool:
        """Whether this run's yield is suspiciously low for this source.

        A source that has historically returned updates and suddenly returns
        (almost) nothing has probably broken, not gone quiet. Assuming the
        latter is how a pipeline dies without anyone noticing.
        """
        baseline = self.baseline_items
        if baseline is None or baseline <= 0:
            return False
        return items_discovered < baseline * PARSER_BREAK_RATIO

    @property
    def needs_alert(self) -> bool:
        """Whether this source warrants a GitHub Issue right now."""
        if self.state is HealthState.DISABLED or self.open_alert_issue is not None:
            return False
        return self.consecutive_failures >= FAILURE_ALERT_THRESHOLD

    def record(self, attempt: SourceAttempt) -> SourceHealth:
        """Fold one attempt into the running state, returning a new copy.

        Never mutates: like `KnowledgeObject.with_engine_fields`, callers get an
        independent object so a partially-applied update is impossible.
        """
        if self.state is HealthState.DISABLED:
            return replace(self, last_attempt_at=attempt.attempted_at)

        if not attempt.ok:
            return replace(
                self,
                state=HealthState.FAILED,
                last_attempt_at=attempt.attempted_at,
                consecutive_failures=self.consecutive_failures + 1,
                last_http_status=attempt.http_status,
                last_failure_reason=attempt.failure_reason,
                last_items_discovered=0,
            )

        suspicious = self.looks_like_parser_break(attempt.items_discovered)
        history = (*self.recent_item_counts, attempt.items_discovered)[-self.MAX_HISTORY:]
        degraded = suspicious or attempt.role is not SourceRole.PRIMARY
        return replace(
            self,
            state=HealthState.DEGRADED if degraded else HealthState.HEALTHY,
            last_success_at=attempt.attempted_at,
            last_attempt_at=attempt.attempted_at,
            consecutive_failures=0,
            last_http_status=attempt.http_status,
            last_failure_reason=(
                "possible parser break: item count far below historical baseline"
                if suspicious else None
            ),
            last_items_discovered=attempt.items_discovered,
            recent_item_counts=history,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_name": self.source_name,
            "state": str(self.state),
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "consecutive_failures": self.consecutive_failures,
            "last_http_status": self.last_http_status,
            "last_failure_reason": self.last_failure_reason,
            "last_items_discovered": self.last_items_discovered,
            "recent_item_counts": list(self.recent_item_counts),
            "disabled_reason": self.disabled_reason,
            "open_alert_issue": self.open_alert_issue,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SourceHealth:
        return cls(
            source_name=raw["source_name"],
            state=HealthState(raw.get("state", HealthState.HEALTHY)),
            last_success_at=(
                _coerce_datetime(raw["last_success_at"]) if raw.get("last_success_at") else None
            ),
            last_attempt_at=(
                _coerce_datetime(raw["last_attempt_at"]) if raw.get("last_attempt_at") else None
            ),
            consecutive_failures=int(raw.get("consecutive_failures", 0)),
            last_http_status=raw.get("last_http_status"),
            last_failure_reason=raw.get("last_failure_reason"),
            last_items_discovered=int(raw.get("last_items_discovered", 0)),
            recent_item_counts=tuple(raw.get("recent_item_counts") or ()),
            disabled_reason=raw.get("disabled_reason"),
            open_alert_issue=raw.get("open_alert_issue"),
        )


@dataclass
class RunReport:
    """What one pipeline run did. Rendered into `state/run-log.md` and the
    weekly digest.

    The run log is appended to on *every* run, including runs that found
    nothing, because a weekly commit is what stops GitHub from auto-disabling
    the scheduled workflow after 60 days of inactivity.
    """

    pack: str
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    attempts: list[SourceAttempt] = field(default_factory=list)
    health: list[SourceHealth] = field(default_factory=list)
    events: list[KnowledgeEvent] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    revised: list[str] = field(default_factory=list)
    duplicates_skipped: int = 0
    needs_review: list[str] = field(default_factory=list)

    @property
    def failed_attempts(self) -> list[SourceAttempt]:
        return [attempt for attempt in self.attempts if not attempt.ok]

    def by_state(self, state: HealthState) -> list[SourceHealth]:
        """Sources currently in a given state, for the digest and `ke health`."""
        return [source for source in self.health if source.state is state]

    @property
    def alerts_needed(self) -> list[SourceHealth]:
        """Sources that have failed enough consecutive runs to warrant an Issue."""
        return [source for source in self.health if source.needs_alert]

    @property
    def succeeded_overall(self) -> bool:
        """Whether the run itself is a success.

        **A failed source never fails the run.** Harvesting continues from every
        healthy source and the failure is recorded in the run log, the digest
        and the health file. The run only fails if it could not complete at all.
        """
        return self.finished_at is not None

    @property
    def is_empty(self) -> bool:
        return not self.created and not self.revised


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_datetime(value: Any) -> datetime:
    """Read a timestamp, always returning an aware UTC `datetime`.

    Every timestamp the engine writes is UTC ISO-8601. Naive values are treated
    as UTC rather than as local time, because a run log that means different
    instants depending on which machine wrote it is worse than useless.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"cannot read {value!r} as a timestamp")
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(
        timezone.utc
    )


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
