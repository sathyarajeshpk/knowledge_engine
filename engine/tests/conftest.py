"""Shared test fixtures.

These helpers write knowledge objects to disk by hand. That is deliberate for
M0: `ke.store` does not exist until M2, and `ke.validate` must be tested against
real files rather than against the writer that produced them. Testing a checker
with its own writer would hide exactly the class of bug the checker exists to
find.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from ke.acquisition.identity import IdentityBasis
from ke.models import (
    AdapterType,
    ArtifactType,
    DateConfidence,
    Difficulty,
    FeatureId,
    GenerationEntry,
    GenerationStatus,
    KnowledgeObject,
    LearningPriority,
    DatePrecision,
    ExtractionMethod,
    LearningStatus,
    ObjectStatus,
    Provenance,
    Revision,
    SourceAuthority,
    SourceRepresentation,
    Tier,
    Workload,
)

#: A minimal pack.yml good enough for every M0 check.
PACK_CONFIG: dict[str, Any] = {
    "name": "test-pack",
    "display_name": "Test Pack",
    "id_prefix": "TST",
    "schema_version": 1,
    "limits": {"max_summary_words": 120},
    "sources": [],
}


def make_provenance(**overrides: Any) -> Provenance:
    """Build a valid `Provenance` record."""
    defaults: dict[str, Any] = {
        "source_name": "fabric-blog",
        "source_representation": SourceRepresentation.HTML,
        "adapter_type": AdapterType.HTML,
        "discovered_at": datetime(2026, 4, 18, 6, 0, tzinfo=timezone.utc),
        "extraction_method": ExtractionMethod.HTML_TABLE_ROW,
        "parser_version": 1,
        "selector": "h2#generally-available-features + table tr",
        "run_id": "run-2026-04-18T06-00-00Z",
        "identity_basis": IdentityBasis.CANONICAL_URL,
        "identity_key": "sha256:" + "c" * 64,
    }
    defaults.update(overrides)
    return Provenance(**defaults)


def make_object(**overrides: Any) -> KnowledgeObject:
    """Build a valid `KnowledgeObject`, with any field overridden."""
    defaults: dict[str, Any] = {
        "id": FeatureId.parse("TST-2026-04-001"),
        "slug": "direct-lake-ga",
        "title": "Direct Lake mode reaches general availability",
        "source_name": "fabric-blog",
        "source_url": "https://example.invalid/direct-lake-ga",
        "source_authority": SourceAuthority.OFFICIAL_MICROSOFT,
        "published_date": date(2026, 4, 15),
        "discovered_date": date(2026, 4, 18),
        "date_confidence": DateConfidence.EXACT,
        "date_precision": DatePrecision.DAY,
        "provenance": make_provenance(),
        "content_hash": "sha256:" + "a" * 64,
        "url_hash": "sha256:" + "b" * 64,
        "tier": Tier.ACT_NOW,
        "learning_priority": LearningPriority.HIGH,
        "category": "data-engineering",
        "tags": ("direct-lake", "semantic-model"),
        "difficulty": Difficulty.INTERMEDIATE,
        "workload": Workload.MODERATE,
        "version": "2026 Release Wave 1",
        "reading_time": 4,
        "learning_status": LearningStatus.NOT_STARTED,
        "status": ObjectStatus.ACTIVE,
        "revisions": (
            Revision(revision=1, date=date(2026, 4, 18), summary="Initial ingestion"),
        ),
        "generation": {
            ArtifactType.TUTORIAL: GenerationEntry(status=GenerationStatus.NONE)
        },
    }
    defaults.update(overrides)
    return KnowledgeObject(**defaults)


def write_object(
    pack_root: Path,
    obj: KnowledgeObject,
    *,
    directory_name: str | None = None,
    feature_title: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
    drop_fields: tuple[str, ...] = (),
    knowledge_subpath: str | None = None,
    body: str | None = None,
) -> Path:
    """Write a knowledge object into `pack_root`, optionally corrupted.

    The `directory_name`, `metadata_patch` and `drop_fields` hooks exist so a
    test can produce a *deliberately broken* object without going through any
    engine code path that would refuse to create it.
    """
    metadata = obj.to_metadata_dict()
    for name in drop_fields:
        metadata.pop(name, None)
    if metadata_patch:
        metadata.update(metadata_patch)

    obj_dir = (
        pack_root
        / "knowledge"
        / (knowledge_subpath or obj.id.knowledge_subpath)
        / (directory_name or obj.directory_name)
    )
    # No `artifacts/`, `images/` or `references/`: they are created on demand
    # (ADR-0015). Creating them here would let tests pass against a state Git
    # cannot actually reproduce after a clone.
    obj_dir.mkdir(parents=True, exist_ok=True)

    (obj_dir / "metadata.yaml").write_text(
        yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (obj_dir / "feature.md").write_text(
        f"# {feature_title or obj.title}\n\n"
        + (
            body
            or "Direct Lake mode is now generally available for production workloads.\n"
        )
        + f"\nSource: {obj.source_url}\n",
        encoding="utf-8",
    )
    return obj_dir


def write_registry(pack_root: Path, counters: dict[str, int], paths: dict[str, str]) -> None:
    """Write `state/id-registry.json`."""
    state = pack_root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "id-registry.json").write_text(
        json.dumps({"counters": counters, "paths": paths}, indent=2) + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def pack_root(tmp_path: Path) -> Path:
    """An empty but structurally valid domain pack."""
    root = tmp_path / "domain-packs" / "test-pack"
    # Only `state/` up front; the rest are created on demand (ADR-0015).
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "pack.yml").write_text(
        yaml.safe_dump(PACK_CONFIG, sort_keys=False), encoding="utf-8"
    )
    write_registry(root, counters={}, paths={})
    (root / "state" / "seen.json").write_text('{"urls": {}, "content": {}}\n', encoding="utf-8")
    (root / "state" / "run-log.md").write_text("# Run log\n", encoding="utf-8")
    return root


@pytest.fixture
def populated_pack(pack_root: Path) -> Path:
    """A pack containing one valid knowledge object."""
    obj = make_object()
    write_object(pack_root, obj)
    write_registry(
        pack_root,
        counters={obj.id.month_key: obj.id.sequence},
        paths={str(obj.id): obj.knowledge_subpath},
    )
    return pack_root


def make_pack(packs_dir: Path, name: str, prefix: str) -> Path:
    """Create an additional empty pack alongside an existing one."""
    root = packs_dir / name
    (root / "state").mkdir(parents=True, exist_ok=True)
    (root / "pack.yml").write_text(
        yaml.safe_dump({**PACK_CONFIG, "name": name, "id_prefix": prefix}, sort_keys=False),
        encoding="utf-8",
    )
    write_registry(root, counters={}, paths={})
    return root


@pytest.fixture
def two_packs(pack_root: Path) -> tuple[Path, Path]:
    """Two packs side by side.

    Three of the four defects found in the M0 architecture review were
    invisible with a single pack, so multi-pack coverage is now a standing
    fixture rather than something remembered at M8.
    """
    return pack_root, make_pack(pack_root.parent, "other-pack", "OTH")
