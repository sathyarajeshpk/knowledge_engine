"""Tests for the core data models.

The ownership tests are the important ones. Everything else in the engine
depends on the registry being a correct partition of the metadata fields.
"""

from __future__ import annotations

from datetime import date

import pytest

from ke.models import (
    ALL_METADATA_FIELDS,
    ENGINE_OWNED_FIELDS,
    ENGINE_PROPOSED_FIELDS,
    USER_OWNED_FIELDS,
    ArtifactType,
    DateConfidence,
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

from conftest import make_object

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
