"""Tests for the core data models.

The ownership tests are the important ones. Everything else in the engine
depends on the registry being a correct partition of the metadata fields.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ke.models import (
    ALL_METADATA_FIELDS,
    ENGINE_OWNED_FIELDS,
    ENGINE_PROPOSED_FIELDS,
    USER_OWNED_FIELDS,
    AdapterType,
    ArtifactType,
    DateConfidence,
    DatePrecision,
    EventType,
    ExtractionMethod,
    HealthState,
    KnowledgeEvent,
    Provenance,
    RunReport,
    SourceAttempt,
    SourceHealth,
    SourceRole,
    FeatureId,
    GenerationEntry,
    GenerationStatus,
    KnowledgeObject,
    Ownership,
    RawItem,
    Revision,
    SourceAuthority,
    is_engine_writable,
    ownership_of,
)

from ke.acquisition.identity import compute_identity

from conftest import make_object, make_provenance

# ---------------------------------------------------------------------------
# Feature IDs
# ---------------------------------------------------------------------------


def test_feature_id_roundtrips_through_string():
    parsed = FeatureId.parse("MSF-2026-04-001")
    assert parsed.prefix == "MSF"
    assert (parsed.year, parsed.month, parsed.sequence) == (2026, 4, 1)
    assert str(parsed) == "MSF-2026-04-001"


def test_feature_id_exposes_registry_and_path_keys():
    parsed = FeatureId.parse("MSF-2026-04-007")
    assert parsed.month_key == "2026-04"
    assert parsed.knowledge_subpath == "2026/04"
    assert parsed.directory_name("direct-lake-ga") == "MSF-2026-04-007-direct-lake-ga"


def test_feature_id_allows_four_digit_sequence_overflow():
    """A month exceeding 999 items widens the sequence; it never renumbers."""
    assert str(FeatureId.parse("MSF-2026-04-1000")) == "MSF-2026-04-1000"


@pytest.mark.parametrize(
    "raw",
    [
        "MSF-2026-4-001",  # month not zero-padded
        "MSF-2026-13-001",  # impossible month
        "MSF-2026-00-001",  # impossible month
        "msf-2026-04-001",  # prefix must be upper case
        "MSF-2026-04-01",  # sequence too short
        "MSF-2026-04",  # missing sequence
        "MSF20260401",  # no separators
        "",
    ],
)
def test_feature_id_rejects_malformed_input(raw):
    assert FeatureId.is_valid(raw) is False
    with pytest.raises(ValueError):
        FeatureId.parse(raw)


def test_feature_id_components_are_validated_on_construction():
    """Building an ID in code and reading one from disk must agree."""
    with pytest.raises(ValueError):
        FeatureId(prefix="MSF", year=2026, month=13, sequence=1)


def test_feature_ids_sort_chronologically():
    ids = [
        FeatureId.parse("MSF-2026-04-002"),
        FeatureId.parse("MSF-2025-11-001"),
        FeatureId.parse("MSF-2026-04-001"),
    ]
    assert [str(i) for i in sorted(ids)] == [
        "MSF-2025-11-001",
        "MSF-2026-04-001",
        "MSF-2026-04-002",
    ]


# ---------------------------------------------------------------------------
# Field ownership - the safety property
# ---------------------------------------------------------------------------


def test_ownership_classes_are_disjoint():
    assert not (ENGINE_OWNED_FIELDS & ENGINE_PROPOSED_FIELDS)
    assert not (ENGINE_OWNED_FIELDS & USER_OWNED_FIELDS)
    assert not (ENGINE_PROPOSED_FIELDS & USER_OWNED_FIELDS)


def test_ownership_classes_cover_every_metadata_field():
    union = ENGINE_OWNED_FIELDS | ENGINE_PROPOSED_FIELDS | USER_OWNED_FIELDS
    assert union == ALL_METADATA_FIELDS


def test_every_serialised_field_has_a_declared_owner():
    """A field that reaches metadata.yaml without an owner would be writable by
    accident. This is the check that catches a new field added to the dataclass
    but forgotten in the registry."""
    for name in make_object().to_metadata_dict():
        assert ownership_of(name) in Ownership


def test_ownership_lookup_rejects_unknown_fields():
    with pytest.raises(KeyError):
        ownership_of("not_a_real_field")


def test_engine_may_write_its_own_fields():
    assert is_engine_writable("title", set()) is True
    assert is_engine_writable("content_hash", set()) is True


def test_engine_may_never_write_user_fields():
    for name in USER_OWNED_FIELDS:
        assert is_engine_writable(name, set()) is False


def test_overrides_lock_proposed_fields_only():
    assert is_engine_writable("difficulty", set()) is True
    assert is_engine_writable("difficulty", {"difficulty"}) is False
    # Locking an engine-owned field has no effect; the engine still owns it.
    assert is_engine_writable("title", {"title"}) is True


def test_with_engine_fields_applies_permitted_updates():
    obj = make_object()
    updated = obj.with_engine_fields(title="Renamed by the source")
    assert updated.title == "Renamed by the source"
    assert obj.title != updated.title  # original untouched


def test_with_engine_fields_refuses_user_owned_writes():
    obj = make_object(notes="my notes")
    with pytest.raises(PermissionError):
        obj.with_engine_fields(notes="clobbered")
    with pytest.raises(PermissionError):
        obj.with_engine_fields(learning_status="learned")


def test_with_engine_fields_respects_overrides():
    obj = make_object(overrides=("difficulty",))
    with pytest.raises(PermissionError):
        obj.with_engine_fields(difficulty="beginner")
    # The same field is writable when not locked.
    assert make_object().with_engine_fields(difficulty="beginner").difficulty == "beginner"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_knowledge_object_survives_a_serialisation_roundtrip():
    original = make_object()
    restored = KnowledgeObject.from_metadata_dict(original.to_metadata_dict())
    assert restored == original


def test_serialised_dates_stay_as_dates():
    metadata = make_object().to_metadata_dict()
    assert metadata["published_date"] == date(2026, 4, 15)
    assert metadata["discovered_date"] == date(2026, 4, 18)


def test_quoted_dates_are_accepted_when_hand_edited():
    """A human editing metadata.yaml may quote a date; that must still load."""
    metadata = make_object().to_metadata_dict()
    metadata["published_date"] = "2026-04-15"
    metadata["discovered_date"] = "2026-04-18"
    restored = KnowledgeObject.from_metadata_dict(metadata)
    assert restored.published_date == date(2026, 4, 15)


def test_null_publication_date_is_allowed_for_inferred_dating():
    metadata = make_object(
        published_date=None, date_confidence=DateConfidence.INFERRED
    ).to_metadata_dict()
    restored = KnowledgeObject.from_metadata_dict(metadata)
    assert restored.published_date is None
    assert restored.date_confidence is DateConfidence.INFERRED


def test_untouched_generation_entries_serialise_compactly():
    """Keeps metadata.yaml readable when most artifacts were never requested."""
    entry = GenerationEntry(status=GenerationStatus.NONE).to_dict()
    assert entry == {"status": "none"}


# ---------------------------------------------------------------------------
# Revisions and staleness
# ---------------------------------------------------------------------------


def test_current_revision_reads_the_highest_recorded_revision():
    assert make_object().current_revision == 1
    assert make_object(revisions=()).current_revision == 0


def test_artifact_generated_from_an_older_revision_is_stale():
    entry = GenerationEntry(
        status=GenerationStatus.GENERATED,
        path="artifacts/tutorial.md",
        generated_at=date(2026, 4, 19),
        generated_from_revision=1,
    )
    assert entry.is_stale_against(2) is True
    assert entry.is_stale_against(1) is False


def test_ungenerated_artifacts_are_never_stale():
    for status in (GenerationStatus.NONE, GenerationStatus.REQUESTED):
        entry = GenerationEntry(status=status, generated_from_revision=1)
        assert entry.is_stale_against(5) is False


def test_stale_artifacts_are_reported_per_object():
    obj = make_object(
        revisions=(
            *make_object().revisions,
            Revision(revision=2, date=date(2026, 5, 1), summary="Source updated"),
        ),
        generation={
            ArtifactType.TUTORIAL: GenerationEntry(
                status=GenerationStatus.GENERATED,
                path="artifacts/tutorial.md",
                generated_from_revision=1,
            ),
            ArtifactType.QUIZ: GenerationEntry(
                status=GenerationStatus.GENERATED,
                path="artifacts/quiz.md",
                generated_from_revision=2,
            ),
        },
    )
    assert obj.stale_artifacts() == (ArtifactType.TUTORIAL,)


# ---------------------------------------------------------------------------
# Raw items
# ---------------------------------------------------------------------------


def test_raw_item_mints_from_publication_month_when_date_is_exact():
    item = RawItem(
        source_name="fabric-blog",
        source_url="https://example.invalid/a",
        source_authority=SourceAuthority.OFFICIAL_MICROSOFT,
        title="A feature",
        summary="Summary.",
        discovered_date=date(2026, 8, 3),
        published_date=date(2026, 4, 15),
        date_confidence=DateConfidence.EXACT,
        provenance=make_provenance(),
        identity=compute_identity(canonical_url="https://example.invalid/x"),
    )
    assert item.id_basis_date == date(2026, 4, 15)


def test_raw_item_falls_back_to_discovery_month_when_date_is_inferred():
    item = RawItem(
        source_name="fabric-blog",
        source_url="https://example.invalid/b",
        source_authority=SourceAuthority.OFFICIAL_MICROSOFT,
        title="A feature",
        summary="Summary.",
        discovered_date=date(2026, 8, 3),
        published_date=None,
        date_confidence=DateConfidence.INFERRED,
        provenance=make_provenance(),
        identity=compute_identity(canonical_url="https://example.invalid/y"),
    )
    assert item.id_basis_date == date(2026, 8, 3)


# ---------------------------------------------------------------------------
# Copy independence
#
# `with_engine_fields` documents itself as returning a copy. dataclasses.replace
# is shallow, so that promise has to be tested, not assumed.
# ---------------------------------------------------------------------------


def test_no_mutable_state_is_shared_after_an_engine_write():
    """Walks every field, so a future mutable field cannot silently alias.

    This is the guard: adding a list/dict/set field without copying it in
    `with_engine_fields` fails here rather than in production.
    """
    from dataclasses import fields

    original = make_object()
    copy = original.with_engine_fields(title="Renamed by the source")

    for f in fields(KnowledgeObject):
        before, after = getattr(original, f.name), getattr(copy, f.name)
        if isinstance(before, (dict, list, set)):
            assert before is not after, (
                f"{f.name!r} is shared mutable state; copy it in with_engine_fields"
            )


def test_mutating_a_copys_generation_does_not_touch_the_original():
    original = make_object(
        generation={ArtifactType.TUTORIAL: GenerationEntry(status=GenerationStatus.REQUESTED)}
    )
    copy = original.with_engine_fields(title="Renamed")
    copy.generation[ArtifactType.QUIZ] = GenerationEntry(status=GenerationStatus.GENERATED)

    assert ArtifactType.QUIZ in copy.generation
    assert ArtifactType.QUIZ not in original.generation


def test_an_explicit_generation_update_still_wins():
    """The defensive copy must not shadow a deliberate engine write."""
    original = make_object()
    replacement = {ArtifactType.QUIZ: GenerationEntry(status=GenerationStatus.STALE)}
    updated = original.with_engine_fields(generation=replacement)
    assert updated.generation == replacement


# ---------------------------------------------------------------------------
# Date precision - independent of confidence
# ---------------------------------------------------------------------------


def test_precision_and_confidence_are_independent():
    """A month-precise date can still be exactly known.

    The Learn "What's New" page dates updates to a month. That is an exactly
    known month, not a guess, so overloading `date_confidence` to carry
    precision would have lost real information.
    """
    obj = make_object(
        published_date=date(2026, 7, 1),
        date_precision=DatePrecision.MONTH,
        date_confidence=DateConfidence.EXACT,
    )
    assert obj.date_precision is DatePrecision.MONTH
    assert obj.date_confidence is DateConfidence.EXACT
    restored = KnowledgeObject.from_metadata_dict(obj.to_metadata_dict())
    assert restored == obj


def test_month_precision_still_mints_from_the_publication_month():
    """Month precision is exactly what ADR-0005 needs; it must not degrade IDs."""
    item = RawItem(
        source_name="learn-fabric-whats-new",
        source_url="https://example.invalid/x",
        source_authority=SourceAuthority.OFFICIAL_MICROSOFT,
        title="A feature",
        summary="Summary.",
        discovered_date=date(2026, 8, 3),
        published_date=date(2026, 7, 1),
        date_confidence=DateConfidence.EXACT,
        date_precision=DatePrecision.MONTH,
        provenance=make_provenance(),
        identity=compute_identity(canonical_url="https://example.invalid/z"),
    )
    assert item.id_basis_date == date(2026, 7, 1)


def test_date_precision_defaults_to_day_for_older_files():
    metadata = make_object().to_metadata_dict()
    del metadata["date_precision"]
    assert KnowledgeObject.from_metadata_dict(metadata).date_precision is DatePrecision.DAY


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_survives_a_roundtrip():
    original = make_provenance()
    assert Provenance.from_dict(original.to_dict()) == original


def test_provenance_travels_with_the_knowledge_object():
    obj = make_object()
    restored = KnowledgeObject.from_metadata_dict(obj.to_metadata_dict())
    assert restored.provenance.parser_version == 1
    assert restored.provenance.extraction_method is ExtractionMethod.HTML_TABLE_ROW
    assert restored.provenance.selector is not None


def test_provenance_is_engine_owned():
    """The engine must be free to correct provenance; the user never writes it."""
    assert ownership_of("provenance") is Ownership.ENGINE
    assert make_object().with_engine_fields(provenance=make_provenance(parser_version=2))


# ---------------------------------------------------------------------------
# Source health
# ---------------------------------------------------------------------------


def _attempt(**overrides):
    defaults = dict(
        source_name="fabric-whats-new",
        run_id="run-1",
        attempted_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        ok=True,
        http_status=200,
        response_ms=120,
        items_discovered=20,
    )
    defaults.update(overrides)
    return SourceAttempt(**defaults)


def test_a_successful_run_keeps_a_source_healthy():
    health = SourceHealth(source_name="fabric-whats-new").record(_attempt())
    assert health.state is HealthState.HEALTHY
    assert health.consecutive_failures == 0
    assert health.last_success_at is not None


def test_failures_accumulate_and_mark_the_source_failed():
    health = SourceHealth(source_name="s")
    for _ in range(2):
        health = health.record(_attempt(ok=False, http_status=403, failure_reason="HTTP 403"))
    assert health.state is HealthState.FAILED
    assert health.consecutive_failures == 2
    assert health.last_failure_reason == "HTTP 403"


def test_an_alert_is_raised_after_three_consecutive_failures():
    health = SourceHealth(source_name="s")
    for _ in range(2):
        health = health.record(_attempt(ok=False, failure_reason="boom"))
    assert health.needs_alert is False
    health = health.record(_attempt(ok=False, failure_reason="boom"))
    assert health.needs_alert is True


def test_no_duplicate_alert_while_one_issue_is_open():
    health = SourceHealth(source_name="s", consecutive_failures=9, open_alert_issue=42)
    assert health.needs_alert is False


def test_a_disabled_source_never_alerts_and_never_changes_state():
    health = SourceHealth(source_name="s", state=HealthState.DISABLED, disabled_reason="retired")
    after = health.record(_attempt(ok=False, failure_reason="boom"))
    assert after.state is HealthState.DISABLED
    assert after.consecutive_failures == 0
    assert after.needs_alert is False


def test_recovery_clears_the_failure_count():
    health = SourceHealth(source_name="s")
    health = health.record(_attempt(ok=False, failure_reason="boom"))
    health = health.record(_attempt())
    assert health.state is HealthState.HEALTHY
    assert health.consecutive_failures == 0


def test_using_a_fallback_marks_the_source_degraded_not_healthy():
    """Falling back is not failure, but it is not business as usual either."""
    health = SourceHealth(source_name="s").record(_attempt(role=SourceRole.SECONDARY))
    assert health.state is HealthState.DEGRADED


# ---------------------------------------------------------------------------
# Parser-break detection - the check that stops silent death
# ---------------------------------------------------------------------------


def test_a_sudden_collapse_in_item_count_is_treated_as_a_parser_break():
    """Zero items from a source that always returns twenty is not 'no news'."""
    health = SourceHealth(source_name="s", recent_item_counts=(20, 22, 19, 21))
    assert health.baseline_items == 20.5
    assert health.looks_like_parser_break(0) is True
    assert health.looks_like_parser_break(2) is True

    after = health.record(_attempt(items_discovered=0))
    assert after.state is HealthState.DEGRADED
    assert "parser break" in after.last_failure_reason


def test_a_normal_quiet_week_is_not_a_parser_break():
    health = SourceHealth(source_name="s", recent_item_counts=(20, 22, 19, 21))
    assert health.looks_like_parser_break(15) is False
    assert health.record(_attempt(items_discovered=15)).state is HealthState.HEALTHY


def test_no_baseline_means_no_opinion():
    """A new source must not be accused of breaking before it has a history."""
    health = SourceHealth(source_name="s", recent_item_counts=(20,))
    assert health.baseline_items is None
    assert health.looks_like_parser_break(0) is False


def test_history_is_bounded():
    health = SourceHealth(source_name="s")
    for index in range(40):
        health = health.record(_attempt(items_discovered=index))
    assert len(health.recent_item_counts) == SourceHealth.MAX_HISTORY


def test_source_health_survives_a_roundtrip():
    health = SourceHealth(source_name="s").record(_attempt())
    assert SourceHealth.from_dict(health.to_dict()) == health


# ---------------------------------------------------------------------------
# Knowledge Time Machine
# ---------------------------------------------------------------------------


def test_revisions_carry_snapshots_so_history_is_readable_from_the_object():
    """"How did this evolve?" must be answerable without Git or an AI model."""
    revision = Revision(
        revision=2,
        date=date(2026, 9, 14),
        changed_fields=("title", "content_hash"),
        summary="Source article retitled",
        content_hash="sha256:beef",
        title_snapshot="Direct Lake mode reaches general availability",
        summary_snapshot="Direct Lake is now GA.",
        run_id="run-9",
    )
    assert Revision.from_dict(revision.to_dict()) == revision


def test_a_revision_without_snapshots_still_loads():
    """Revision 1 written before snapshots existed must not break."""
    assert Revision.from_dict({"revision": 1, "date": date(2026, 4, 18)}).content_hash is None


def test_events_are_time_ordered_and_roundtrip():
    events = [
        KnowledgeEvent(
            occurred_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
            event_type=EventType.DISCOVERED,
            feature_id="MSF-2026-07-001",
            run_id="run-1",
            revision=1,
        ),
        KnowledgeEvent(
            occurred_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
            event_type=EventType.REVISED,
            feature_id="MSF-2026-07-001",
            run_id="run-2",
            revision=2,
            changed_fields=("title",),
        ),
    ]
    for event in events:
        assert KnowledgeEvent.from_dict(event.to_dict()) == event

    # "What changed in August 2026?" is a filter over one ordered log.
    august = [e for e in events if e.occurred_at.month == 8]
    assert [e.feature_id for e in august] == ["MSF-2026-07-001"]


def test_timestamps_are_normalised_to_utc():
    """A naive timestamp is read as UTC, never as local time."""
    event = KnowledgeEvent.from_dict({
        "occurred_at": "2026-07-05T06:00:00",
        "event_type": "discovered",
        "feature_id": "MSF-2026-07-001",
        "run_id": "run-1",
    })
    assert event.occurred_at.tzinfo is timezone.utc


# ---------------------------------------------------------------------------
# Run reporting - a failed source must never fail the run
# ---------------------------------------------------------------------------


def test_a_failed_source_does_not_fail_the_run():
    report = RunReport(
        pack="microsoft-fabric",
        run_id="run-1",
        started_at=datetime(2026, 7, 5, tzinfo=timezone.utc),
        finished_at=datetime(2026, 7, 5, 0, 1, tzinfo=timezone.utc),
        attempts=[_attempt(), _attempt(source_name="dead", ok=False, failure_reason="HTTP 403")],
        health=[
            SourceHealth(source_name="ok", state=HealthState.HEALTHY),
            SourceHealth(source_name="dead", state=HealthState.FAILED, consecutive_failures=3),
        ],
        created=["MSF-2026-07-001"],
    )
    assert report.succeeded_overall is True
    assert len(report.failed_attempts) == 1
    assert [s.source_name for s in report.by_state(HealthState.FAILED)] == ["dead"]
    assert [s.source_name for s in report.alerts_needed] == ["dead"]
