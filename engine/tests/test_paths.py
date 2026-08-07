"""Symlink containment: the one way a data-only pack can reach engine files.

Every test here creates a real symlink rather than mocking one. A containment
check verified against a mock proves the mock behaves as expected and nothing
about `resolve()`, which is where all the actual subtlety lives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ke.pack import Pack
from ke.paths import PathEscape, contained, ensure_contained, escaping_links, resolved
from ke.validate import Level, validate_repo
from tests.conftest import make_object


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A repository with one ordinary pack and somewhere to escape to."""
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("private\n", encoding="utf-8")
    repo_root = tmp_path / "repo"
    packs = repo_root / "domain-packs" / "good"
    (packs / "state").mkdir(parents=True)
    (packs / "pack.yml").write_text(
        "name: good\nid_prefix: GD\nschema_version: 1\n", encoding="utf-8"
    )
    (repo_root / ".git").mkdir()
    return repo_root


# ---------------------------------------------------------------------------
# contained()
# ---------------------------------------------------------------------------


def test_contained_accepts_a_path_inside(repo: Path) -> None:
    assert contained(repo / "domain-packs" / "good" / "pack.yml", repo)


def test_contained_accepts_the_root_itself(repo: Path) -> None:
    """`root` is inside `root`. Off-by-one here would reject every pack root."""
    assert contained(repo, repo)


def test_contained_rejects_a_sibling_with_a_shared_prefix(tmp_path: Path) -> None:
    """`/a/repo-backup` is not inside `/a/repo`, though the string starts with it.

    A containment check written with `str.startswith` passes every other test in
    this file and fails this one.
    """
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo-backup").mkdir()
    assert not contained(tmp_path / "repo-backup", tmp_path / "repo")


def test_contained_follows_a_symlink_out(repo: Path) -> None:
    """The case the whole module exists for."""
    link = repo / "domain-packs" / "escape"
    link.symlink_to(repo.parent / "outside")
    assert not contained(link, repo)


def test_contained_follows_a_symlinked_ancestor(repo: Path) -> None:
    """The path is engine-derived and well formed; a *component* redirects it.

    This is the one that string inspection of the path cannot catch: nothing in
    `domain-packs/good/knowledge/2026/01/x` looks wrong.
    """
    knowledge = repo / "domain-packs" / "good" / "knowledge"
    knowledge.symlink_to(repo.parent / "outside")
    assert not contained(knowledge / "2026" / "01" / "x", repo)


def test_contained_handles_a_path_that_does_not_exist_yet(repo: Path) -> None:
    """Writes are checked before the file exists, so resolution must be lax.

    `resolve(strict=True)` would raise here, and the guard would only ever work
    on files it was too late to protect.
    """
    target = repo / "domain-packs" / "good" / "knowledge" / "2026" / "01" / "new.md"
    assert not target.exists()
    assert contained(target, repo)


def test_contained_resolves_the_root_too(tmp_path: Path) -> None:
    """A checkout reached through a symlink is not an escape.

    Without resolving `root`, every file in such a checkout reports as outside
    itself -- the false positive that makes people delete the check.
    """
    real = tmp_path / "real-repo"
    (real / "domain-packs").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(real)
    assert contained(alias / "domain-packs", alias)
    assert contained(real / "domain-packs", alias)


# ---------------------------------------------------------------------------
# ensure_contained()
# ---------------------------------------------------------------------------


def test_ensure_contained_returns_the_path_unchanged(repo: Path) -> None:
    """Returns the requested path, not the resolved one.

    Resolving it would silently rewrite every path the engine stores, and object
    paths are permanent (CLAUDE.md).
    """
    wanted = repo / "domain-packs" / "good" / "pack.yml"
    assert ensure_contained(wanted, repo) == wanted


def test_ensure_contained_names_where_the_path_actually_lands(repo: Path) -> None:
    link = repo / "domain-packs" / "escape"
    link.symlink_to(repo.parent / "outside")
    with pytest.raises(PathEscape) as caught:
        ensure_contained(link / "x", repo, what="object directory")
    message = str(caught.value)
    assert "object directory" in message
    # The resolved target is the only fact that leads to the fix; without it the
    # message reads like an engine bug.
    assert str(resolved(repo.parent / "outside")) in message


# ---------------------------------------------------------------------------
# escaping_links()
# ---------------------------------------------------------------------------


def test_escaping_links_finds_a_link_out(repo: Path) -> None:
    link = repo / "domain-packs" / "good" / "state" / "elsewhere"
    link.symlink_to(repo.parent / "outside")
    found = escaping_links(repo / "domain-packs")
    assert [str(a) for a, _ in found] == [str(link)]


