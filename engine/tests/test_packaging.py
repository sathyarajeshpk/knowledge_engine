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
