"""Security regression tests.

M6 is the first milestone where the engine runs **unattended**, with
credentials, network access and write permission to the repository. That
combination is what turns ordinary bugs into security problems, so these tests
cover the seven areas the maintainer asked for explicitly plus the standing
threat model in `docs/reviews/M6_SECURITY_REVIEW.md`.

The organising question throughout: **what can a hostile source do?** Everything
the engine ingests comes from the public internet — page titles, summaries,
URLs, dates — and none of it is trustworthy just because Microsoft published it.
A compromised CDN, a hijacked blog or a typo'd URL all deliver the same thing:
attacker-controlled text arriving in a process with a write token.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.harvest import harvest_pack
from ke.lock import STALE_AFTER_SECONDS, LockError, pack_lock
from ke.models import FeatureId
from ke.normalize import canonical_url, slugify
from ke.notify import Notification, notify_all, redact
from ke.pack import Pack
from ke.store import build_object, write_object

from tests.test_pipeline import CLOCK, make_item

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


@pytest.fixture
def pack(tmp_path) -> Pack:
    root = tmp_path / "domain-packs" / "test-pack"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')
    return Pack.load(root)


def run(pack, items, monkeypatch, **kwargs):
    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=list(items))
    )
    return harvest_pack(pack, clock=CLOCK, **kwargs)


# ---------------------------------------------------------------------------
# Secret leakage prevention
# ---------------------------------------------------------------------------


def test_known_secret_values_are_redacted(monkeypatch):
    monkeypatch.setenv("KE_SMTP_PASSWORD", "hunter2-very-secret")
    assert "hunter2-very-secret" not in redact("login failed: hunter2-very-secret")


def test_unknown_credentials_are_redacted_by_pattern():
    """Pattern-based, not value-based: a secret the engine was never told about
    — echoed back by a remote server, say — must still be caught."""
    for leaky in (
        "auth failed for ghp_abcdefghijklmnop0123456789",
        "connecting to smtp://alice:s3cr3tpassword@mail.example.com",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefg",
        "token xoxb-1234567890-abcdefghijkl",
        "key AKIAIOSFODNN7EXAMPLE",
    ):
        cleaned = redact(leaky)
        assert "[redacted]" in cleaned, leaky
        for fragment in ("s3cr3tpassword", "ghp_abcdefghijklmnop", "xoxb-1234567890"):
            assert fragment not in cleaned


def test_a_notifier_exception_cannot_leak_its_connection_string():
    """The realistic leak: SMTP libraries put the URL in the exception."""

    class Leaky:
        name = "leaky"

        def send(self, notification):
            raise RuntimeError("smtp://alice:hunter2000@mail.example.com refused")

    _, failures = notify_all([Leaky()], Notification("s", "b", "p"))
    assert failures and "hunter2000" not in failures[0]


def test_a_short_secret_is_not_used_for_redaction(monkeypatch):
    """Redacting a two-character value would mangle unrelated text for no gain."""
    monkeypatch.setenv("KE_SMTP_USER", "ab")
    assert redact("a fabric feature about abstraction") == (
        "a fabric feature about abstraction"
    )


def test_no_secret_reaches_a_stored_object(pack, monkeypatch):
    """The pack is committed to Git. Nothing from the environment may reach it."""
    monkeypatch.setenv("KE_SMTP_PASSWORD", "supersecret-value-here")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_tokenvaluethatmustnotleak123")
    run(pack, [make_item()], monkeypatch)

    for path in pack.root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert "supersecret-value-here" not in text
            assert "ghp_tokenvaluethatmustnotleak123" not in text


def test_the_smtp_recipient_is_masked_in_confirmations():
    """The confirmation goes into a run log that may be public."""
    from ke.notify.smtp_email import _mask

    assert _mask("someone@example.com") == "s***@example.com"
    assert "@" not in _mask("malformed")


# ---------------------------------------------------------------------------
# File system safety — the highest-severity area
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_title",
    [
        "../../../../etc/passwd",
        "..\\..\\windows\\system32",
        "/etc/shadow",
        "a" * 500,
        "....//....//escape",
        "~/.ssh/authorized_keys",
        "feature\x00truncated",
        "con",           # reserved on Windows
        "  ..  ..  ",
    ],
)
def test_a_hostile_title_cannot_escape_the_pack(pack, hostile_title, monkeypatch):
    """A source controls titles, and titles become directory names.

    This is the highest-severity path in the engine: a title that survived into
    a filesystem path unsanitised would let a source write anywhere the process
    can reach — with a repository write token in the environment.
    """
    item = make_item(title=hostile_title)
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(
        pack.knowledge_dir, obj, item.summary, max_summary_words=120
    )

    resolved = directory.resolve()
    assert pack.knowledge_dir.resolve() in resolved.parents
    assert ".." not in str(resolved)
    assert "\x00" not in str(resolved)


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "..\\..\\x", "a/b/c", "\x00null", "/absolute", "."],
)
def test_slugify_never_produces_a_path_separator(hostile):
    slug = slugify(hostile)
    assert "/" not in slug and "\\" not in slug
    assert "\x00" not in slug
    assert slug not in ("", ".", "..")


def test_a_harvest_writes_nothing_outside_the_pack(pack, monkeypatch, tmp_path):
    """Whole-pipeline containment, not just the storage function."""
    outside = tmp_path / "outside-canary.txt"
    outside.write_text("untouched", encoding="utf-8")

    run(pack, [make_item(title="../../../escape attempt")], monkeypatch)

    assert outside.read_text() == "untouched"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert pack.root.resolve() in path.resolve().parents or path == outside


# ---------------------------------------------------------------------------
# Input validation — everything ingested is attacker-controlled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile_url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,<script>alert(1)</script>",
        "http://example.invalid/\r\nInjected-Header: yes",
        "https://example.invalid/" + "a" * 4000,
    ],
)
def test_a_hostile_url_is_normalised_without_crashing(hostile_url):
    """A URL from a source must never crash the harvest or smuggle a newline."""
    result = canonical_url(hostile_url)
    assert "\r" not in result and "\n" not in result


def test_markdown_in_a_title_cannot_forge_the_stored_document(pack):
    """A title containing Markdown must not be able to fake structure.

    A source that could inject a `# ` heading could make the stored article
    claim to be a different feature than its metadata says — and `ke validate`
    compares the two, so this is caught, but the check belongs here too.
    """
    hostile = "Real title\n# Forged heading\n\nInjected body"
    item = make_item(title=hostile)
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(pack.knowledge_dir, obj, item.summary, max_summary_words=120)

    text = (directory / "feature.md").read_text(encoding="utf-8")
    assert len(re.findall(r"^# ", text, re.MULTILINE)) == 1


def test_yaml_from_a_source_is_never_executed(pack, monkeypatch):
    """`safe_load` everywhere. A `!!python/object` tag must not construct anything."""
    run(pack, [make_item()], monkeypatch)
    path = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0]
    path.write_text(
        "!!python/object/apply:os.system ['echo pwned']\n", encoding="utf-8"
    )
    from ke.store import load_object

    assert load_object(path.parent) is None  # refused, not executed


def test_every_yaml_load_in_the_engine_is_safe():
    """Enforced by scanning, because one `yaml.load` is enough."""
    offenders = []
    for path in (REPO_ROOT / "engine" / "ke").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"yaml\.(\w+)", text):
            # `YAMLError` is the exception type, not a loader — catching it is
            # how a malformed file is refused rather than crashing the run.
            if match.group(1) not in (
                "safe_load", "safe_dump", "dump", "SafeDumper", "YAMLError",
            ):
                offenders.append(f"{path.name}: yaml.{match.group(1)}")
    assert not offenders, offenders


def test_an_enormous_summary_cannot_exhaust_the_repository(pack, monkeypatch):
    """ADR-0003's copyright limit is also a resource limit."""
    run(pack, [make_item(summary="word " * 200_000)], monkeypatch)
    for path in pack.knowledge_dir.rglob("feature.md"):
        assert len(path.read_text(encoding="utf-8").split()) <= 130