def test_escaping_links_ignores_a_link_that_stays_inside(repo: Path) -> None:
    """Internal links are legitimate and must not be reported."""
    (repo / "domain-packs" / "good" / "alias").symlink_to(
        repo / "domain-packs" / "good" / "state"
    )
    assert escaping_links(repo / "domain-packs") == []


def test_escaping_links_catches_a_link_into_the_repository(repo: Path) -> None:
    """`.git` is inside the repository and is exactly the target to stop.

    Checking containment against the repository root instead of `domain-packs/`
    would pass this test's setup as safe.
    """
    link = repo / "domain-packs" / "good" / "state" / "git"
    link.symlink_to(repo / ".git")
    assert [str(a) for a, _ in escaping_links(repo / "domain-packs")] == [str(link)]


def test_escaping_links_does_not_hang_on_a_cycle(repo: Path) -> None:
    """`os.walk(followlinks=False)`. With `True` this never returns."""
    inner = repo / "domain-packs" / "good" / "loop"
    inner.symlink_to(repo / "domain-packs" / "good")
    assert escaping_links(repo / "domain-packs") == []


def test_escaping_links_reports_a_link_once_not_its_contents(repo: Path) -> None:
    """A link into a large external tree is one finding, not thousands."""
    big = repo.parent / "outside" / "tree"
    (big / "a" / "b").mkdir(parents=True)
    (big / "a" / "b" / "f.txt").write_text("x", encoding="utf-8")
    (repo / "domain-packs" / "good" / "linked").symlink_to(big)
    assert len(escaping_links(repo / "domain-packs")) == 1


# ---------------------------------------------------------------------------
# The guards, in place
# ---------------------------------------------------------------------------


def test_find_roots_refuses_a_symlinked_pack_root(repo: Path) -> None:
    """A symlinked pack root would redirect state, knowledge and indexes at once."""
    outside_pack = repo.parent / "outside" / "evil"
    outside_pack.mkdir()
    (outside_pack / "pack.yml").write_text(
        "name: evil\nid_prefix: EV\nschema_version: 1\n", encoding="utf-8"
    )
    (repo / "domain-packs" / "evil").symlink_to(outside_pack)

    roots = Pack.find_roots(repo)
    assert [r.name for r in roots] == ["good"]


def test_find_roots_still_returns_ordinary_packs(repo: Path) -> None:
    """The guard must not cost the normal case, which is every pack today."""
    assert [r.name for r in Pack.find_roots(repo)] == ["good"]


def test_object_dir_refuses_a_symlinked_knowledge_tree(repo: Path) -> None:
    """The write path itself refuses, on a machine where nothing was validated."""
    from ke.store import object_dir

    pack_root = repo / "domain-packs" / "good"
    (pack_root / "knowledge").symlink_to(repo.parent / "outside")

    with pytest.raises(PathEscape):
        object_dir(pack_root / "knowledge", make_object())


def test_validate_reports_an_escaping_link_as_an_error(repo: Path) -> None:
    (repo / "domain-packs" / "good" / "state" / "git").symlink_to(repo / ".git")

    findings = validate_repo(repo)
    escapes = [f for f in findings if f.code == "SEC001"]
    assert len(escapes) == 1
    assert escapes[0].level is Level.ERROR


def test_validate_reports_the_escape_even_under_pack_filter(repo: Path) -> None:
    """A flag must not be able to switch off a security check.

    `--pack` exists to narrow noisy per-object output; letting it also hide a
    symlink would make the narrowest invocation the least safe one.
    """
    (repo / "domain-packs" / "good" / "state" / "git").symlink_to(repo / ".git")

    findings = validate_repo(repo, pack_name="good")
    assert any(f.code == "SEC001" for f in findings)


def test_validate_reports_a_symlinked_pack_root_that_find_roots_skipped(
    repo: Path,
) -> None:
    """The two guards cover each other.

    `find_roots` refuses to load it, so no per-pack check would ever see it.
    If validation did not look at `domain-packs/` directly, a symlinked pack
    root would be silently ignored — refused, but never reported.
    """
    outside_pack = repo.parent / "outside" / "evil"
    outside_pack.mkdir()
    (outside_pack / "pack.yml").write_text(
        "name: evil\nid_prefix: EV\nschema_version: 1\n", encoding="utf-8"
    )
    (repo / "domain-packs" / "evil").symlink_to(outside_pack)

    codes = [f.code for f in validate_repo(repo)]
    assert "SEC001" in codes


def test_validate_is_clean_on_a_repository_with_no_links(repo: Path) -> None:
    assert not [f for f in validate_repo(repo) if f.code == "SEC001"]
