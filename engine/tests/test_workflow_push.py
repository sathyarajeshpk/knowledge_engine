"""The weekly workflow's commit-and-push step, executed for real.

Every other workflow test in this repository asserts on the *text* of the YAML
-- that permissions are least-privilege, that `ke generate` is never invoked.
Those are worth having, but they cannot catch a shell script that is wrong.

The push step is the riskiest code in M6 and the only part written in shell: it
runs unattended, it is the last thing to touch the repository, and its failure
mode is a half-published harvest. So this module extracts that step's script
from the workflow and runs it against real local Git repositories, including
the cases it exists to survive:

* somebody hand-edited their learning state while the harvest was running
* the push is rejected, repeatedly
* the harvest tried to commit something outside `domain-packs/`

The script is read from the workflow rather than copied here. A copy would
drift, and a test of a copy proves nothing about what actually runs on Sunday.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "weekly-harvest.yml"

GIT_IDENTITY = (
    "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
)


def push_script() -> str:
    """The `run:` body of the workflow's commit-and-push step."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["harvest"]["steps"]:
        if step.get("name") == "Commit and push":
            return step["run"]
    raise AssertionError("the weekly workflow has no 'Commit and push' step")


def test_the_push_step_needs_no_actions_expressions():
    """It must be runnable outside Actions, or this whole module is a fiction.

    Also a design constraint worth keeping: `${{ }}` inside a shell body is how
    untrusted input becomes command injection, which is why the interpolation
    check in `test_security.py` exists.
    """
    assert "${{" not in push_script()


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *GIT_IDENTITY, *args],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    """A bare repository standing in for `origin`."""
    path = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(path)],
                   capture_output=True, check=True)
    return path


@pytest.fixture
def clone(tmp_path: Path, remote: Path) -> Path:
    """A working clone with one harvested pack already committed."""
    path = tmp_path / "work"
    subprocess.run(["git", "clone", str(remote), str(path)],
                   capture_output=True, check=True)

    pack = path / "domain-packs" / "microsoft-fabric"
    (pack / "state").mkdir(parents=True)
    (pack / "state" / "run-log.md").write_text("# Run log\n", encoding="utf-8")
    (path / "engine").mkdir()
    (path / "engine" / "ke.py").write_text("# the engine\n", encoding="utf-8")

    git(path, "add", "-A")
    git(path, "commit", "-m", "initial")
    git(path, "push", "origin", "main")
    return path


def run_push(clone: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    """Run the real workflow script, with `sleep` stubbed out.

    The script backs off between retries. Waiting through that would make this
    module take a minute to prove something that has nothing to do with time,
    so a no-op `sleep` goes first on PATH.
    """
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    stub = fake_bin / "sleep"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)

    return subprocess.run(
        ["bash", "-c", push_script()],
        cwd=str(clone), capture_output=True, text=True,
        env={
            **os.environ,
            "GITHUB_REF_NAME": "main",
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
    )


def remote_log(remote: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline", "main"],
        cwd=str(remote), capture_output=True, text=True, check=True,
    ).stdout


def file_at_remote(remote: Path, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"main:{path}"],
        cwd=str(remote), capture_output=True, text=True,
    )
    return result.stdout if result.returncode == 0 else None


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------


def test_a_harvest_is_committed_and_pushed(clone, remote, tmp_path):
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )

    result = run_push(clone, tmp_path)

    assert result.returncode == 0, result.stderr
    assert "run-2026-08-02" in (file_at_remote(remote, "domain-packs/microsoft-fabric/state/run-log.md") or "")


def test_a_run_that_wrote_nothing_fails_loudly(clone, tmp_path):
    """An empty diff means the run log was not appended, which cannot happen.

    Exiting 0 here would be the worst outcome available: a green weekly run,
    every week, with the engine writing nothing at all. That failure is exactly
    what the 60-day cron auto-disable rule punishes, and it would be invisible.
    """
    result = run_push(clone, tmp_path)

    assert result.returncode != 0
    assert "Nothing to commit" in result.stdout