# ---------------------------------------------------------------------------
# Concurrent harvest protection
# ---------------------------------------------------------------------------


def test_two_harvests_cannot_run_at_once(pack):
    """A duplicate Feature ID is permanent, so detection alone is not enough."""
    with pack_lock(pack.state_dir, holder="first"):
        with pytest.raises(LockError):
            with pack_lock(pack.state_dir, holder="second"):
                pass


def test_the_lock_is_released_even_when_the_harvest_raises(pack):
    """A crash must not disable the pack until the staleness timeout."""
    with pytest.raises(RuntimeError):
        with pack_lock(pack.state_dir):
            raise RuntimeError("harvest blew up")
    with pack_lock(pack.state_dir):
        pass  # acquired again


def test_a_stale_lock_is_reclaimed(pack):
    """A crashed run must not silently disable the weekly harvest forever."""
    import json
    import time

    lock_path = pack.state_dir / ".harvest.lock"
    pack.state_dir.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps({"acquired_at": time.time() - STALE_AFTER_SECONDS - 60})
    )
    with pack_lock(pack.state_dir):
        pass


def test_an_unreadable_lock_is_reclaimed(pack):
    """Nothing can prove a corrupt lock is live; respecting it forever is worse."""
    pack.state_dir.mkdir(parents=True, exist_ok=True)
    (pack.state_dir / ".harvest.lock").write_text("{ corrupt")
    with pack_lock(pack.state_dir):
        pass


