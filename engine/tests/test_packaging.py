"""The engine as an *installed* package, not as a source tree.

Every other test in this suite runs against `engine/` on `sys.path`. That is the
right default — it is fast, and it is how development actually happens — but it
means a whole category of defect is invisible: anything that works because a
file happens to be next to the source and would not be there after
`pip install`.

M7 hit exactly that. The prompt templates lived at `engine/prompts/`, one level
above the package. Under `pip install -e .` the path resolved and all seven
loaded. In a real install they were not copied at all, `ke generate` found zero
templates, and every artifact type failed with "no prompt template" — a command
that was thoroughly tested and completely broken.

These tests build and install the package for real. They are slow by the
standards of the rest of the suite (a few seconds each) and that is the price of
testing the thing that ships rather than the thing in front of you.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def installed(tmp_path_factory) -> Path:
    """A throwaway virtualenv with the package installed non-editably.

    Non-editable is the whole point: an editable install leaves the source tree
    on the path and would reproduce the bug this module exists to prevent.
    """
    venv = tmp_path_factory.mktemp("venv") / "env"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True
    )
    python = venv / "bin" / "python"
    if not python.exists():  # pragma: no cover - Windows layout
        python = venv / "Scripts" / "python.exe"

    result = subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", str(REPO_ROOT)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"could not install the package here: {result.stderr[-400:]}")
    return python


def run(python: Path, code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True
    )


def test_the_installed_package_imports(installed):
    result = run(installed, "import ke; print(ke.__version__)")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_every_prompt_template_ships_with_the_package(installed):
    """The regression this module was written for.

    Seven templates present in the repository and zero in the install is not a
    difference a source-tree test can see.
    """
    result = run(
        installed,
        "from ke.generate import available_templates\n"
        "from ke.models import ArtifactType\n"
        "print(len(available_templates()), len(list(ArtifactType)))",
    )

    assert result.returncode == 0, result.stderr
    found, expected = result.stdout.split()
    assert found == expected, f"{found} templates installed, expected {expected}"


def test_the_prompts_directory_is_inside_the_package(installed):
    """Anything outside the package is not installed with it."""
    result = run(
        installed,
        "from ke.generate import PROMPTS_DIR\n"
        "import ke, pathlib\n"
        "print(PROMPTS_DIR.exists(), "
        "PROMPTS_DIR.is_relative_to(pathlib.Path(ke.__file__).parent))",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["True", "True"]


def test_the_console_script_is_installed_and_runs(installed):
    """`ke --version` is the smallest end-to-end check of the entry point."""
    ke = installed.parent / "ke"
    if not ke.exists():  # pragma: no cover - Windows layout
        ke = installed.parent / "ke.exe"

    result = subprocess.run([str(ke), "--version"], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "knowledge-engine" in result.stdout


def test_generate_list_works_in_an_installed_environment(installed):
    """The command that was silently broken, exercised as a user would."""
    ke = installed.parent / "ke"
    if not ke.exists():  # pragma: no cover
        ke = installed.parent / "ke.exe"

    result = subprocess.run(
        [str(ke), "generate", "list"], capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    for artifact_type in ("tutorial", "quiz", "linkedin-post", "infographic"):
        assert artifact_type in result.stdout


def test_domain_packs_are_not_part_of_the_package(installed):
    """Packs are data addressed by path, never bundled with the engine.

    If they ever were, extracting the engine into its own repository — the
    reason it is packaged from `engine/` at all — would start moving knowledge
    around with it.
    """
    result = run(
        installed,
        "import ke, pathlib\n"
        "root = pathlib.Path(ke.__file__).parent\n"
        "print(any(root.rglob('pack.yml')))",
    )

    assert result.stdout.strip() == "False"


# ---------------------------------------------------------------------------
# M8 — the second pack, cross-pack detection, and the pack trust boundary
# ---------------------------------------------------------------------------


def ke_cli(installed: Path) -> Path:
    executable = installed.parent / "ke"
    if not executable.exists():  # pragma: no cover - Windows layout
        executable = installed.parent / "ke.exe"
    return executable


def write_pack(root: Path, name: str, prefix: str, *, url: str = "") -> Path:
    """A minimal but loadable pack. Objects are not needed for these checks."""
    pack = root / "domain-packs" / name
    (pack / "state").mkdir(parents=True)
    sources = (
        f"sources:\n  - name: s\n    adapter: rss\n    url: {url}\n"
        "    authority: official-microsoft\n"
        if url
        else "sources: []\n"
    )
    (pack / "pack.yml").write_text(
        f"name: {name}\nid_prefix: {prefix}\nschema_version: 1\n"
        f"limits:\n  max_summary_words: 120\n{sources}",
        encoding="utf-8",
    )
    (pack / "state" / "id-registry.json").write_text(f'{{"prefix": "{prefix}"}}\n')
    return pack


def test_the_m8_modules_ship_with_the_package(installed):
    """`ke.crosspack` and `ke.paths` are new in M8.

    Both are ordinary modules and *should* be picked up automatically — which is
    exactly what was assumed about the prompt templates in M7, and exactly what
    turned out to be false. The assumption is cheap to check and was wrong once.
    """
    result = run(
        installed,
        "from ke.crosspack import find_duplicates, Resolutions\n"
        "from ke.paths import contained, escaping_links\n"
        "print('ok')",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_the_installed_engine_validates_the_real_two_pack_repository(installed):
    """The shipped engine against the shipped knowledge, both packs at once.

    Exercises the M8 whole-repository checks (REF001 dangling references across
    packs, XPK001 cross-pack duplicates) on 422 real objects rather than on
    fixtures — the closest thing to what the weekly workflow actually runs.
    """
    result = subprocess.run(
        [str(ke_cli(installed)), "validate", "--repo-root", str(REPO_ROOT)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert "2 pack(s)" in result.stdout
    assert "0 error(s)" in result.stdout


def test_the_installed_engine_reports_an_escaping_symlink(installed, tmp_path):
    """SEC001 end to end, through the console script.

    A guard that works in-process and is not wired into the CLI protects
    nothing: `ke validate` in CI is the only place a pack author meets it.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    write_pack(repo, "alpha", "ALF")
    write_pack(repo, "beta", "BET")
    (repo / "domain-packs" / "alpha" / "state" / "git").symlink_to(repo / ".git")

    result = subprocess.run(
        [str(ke_cli(installed)), "validate", "--repo-root", str(repo)],
        capture_output=True, text=True,
    )

    assert "SEC001" in result.stdout, result.stdout + result.stderr
    # ERROR, so the pull request that introduces the link cannot go green.
    assert result.returncode != 0