def test_the_push_never_carries_a_change_to_the_engine(clone, remote, tmp_path):
    """`git add domain-packs/` is a containment boundary, not a convenience.

    A harvest that could commit outside the pack could modify the engine that
    runs it next week, or the workflow's own permissions.
    """
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )
    (clone / "engine" / "ke.py").write_text("# tampered\n", encoding="utf-8")

    assert run_push(clone, tmp_path).returncode == 0
    assert file_at_remote(remote, "engine/ke.py") == "# the engine\n"


# ---------------------------------------------------------------------------
# Somebody edited their notes while the harvest was running
# ---------------------------------------------------------------------------


def test_a_concurrent_hand_edit_survives_the_push(clone, remote, tmp_path):
    """The case the rebase exists for, and the one most likely to happen.

    Learning state is user-owned and hand-edited. If the weekly push could
    clobber it, the field-ownership model would be worth nothing -- the engine
    would not overwrite your notes, it would simply push over them.
    """
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )

    # Meanwhile, in another checkout, the user marks something as learned.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)],
                   capture_output=True, check=True)
    notes = other / "domain-packs" / "microsoft-fabric" / "notes.md"
    notes.write_text("learning_status: learned\n", encoding="utf-8")
    git(other, "add", "-A")
    git(other, "commit", "-m", "mark as learned")
    git(other, "push", "origin", "main")

    result = run_push(clone, tmp_path)

    assert result.returncode == 0, result.stderr
    # Both survive: the harvest rebased on top of the hand-edit rather than
    # replacing it.
    assert file_at_remote(remote, "domain-packs/microsoft-fabric/notes.md") == (
        "learning_status: learned\n"
    )
    assert "run-2026-08-02" in (file_at_remote(remote, "domain-packs/microsoft-fabric/state/run-log.md") or "")


# ---------------------------------------------------------------------------
# Push failure and rollback
# ---------------------------------------------------------------------------


def reject_pushes(remote: Path) -> None:
    hook = remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'rejected by policy' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)


def test_a_rejected_push_fails_the_run_rather_than_reporting_success(
    clone, remote, tmp_path
):
    """Three attempts, then a real failure. Silence here is data loss."""
    reject_pushes(remote)
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )

    result = run_push(clone, tmp_path)

    assert result.returncode != 0
    assert "push failed after 3 attempts" in result.stdout + result.stderr


def test_a_rejected_push_leaves_the_remote_untouched(clone, remote, tmp_path):
    """Rollback: a failed harvest publishes nothing, not part of something.

    The commit exists on the runner, which is discarded. Next Sunday's harvest
    starts from the same state as this one did and rediscovers the same items --
    the pipeline is idempotent precisely so that a failed run costs nothing.
    """
    before = remote_log(remote)
    reject_pushes(remote)
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )

    assert run_push(clone, tmp_path).returncode != 0
    assert remote_log(remote) == before


def test_an_unreachable_remote_fails_before_pushing(clone, remote, tmp_path):
    """A rebase that cannot run must stop the step, not push blindly.

    Pushing without rebasing is how a concurrent hand-edit gets rejected as
    non-fast-forward -- or worse, how a retry loop is tempted into `--force`.
    """
    git(clone, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    (clone / "domain-packs" / "microsoft-fabric" / "state" / "run-log.md").write_text(
        "# Run log\n\n- run-2026-08-02\n", encoding="utf-8"
    )

    result = run_push(clone, tmp_path)

    assert result.returncode != 0
    assert "could not rebase onto origin" in result.stdout + result.stderr
    assert remote_log(remote).strip().endswith("initial")


def test_the_push_step_never_forces(clone):
    """No `--force` anywhere, at any point in the retry logic.

    A force-push from an unattended weekly job is the one operation in this
    design that could actually destroy knowledge, which CLAUDE.md forbids
    outright. Asserted on the text because its absence cannot be tested by
    running it.
    """
    script = push_script()
    for forbidden in ("--force", "-f ", "+refs/", "--hard"):
        assert forbidden not in script, forbidden
