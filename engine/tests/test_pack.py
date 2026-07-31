"""Tests for Domain Pack loading.

`Pack.discover` is load-bearing: the weekly workflow iterates over whatever it
returns, so adding a pack must be a matter of creating a directory.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ke.models import FeatureId
from ke.pack import DEFAULT_MAX_SUMMARY_WORDS, Pack, find_repo_root

from conftest import PACK_CONFIG, make_object, write_object


def test_discover_finds_every_pack_sorted(pack_root):
    packs_dir = pack_root.parent
    second = packs_dir / "another-pack"
    second.mkdir()
    (second / "pack.yml").write_text(
        yaml.safe_dump({**PACK_CONFIG, "name": "another-pack", "id_prefix": "ANP"})
    )

    found = Pack.discover(packs_dir.parent)
    assert [pack.name for pack in found] == ["another-pack", "test-pack"]


def test_discover_ignores_directories_without_a_pack_yml(pack_root):
    (pack_root.parent / "not-a-pack").mkdir()
    assert [pack.name for pack in Pack.discover(pack_root.parent.parent)] == ["test-pack"]


def test_discover_returns_nothing_when_there_are_no_packs(tmp_path):
    assert Pack.discover(tmp_path) == []


def test_iter_object_dirs_yields_objects_in_id_order(pack_root):
    for slug, feature_id in (
        ("newer", "TST-2026-04-002"),
        ("older", "TST-2025-11-001"),
    ):
        write_object(pack_root, make_object(id=FeatureId.parse(feature_id), slug=slug))

    names = [d.name for d in Pack.load(pack_root).iter_object_dirs()]
    assert names == ["TST-2025-11-001-older", "TST-2026-04-002-newer"]


def test_iter_object_dirs_yields_malformed_objects_too(pack_root):
    """A directory missing its metadata.yaml must be reported, not skipped."""
    broken = pack_root / "knowledge" / "2026" / "04" / "TST-2026-04-003-broken"
    broken.mkdir(parents=True)
    assert broken in list(Pack.load(pack_root).iter_object_dirs())


def test_summary_limit_falls_back_to_a_default(pack_root):
    config = yaml.safe_load((pack_root / "pack.yml").read_text())
    del config["limits"]
    (pack_root / "pack.yml").write_text(yaml.safe_dump(config))
    assert Pack.load(pack_root).max_summary_words == DEFAULT_MAX_SUMMARY_WORDS


def test_paths_are_derived_from_the_pack_root(pack_root):
    pack = Pack.load(pack_root)
    assert pack.knowledge_dir == pack_root / "knowledge"
    assert pack.registry_path == pack_root / "state" / "id-registry.json"
    assert pack.run_log_path == pack_root / "state" / "run-log.md"


def test_find_repo_root_walks_up_to_the_packs_directory(pack_root):
    repo_root = pack_root.parent.parent
    deep = pack_root / "knowledge" / "2026" / "04"
    deep.mkdir(parents=True, exist_ok=True)
    assert find_repo_root(deep) == repo_root.resolve()


def test_relative_renders_paths_against_the_pack_root(pack_root):
    pack = Pack.load(pack_root)
    assert pack.relative(pack_root / "state" / "run-log.md") == str(
        Path("state") / "run-log.md"
    )