def test_the_installed_engine_refuses_a_file_url_source(installed, tmp_path):
    """S-1 end to end. A pack naming `file://` must fail validation, not fetch.

    Asserted through the CLI because that is where a pack author and CI both
    meet it — the in-process test proves the guard, this proves it is reachable.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    write_pack(repo, "alpha", "ALF", url="file:///etc/hostname")

    result = subprocess.run(
        [str(ke_cli(installed)), "validate", "--repo-root", str(repo)],
        capture_output=True, text=True,
    )

    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "file:///etc/hostname" in combined


def test_the_installed_engine_lists_cross_pack_review_items(installed, tmp_path):
    """`ke review` reaches the cross-pack provider from an installed package.

    Two empty packs have nothing to report, which is the point: the command must
    complete and say so rather than fail looking for the other pack.

    `--kind cross-pack` is asserted separately because it was **rejected by
    argparse** until this test was written. M8 added `TaskKind.CROSS_PACK`, a
    provider for it and a row for it in the rendered queue — and left the CLI's
    hard-coded choice tuple behind, so the engine produced a kind the CLI
    refused to name.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    write_pack(repo, "alpha", "ALF")
    write_pack(repo, "beta", "BET")

    result = subprocess.run(
        [str(ke_cli(installed)), "review", "list", "--repo-root", str(repo)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    filtered = subprocess.run(
        [str(ke_cli(installed)), "review", "list",
         "--kind", "cross-pack", "--repo-root", str(repo)],
        capture_output=True, text=True,
    )

    assert filtered.returncode == 0, filtered.stdout + filtered.stderr