def test_a_fresh_lock_is_respected(pack):
    import json
    import time

    pack.state_dir.mkdir(parents=True, exist_ok=True)
    (pack.state_dir / ".harvest.lock").write_text(
        json.dumps({"acquired_at": time.time()})
    )
    with pytest.raises(LockError):
        with pack_lock(pack.state_dir):
            pass


# ---------------------------------------------------------------------------
# Interrupted writes, rollback and corrupted state recovery
# ---------------------------------------------------------------------------


def test_an_interrupted_object_write_leaves_nothing(pack, monkeypatch):
    """Regression from M2: 222 orphaned articles with no metadata."""
    import ke.store as store_module

    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    monkeypatch.setattr(
        store_module, "render_metadata",
        lambda _o: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        write_object(pack.knowledge_dir, obj, item.summary, max_summary_words=120)
    assert not list(pack.knowledge_dir.rglob("feature.md"))


def test_a_crash_mid_harvest_leaves_an_id_gap_not_a_dangling_id(pack, monkeypatch):
    """Fail in the recoverable direction (ADR-0031).

    An ID recorded with no object is a permanent hole. An object with no
    registry entry, or a skipped number, is repairable.
    """
    import ke.pipeline as pl

    calls = {"n": 0}
    real = pl.write_object

    def failing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(pl, "write_object", failing)
    report = run(
        pack,
        [make_item(title=f"Feature number {n}") for n in ("one", "two", "three")],
        monkeypatch,
    )

    assert report.errors, "the failure should be recorded"
    import json

    registry = json.loads(pack.registry_path.read_text())
    for feature_id in registry["paths"]:
        subpath = registry["paths"][feature_id]
        assert (pack.knowledge_dir / subpath / "metadata.yaml").exists(), (
            f"{feature_id} is registered but has no object"
        )


def test_a_corrupt_registry_stops_the_harvest_rather_than_reusing_ids(pack, monkeypatch):
    """The one state file where continuing would be unrecoverable."""
    pack.registry_path.write_text(
        '{"prefix": "TST", "counters": {"2026-07": 1}, '
        '"paths": {"TST-2026-07-099": "2026/07/x"}}'
    )
    report = run(pack, [make_item()], monkeypatch)
    assert report.errors and report.minted == []


def test_a_corrupt_dedup_cache_does_not_stop_the_harvest(pack, monkeypatch):
    """The rebuildable one degrades instead (ADR-0032)."""
    pack.seen_path.parent.mkdir(parents=True, exist_ok=True)
    pack.seen_path.write_text("{ corrupt")
    report = run(pack, [make_item()], monkeypatch)
    assert len(report.minted) == 1


def test_an_unreadable_object_does_not_stop_the_harvest(pack, monkeypatch):
    """One damaged object must not cost the whole run; validation reports it."""
    run(pack, [make_item(title="Good one")], monkeypatch)
    path = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0]
    path.write_text("{{{ not yaml", encoding="utf-8")

    report = run(pack, [make_item(title="Another good two")], monkeypatch)
    assert len(report.minted) == 1


# ---------------------------------------------------------------------------
# Notification safety — must never fail the run
# ---------------------------------------------------------------------------


def test_a_failing_notifier_cannot_fail_the_harvest(pack, monkeypatch):
    """Knowledge is already committed. A dead SMTP server is an inconvenience."""

    class AlwaysFails:
        name = "always-fails"

        def send(self, notification):
            raise ConnectionError("no route to host")

    delivered, failures = notify_all([AlwaysFails()], Notification("s", "b", "p"))
    assert delivered == [] and len(failures) == 1


def test_notifiers_are_skipped_when_unconfigured(monkeypatch):
    """Absent configuration is not an error — no weekly failure for unused email."""
    from ke.notify.github_issue import GitHubIssueNotifier
    from ke.notify.smtp_email import SmtpNotifier

    for name in ("GITHUB_REPOSITORY", "GITHUB_TOKEN", "GH_TOKEN",
                 "KE_SMTP_HOST", "KE_SMTP_USER", "KE_SMTP_PASSWORD", "KE_SMTP_TO"):
        monkeypatch.delenv(name, raising=False)
    assert GitHubIssueNotifier.from_environment() is None
    assert SmtpNotifier.from_environment() is None


def test_notification_is_off_by_default(pack, monkeypatch):
    """No test or manual run may send anything without asking."""
    report = run(pack, [make_item()], monkeypatch)
    assert report.notifications == []


# ---------------------------------------------------------------------------
# GitHub Actions and supply chain
# ---------------------------------------------------------------------------


