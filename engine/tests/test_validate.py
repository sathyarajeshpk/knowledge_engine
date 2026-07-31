"""Tests for `ke validate`.

Each test proves that a specific guardrail actually fires. The rules in
CLAUDE.md are only real if something checks them, and a checker is only real if
something checks the checker.

Tests assert on finding *codes* rather than message wording, so messages can be
improved without breaking the suite.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import yaml

from ke.models import FeatureId, Revision
from ke.pack import Pack, PackError
from ke.validate import Level, has_errors, validate_pack, validate_repo

from conftest import make_object, write_object, write_registry


def codes(findings) -> set[str]:
    return {finding.code for finding in findings}


def check(pack_root: Path) -> list:
    return validate_pack(Pack.load(pack_root))


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_valid_pack_produces_no_findings(populated_pack):
    assert check(populated_pack) == []


def test_empty_pack_is_valid(pack_root):
    """A pack with no knowledge yet must pass; that is the state M0 ships in."""
    assert check(pack_root) == []


# ---------------------------------------------------------------------------
# Pack structure
# ---------------------------------------------------------------------------


def test_missing_pack_yml_key_is_reported(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    del config["id_prefix"]
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    assert "PACK001" in codes(check(pack_root))


def test_unsupported_pack_schema_version_is_reported(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    config["schema_version"] = 99
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    assert "PACK002" in codes(check(pack_root))


def test_malformed_id_prefix_is_reported(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    config["id_prefix"] = "test"  # must be 2-4 upper-case letters
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    assert "PACK003" in codes(check(pack_root))


def test_missing_pack_directory_is_reported(pack_root):
    (pack_root / "digests").rmdir()
    assert "PACK004" in codes(check(pack_root))


def test_unparseable_pack_yml_raises(pack_root):
    (pack_root / "pack.yml").write_text("name: [unclosed\n")
    with pytest.raises(PackError):
        Pack.load(pack_root)


# ---------------------------------------------------------------------------
# Object structure
# ---------------------------------------------------------------------------


def test_missing_metadata_is_reported(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "metadata.yaml").unlink()
    assert "OBJ002" in codes(check(populated_pack))


def test_missing_feature_document_is_reported(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "feature.md").unlink()
    assert "OBJ003" in codes(check(populated_pack))


def test_unparseable_metadata_is_reported(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "metadata.yaml").write_text("id: [unclosed\n")
    assert "OBJ004" in codes(check(populated_pack))


def test_missing_object_subdirectory_is_a_warning(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "images").rmdir()
    findings = check(populated_pack)
    assert "OBJ005" in codes(findings)
    # A missing subdirectory is recoverable, so it must not fail the build.
    assert not has_errors(findings)


def test_directory_name_must_match_id_and_slug(pack_root):
    write_object(pack_root, make_object(), directory_name="TST-2026-04-001-wrong-slug")
    write_registry(
        pack_root, {"2026-04": 1}, {"TST-2026-04-001": "2026/04/TST-2026-04-001-direct-lake-ga"}
    )
    assert "OBJ001" in codes(check(pack_root))


# ---------------------------------------------------------------------------
# Metadata schema
# ---------------------------------------------------------------------------


def test_missing_required_field_is_reported(pack_root):
    write_object(pack_root, make_object(), drop_fields=("content_hash",))
    assert "SCHEMA002" in codes(check(pack_root))


def test_unknown_field_is_a_warning(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    metadata = yaml.safe_load((obj_dir / "metadata.yaml").read_text())
    metadata["lerning_status"] = "learned"  # typo the engine would silently drop
    (obj_dir / "metadata.yaml").write_text(yaml.safe_dump(metadata))
    findings = check(populated_pack)
    assert "SCHEMA004" in codes(findings)
    assert not has_errors(findings)


def test_invalid_enum_value_is_reported(pack_root):
    write_object(pack_root, make_object(), metadata_patch={"difficulty": "impossible"})
    assert "SCHEMA003" in codes(check(pack_root))


def test_unsupported_object_schema_version_is_reported(pack_root):
    write_object(pack_root, make_object(), metadata_patch={"schema_version": 99})
    assert "SCHEMA001" in codes(check(pack_root))


def test_undeclared_category_is_a_warning(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    config["categories"] = ["data-engineering"]
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    write_object(pack_root, make_object(category="astrology"))
    write_registry(
        pack_root, {"2026-04": 1}, {"TST-2026-04-001": "2026/04/TST-2026-04-001-direct-lake-ga"}
    )
    findings = check(pack_root)
    assert "SCHEMA005" in codes(findings)
    assert not has_errors(findings)


# ---------------------------------------------------------------------------
# Feature identity
# ---------------------------------------------------------------------------


def test_wrong_pack_prefix_is_reported(pack_root):
    obj = make_object(id=FeatureId.parse("XXX-2026-04-001"))
    write_object(pack_root, obj)
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): obj.knowledge_subpath})
    assert "ID002" in codes(check(pack_root))


def test_duplicate_feature_id_is_reported(pack_root):
    """A core CLAUDE.md rule: Feature IDs must be unique."""
    first = make_object(slug="direct-lake-ga")
    second = make_object(slug="a-different-topic")  # same ID, different directory
    write_object(pack_root, first)
    write_object(pack_root, second)
    write_registry(pack_root, {"2026-04": 1}, {str(first.id): first.knowledge_subpath})
    assert "ID003" in codes(check(pack_root))


def test_object_stored_under_the_wrong_month_is_reported(pack_root):
    """Chronological order is a repository rule; the path must match the ID."""
    obj = make_object()
    write_object(pack_root, obj, knowledge_subpath="2025/01")
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): "2025/01/" + obj.directory_name})
    assert "ID004" in codes(check(pack_root))


# ---------------------------------------------------------------------------
# Field ownership
# ---------------------------------------------------------------------------


def test_overrides_may_not_lock_an_engine_owned_field(pack_root):
    write_object(pack_root, make_object(overrides=("title",)))
    assert "OWN001" in codes(check(pack_root))


def test_overrides_may_not_lock_a_user_owned_field(pack_root):
    write_object(pack_root, make_object(overrides=("notes",)))
    assert "OWN001" in codes(check(pack_root))


def test_overrides_may_not_name_an_unknown_field(pack_root):
    write_object(pack_root, make_object(overrides=("nonsense",)))
    assert "OWN002" in codes(check(pack_root))


def test_overrides_accepts_engine_proposed_fields(pack_root):
    obj = make_object(overrides=("difficulty", "tier"))
    write_object(pack_root, obj)
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): obj.knowledge_subpath})
    assert check(pack_root) == []


# ---------------------------------------------------------------------------
# feature.md consistency and the copyright guard
# ---------------------------------------------------------------------------


def test_missing_heading_is_reported(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "feature.md").write_text("No heading here.\n")
    assert "CONS001" in codes(check(populated_pack))


def test_heading_must_match_metadata_title(pack_root):
    obj = make_object()
    write_object(pack_root, obj, feature_title="Something else entirely")
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): obj.knowledge_subpath})
    assert "CONS002" in codes(check(pack_root))


def test_oversized_summary_is_reported(pack_root):
    """The copyright guard: store a short summary and a link, not the article."""
    obj = make_object()
    write_object(pack_root, obj, body=" ".join(["word"] * 500))
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): obj.knowledge_subpath})
    assert "COPY001" in codes(check(pack_root))


def test_summary_limit_comes_from_pack_config(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    config["limits"]["max_summary_words"] = 5
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    obj = make_object()
    write_object(pack_root, obj)
    write_registry(pack_root, {"2026-04": 1}, {str(obj.id): obj.knowledge_subpath})
    assert "COPY001" in codes(check(pack_root))


# ---------------------------------------------------------------------------
# ID registry integrity
# ---------------------------------------------------------------------------


def test_missing_registry_is_reported(populated_pack):
    (populated_pack / "state" / "id-registry.json").unlink()
    assert "REG001" in codes(check(populated_pack))


def test_unparseable_registry_is_reported(populated_pack):
    (populated_pack / "state" / "id-registry.json").write_text("{not json")
    assert "REG001" in codes(check(populated_pack))


def test_counter_below_the_highest_used_sequence_is_reported(populated_pack):
    """The failure this prevents: the next mint reusing an existing ID."""
    write_registry(
        populated_pack,
        counters={"2026-04": 0},
        paths={"TST-2026-04-001": "2026/04/TST-2026-04-001-direct-lake-ga"},
    )
    assert "REG002" in codes(check(populated_pack))


def test_missing_counter_for_a_used_month_is_reported(populated_pack):
    write_registry(
        populated_pack,
        counters={},
        paths={"TST-2026-04-001": "2026/04/TST-2026-04-001-direct-lake-ga"},
    )
    assert "REG002" in codes(check(populated_pack))


def test_unregistered_object_is_reported(populated_pack):
    write_registry(populated_pack, counters={"2026-04": 1}, paths={})
    assert "REG003" in codes(check(populated_pack))


def test_registry_path_mismatch_is_reported(populated_pack):
    write_registry(
        populated_pack,
        counters={"2026-04": 1},
        paths={"TST-2026-04-001": "2026/04/somewhere-else"},
    )
    assert "REG004" in codes(check(populated_pack))


def test_registered_object_that_no_longer_exists_is_reported(populated_pack):
    """Objects are never deleted, so a dangling entry means something was lost."""
    registry = json.loads((populated_pack / "state" / "id-registry.json").read_text())
    registry["paths"]["TST-2026-04-009"] = "2026/04/TST-2026-04-009-vanished"
    (populated_pack / "state" / "id-registry.json").write_text(json.dumps(registry))
    assert "REG005" in codes(check(populated_pack))


def test_a_counter_ahead_of_disk_is_allowed(populated_pack):
    """IDs are never reused, so a counter running ahead is normal and safe."""
    write_registry(
        populated_pack,
        counters={"2026-04": 50},
        paths={"TST-2026-04-001": "2026/04/TST-2026-04-001-direct-lake-ga"},
    )
    assert check(populated_pack) == []


# ---------------------------------------------------------------------------
# Multi-object and multi-pack behaviour
# ---------------------------------------------------------------------------


def test_per_month_counters_are_independent(pack_root):
    """Backfilling an old month must not disturb the current month."""
    april = make_object()
    november = make_object(
        id=FeatureId.parse("TST-2025-11-004"),
        slug="older-topic",
        published_date=date(2025, 11, 2),
        discovered_date=date(2025, 11, 5),
        revisions=(Revision(revision=1, date=date(2025, 11, 5), summary="Initial ingestion"),),
    )
    write_object(pack_root, april)
    write_object(pack_root, november)
    write_registry(
        pack_root,
        counters={"2026-04": 1, "2025-11": 4},
        paths={
            str(april.id): april.knowledge_subpath,
            str(november.id): november.knowledge_subpath,
        },
    )
    assert check(pack_root) == []


def test_validate_repo_scans_every_pack(populated_pack):
    repo_root = populated_pack.parent.parent
    assert validate_repo(repo_root) == []


def test_validate_repo_reports_an_unknown_pack_name(populated_pack):
    repo_root = populated_pack.parent.parent
    assert "PACK000" in codes(validate_repo(repo_root, pack_name="no-such-pack"))


# ---------------------------------------------------------------------------
# Severity handling
# ---------------------------------------------------------------------------


def test_strict_mode_turns_warnings_into_failures(populated_pack):
    obj_dir = next(Pack.load(populated_pack).iter_object_dirs())
    (obj_dir / "images").rmdir()
    findings = check(populated_pack)
    assert not has_errors(findings)
    assert has_errors(findings, strict=True)


def test_strict_mode_still_passes_a_clean_pack(populated_pack):
    assert not has_errors(check(populated_pack), strict=True)


def test_findings_render_readably(pack_root):
    write_object(pack_root, make_object(overrides=("title",)))
    finding = next(f for f in check(pack_root) if f.code == "OWN001")
    rendered = str(finding)
    assert "ERROR" in rendered
    assert "OWN001" in rendered
    assert finding.level is Level.ERROR
