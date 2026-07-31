"""Tests for the `ke` command line.

CI depends on the exit code, so the exit code is the thing worth testing.
"""

from __future__ import annotations

import pytest

from ke.__main__ import main

from conftest import make_object, write_object


def test_validate_exits_zero_for_a_clean_repository(populated_pack, capsys):
    repo_root = populated_pack.parent.parent
    assert main(["validate", "--repo-root", str(repo_root)]) == 0
    assert "no findings" in capsys.readouterr().out


def test_validate_exits_one_when_an_error_is_found(pack_root, capsys):
    write_object(pack_root, make_object(overrides=("title",)))
    repo_root = pack_root.parent.parent
    assert main(["validate", "--repo-root", str(repo_root)]) == 1
    assert "OWN001" in capsys.readouterr().out


def test_validate_passes_warnings_by_default_and_fails_under_strict(populated_pack, capsys):
    obj_dir = next((populated_pack / "knowledge").glob("*/*/*"))
    (obj_dir / "images").rmdir()
    repo_root = populated_pack.parent.parent

    assert main(["validate", "--repo-root", str(repo_root)]) == 0
    assert main(["validate", "--repo-root", str(repo_root), "--strict"]) == 1
    assert "OBJ005" in capsys.readouterr().out


def test_validate_can_target_a_single_pack(populated_pack, capsys):
    repo_root = populated_pack.parent.parent
    assert main(["validate", "--repo-root", str(repo_root), "--pack", "test-pack"]) == 0


def test_validate_reports_an_unknown_pack_name(populated_pack, capsys):
    repo_root = populated_pack.parent.parent
    assert main(["validate", "--repo-root", str(repo_root), "--pack", "nope"]) == 1
    assert "PACK000" in capsys.readouterr().out


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        main([])