def _workflows() -> list[tuple[str, dict]]:
    return [
        (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(WORKFLOWS.glob("*.yml"))
    ]


def test_every_workflow_declares_least_privilege():
    """An undeclared `permissions` block inherits write-all on many repos."""
    for name, data in _workflows():
        assert "permissions" in data, f"{name} declares no permissions"


def test_only_the_harvest_can_write():
    for name, data in _workflows():
        perms = data.get("permissions", {})
        if name == "weekly-harvest.yml":
            assert perms.get("contents") == "write"
            assert perms.get("issues") == "write"
            assert set(perms) == {"contents", "issues"}, "grant nothing further"
        else:
            assert perms.get("contents") == "read", f"{name} should be read-only"


def test_the_harvest_cannot_be_run_concurrently_with_itself():
    data = dict(_workflows())["weekly-harvest.yml"]
    assert data.get("concurrency", {}).get("group")
    # Cancelling a half-written harvest is worse than queueing behind it.
    assert data["concurrency"].get("cancel-in-progress") is False


def test_the_harvest_only_commits_pack_data():
    """A harvest must never be able to commit engine code or a workflow."""
    text = (WORKFLOWS / "weekly-harvest.yml").read_text(encoding="utf-8")
    assert "git add domain-packs/" in text
    assert "git add ." not in text and "git add -A" not in text


def test_secrets_are_not_interpolated_into_shell_commands():
    """`${{ secrets.X }}` inside `run:` is shell injection; use `env:`."""
    for path in WORKFLOWS.glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for block in re.findall(r"run:\s*\|?(.*?)(?=\n      - |\n\njobs|\Z)", text, re.S):
            assert "secrets." not in block, f"{path.name} interpolates a secret into run:"


def test_actions_are_pinned_to_a_major_version():
    """`@main` would execute whatever the action's author pushes next."""
    for path in WORKFLOWS.glob("*.yml"):
        for ref in re.findall(r"uses:\s*(\S+)", path.read_text(encoding="utf-8")):
            assert "@" in ref, ref
            version = ref.split("@")[1]
            assert version not in ("main", "master", "latest"), ref


def test_the_scheduled_pipeline_never_invokes_a_model():
    """ADR-0004, enforced rather than trusted."""
    text = (WORKFLOWS / "weekly-harvest.yml").read_text(encoding="utf-8")
    assert "ke generate" not in text
    for word in ("openai", "anthropic", "claude", "gpt"):
        assert word not in text.lower()


def test_the_runtime_dependency_surface_stays_small():
    """Every dependency is attack surface in a process holding a write token."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    block = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    declared = re.findall(r'"([A-Za-z0-9_.\-]+)', block)
    assert set(declared) <= {"PyYAML", "feedparser"}, declared


def test_the_engine_makes_no_network_call_outside_the_fetcher():
    """One egress point, so it can be reviewed, injected and tested offline."""
    engine_root = REPO_ROOT / "engine" / "ke"
    #: Compared as paths, not bare filenames — `base.py` exists in both
    #: `sources/` and `notify/`, and matching on the name alone would have
    #: exempted the wrong one.
    allowed = {
        Path("acquisition/sources/base.py"),
        Path("notify/github_issue.py"),
        Path("notify/smtp_email.py"),
    }
    offenders = []
    for path in engine_root.rglob("*.py"):
        relative = path.relative_to(engine_root)
        if relative in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        for marker in ("urlopen", "requests.", "http.client", "socket."):
            if marker in text:
                offenders.append(f"{relative}: {marker}")
    assert not offenders, offenders


def test_no_shell_execution_anywhere_in_the_engine():
    """The engine never shells out, so command injection has no entry point."""
    offenders = []
    for path in (REPO_ROOT / "engine" / "ke").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in ("subprocess.", "os.system", "os.popen", "eval(", "exec("):
            if marker in text:
                offenders.append(f"{path.name}: {marker}")
    assert not offenders, offenders


# ---------------------------------------------------------------------------
# Architecture boundary
# ---------------------------------------------------------------------------


def test_no_credential_is_read_outside_the_notifiers():
    """Secrets have exactly one blast radius."""
    offenders = []
    for path in (REPO_ROOT / "engine" / "ke").rglob("*.py"):
        if "notify" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "os.environ" in text or "getenv" in text:
            offenders.append(path.name)
    assert not offenders, f"credentials read outside notify/: {offenders}"


def test_the_cli_never_writes_outside_a_pack(tmp_path):
    """A wrong `--repo-root` must fail, not write somewhere unexpected."""
    result = subprocess.run(
        [sys.executable, "-m", "ke", "harvest", "--repo-root", str(tmp_path)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "engine")},
    )
    assert result.returncode != 0
    assert not list(tmp_path.rglob("*.yaml"))
