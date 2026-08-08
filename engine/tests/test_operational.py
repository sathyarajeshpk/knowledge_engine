"""Operational readiness: what happens when the environment fails.

Every other suite tests the engine doing its job. This one tests the engine
having its job taken away from it halfway through — the disk fills, a write is
refused, the process is killed, the network disappears.

The organising question is not "does it fail?" but **"what state does it leave
behind, and who has to clean it up?"** Each scenario is classified as one of:

* **Graceful degradation** — the run completes, does less, and says so.
* **Automatic recovery** — the next run repairs it with no human involved.
* **Manual recovery** — a person must do something, and the engine must say
  what.

A scenario with no test is a guess. `docs/reviews/M7_OPERATIONAL_READINESS.md`
cites these by name, so a claim in that document is checkable against something
that runs.

## Why failures are injected rather than provoked

Two of these scenarios are conventionally tested with `chmod`. That is useless
here: CI and this container run as **root**, and root bypasses the permission
bits entirely. A `chmod`-based permission test passes without exercising
anything, which is worse than no test — it is a green check mark attached to an
unverified claim.

So `PermissionError` and `OSError(ENOSPC)` are injected at the write boundary.
That tests the engine's handling, which is the part this repository owns. The
kernel's enforcement is not under test and does not need to be.
"""

from __future__ import annotations

import contextlib
import errno
import os
import time
from pathlib import Path

import pytest

import ke.ids as ids_module
import ke.pipeline as pipeline_module
import ke.store as store_module
from ke.acquisition import DiscoveryResult
from ke.harvest import harvest_pack
from ke.lock import STALE_AFTER_SECONDS, LockError, pack_lock
from ke.report import HarvestReport
from ke.pack import Pack

from tests.test_pipeline import CLOCK, make_item

RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


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


def disk_full(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def permission_denied(*_args, **_kwargs):
    raise PermissionError(errno.EACCES, "Permission denied")


def objects_on_disk(pack) -> tuple[int, int]:
    return (
        len(list(pack.knowledge_dir.rglob("metadata.yaml"))),
        len(list(pack.knowledge_dir.rglob("feature.md"))),
    )


def use_channels(monkeypatch, channels):
    """Make the pipeline discover exactly these notification channels.

    Patched at `from_environment`, which is where the pipeline actually looks —
    so this exercises the real discovery path rather than a parallel one
    invented for the test.
    """
    from ke.notify.github_issue import GitHubIssueNotifier
    from ke.notify.smtp_email import SmtpNotifier

    first = channels[0] if channels else None
    rest = channels[1] if len(channels) > 1 else None
    monkeypatch.setattr(
        GitHubIssueNotifier, "from_environment", classmethod(lambda cls: first)
    )
    monkeypatch.setattr(
        SmtpNotifier, "from_environment", classmethod(lambda cls: rest)
    )


# ---------------------------------------------------------------------------
# 1. Disk full  ·  graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("failure", [disk_full, permission_denied])
def test_a_write_failure_completes_the_run_and_reports_it(pack, monkeypatch, failure):
    """The run finishes, mints nothing, and says why.

    Not an exception escaping to the caller: a weekly job that dies with a
    traceback has told GitHub it failed and told the operator nothing useful.
    """
    monkeypatch.setattr(store_module, "_atomic_write", failure)

    report = run(pack, [make_item()], monkeypatch)

    assert report.minted == []
    assert len(report.errors) == 1
    assert "No space left" in report.errors[0] or "Permission denied" in report.errors[0]


@pytest.mark.parametrize("failure", [disk_full, permission_denied])
def test_a_write_failure_leaves_no_partial_object(pack, monkeypatch, failure):
    """A directory that looks like an object but is not is worse than nothing.

    `write_object` renders both documents before writing either, and unlinks
    what it wrote if the second write fails — so this class of failure produces
    "no files" rather than "half an object" at a permanent-looking path.
    """
    monkeypatch.setattr(store_module, "_atomic_write", failure)

    run(pack, [make_item()], monkeypatch)

    assert objects_on_disk(pack) == (0, 0)


def test_a_write_failure_partway_through_does_not_consume_a_feature_id(
    pack, monkeypatch
):
    """The number must still be available next time.

    An ID burned on an object that was never written is not a disaster — gaps
    are cosmetic — but it is avoidable, and avoiding it keeps the registry a
    faithful record of what exists.
    """
    calls = {"n": 0}
    real = store_module._atomic_write

    def fail_on_the_second_file(path, text):
        calls["n"] += 1
        if calls["n"] == 2:
            disk_full()
        return real(path, text)

    monkeypatch.setattr(store_module, "_atomic_write", fail_on_the_second_file)
    run(pack, [make_item()], monkeypatch)

    registry = (pack.state_dir / "id-registry.json").read_text(encoding="utf-8")
    assert "TST-2026" not in registry


def test_a_later_run_recovers_after_a_transient_disk_failure(pack, monkeypatch):
    """Automatic recovery: the item is rediscovered and minted normally."""
    monkeypatch.setattr(store_module, "_atomic_write", disk_full)
    run(pack, [make_item()], monkeypatch)
    monkeypatch.undo()

    report = run(pack, [make_item()], monkeypatch)

    assert len(report.minted) == 1
    assert objects_on_disk(pack) == (1, 1)


# ---------------------------------------------------------------------------
# 2. State persistence fails after minting  ·  manual recovery
# ---------------------------------------------------------------------------


def fail_registry_save(monkeypatch):
    monkeypatch.setattr(
        ids_module.IdRegistry, "save", lambda self, path: disk_full()
    )


def test_a_registry_write_failure_is_reported_with_its_remedy(pack, monkeypatch):
    """The one scenario in this engine that genuinely needs a human.

    Objects are on disk and the registry does not know about them. The operator
    needs to be told that, told nothing is lost, and told what to run — a bare
    "No space left on device" conveys none of it.
    """
    fail_registry_save(monkeypatch)

    report = run(pack, [make_item()], monkeypatch)

    assert report.errors
    message = report.errors[0]
    assert "NOT registered" in message
    assert "ke validate" in message
    assert "Nothing is lost" in message


def test_a_registry_write_failure_never_duplicates_a_feature_id(pack, monkeypatch):
    """The failure that would be permanent, and is prevented.

    After the registry write fails, the counter is back where it started, so the
    next run allocates the same number. `write_object` refuses to overwrite a
    minted object, which is what stops that number being issued twice.
    """
    fail_registry_save(monkeypatch)
    run(pack, [make_item()], monkeypatch)
    monkeypatch.undo()

    second = run(pack, [make_item()], monkeypatch)

    assert second.minted == []
    assert any("FileExistsError" in e or "already exists" in e for e in second.errors)
    directories = sorted(p.name for p in pack.knowledge_dir.rglob("TST-*"))
    assert len(directories) == 1


def test_validate_detects_an_unregistered_object(pack, monkeypatch, tmp_path):
    """Detection is what makes manual recovery possible rather than mysterious."""
    from ke.validate import validate_repo

    fail_registry_save(monkeypatch)
    run(pack, [make_item()], monkeypatch)
    monkeypatch.undo()

    findings = validate_repo(tmp_path, None)

    assert any("REG003" in str(f) for f in findings)


def test_the_scheduled_path_cannot_publish_an_inconsistent_pack():
    """Why this is manual recovery locally and automatic recovery on a schedule.

    The workflow validates *after* harvesting and *before* committing. A pack in
    this state fails validation, the job stops, the runner is discarded, and the
    next Sunday starts from the last good commit — so the state never reaches
    the repository.
    """
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github" / "workflows" / "weekly-harvest.yml"
    ).read_text(encoding="utf-8")

    validate_after = workflow.index("Validate after harvesting")
    commit = workflow.index("Commit and push")
    assert validate_after < commit


# ---------------------------------------------------------------------------
# 3. Interrupted harvest and unexpected termination
# ---------------------------------------------------------------------------


def test_a_crash_in_one_stage_stops_the_run_without_unwinding_earlier_ones(
    pack, monkeypatch
):
    """Stages are not transactional, and pretending otherwise would be worse.

    Everything written before the failure stays written. That is safe because
    the ordering is chosen so a partial run is always recoverable: objects
    before state (ADR-0031), state before indexes, indexes before the digest.
    """
    run(pack, [make_item()], monkeypatch)
    before, _ = objects_on_disk(pack)

    # Patched at `write_indexes`, not at the stage function. `STAGES` is a tuple
    # of direct references captured at import, so replacing the module attribute
    # would leave the tuple pointing at the original — a patch that silently
    # does nothing and a test that silently proves nothing.
    monkeypatch.setattr(
        pipeline_module, "write_indexes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed here")),
    )
    report = run(pack, [make_item(title="Another feature")], monkeypatch)

    assert any("killed here" in e for e in report.errors)
    after, _ = objects_on_disk(pack)
    assert after >= before  # nothing was rolled back or lost


def test_an_interrupted_run_leaves_a_pack_the_next_run_can_use(pack, monkeypatch):
    """Automatic recovery: the following run completes normally."""
    monkeypatch.setattr(
        pipeline_module, "write_indexes",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    run(pack, [make_item()], monkeypatch)
    monkeypatch.undo()

    report = run(pack, [make_item()], monkeypatch)

    assert report.errors == []
    assert (pack.indexes_dir / "INDEX.md").exists()


# ---------------------------------------------------------------------------
# 4. Lock recovery after unexpected termination
# ---------------------------------------------------------------------------


def test_a_lock_left_by_a_killed_process_is_reclaimed(pack):
    """Automatic recovery, after `STALE_AFTER_SECONDS`.

    A process killed hard cannot release its lock. A lock that outlives its
    holder forever turns one crash into a permanently unusable pack, which is
    strictly worse than the concurrency it was preventing.
    """
    lock_path = pack.state_dir / ".harvest.lock"
    lock_path.write_text("pid 99999\n", encoding="utf-8")
    old = time.time() - STALE_AFTER_SECONDS - 60
    os.utime(lock_path, (old, old))

    with pack_lock(pack.state_dir, holder="recovery test"):
        pass  # reclaimed rather than refused

    assert not lock_path.exists()


def test_a_lock_from_a_live_run_is_respected(pack):
    """The control only means something if it actually blocks."""
    with pack_lock(pack.state_dir, holder="first"):
        with pytest.raises(LockError):
            with pack_lock(pack.state_dir, holder="second"):
                pass


def test_the_lock_is_released_when_the_run_raises(pack):
    """Graceful degradation: a failed harvest must not wedge the next one."""
    with pytest.raises(RuntimeError):
        with pack_lock(pack.state_dir, holder="doomed"):
            raise RuntimeError("boom")

    with pack_lock(pack.state_dir, holder="next"):
        pass


# ---------------------------------------------------------------------------
# 5. Corrupted state files
# ---------------------------------------------------------------------------


def test_a_corrupt_dedup_cache_degrades_rather_than_stopping(pack, monkeypatch):
    """Worst case is re-processing something already known: wasteful, not harmful."""
    run(pack, [make_item()], monkeypatch)
    pack.seen_path.write_text("{ not json at all", encoding="utf-8")

    report = run(pack, [make_item()], monkeypatch)

    assert report.errors == []


def test_a_corrupt_registry_stops_the_run(pack, monkeypatch):
    """The opposite policy, for the opposite reason.

    The counter is the only thing preventing a duplicate permanent ID. Guessing
    it is unrecoverable, so this one refuses to proceed (ADR-0032).
    """
    pack.registry_path.write_text("{ not json", encoding="utf-8")

    report = run(pack, [make_item()], monkeypatch)

    assert report.errors
    assert report.minted == []


def test_one_unreadable_object_does_not_cost_the_others(pack, monkeypatch):
    """Manual recovery for the damaged file; the rest of the pack keeps working."""
    run(pack, [make_item(title=f"Feature {n}") for n in range(4)], monkeypatch)
    damaged = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0]
    damaged.write_text("!!! not yaml", encoding="utf-8")

    from ke.harvest import load_objects_with_dirs

    assert len(load_objects_with_dirs(pack)) == 3


def test_a_corrupt_review_queue_stops_the_run(pack, monkeypatch):
    """Loud, and correctly so — I expected degradation and was wrong.

    Degrading looks safe at first glance: queued items are rediscovered on the
    next run, so treating a damaged queue as empty appears to cost nothing.

    It costs something permanent. The queue stores `first_discovered_date`, and
    a Feature ID's month comes from when the knowledge *appeared*, not when a
    human got round to approving it (ADR-0028). Silently losing those dates
    would shift the month of every ID minted from the queue afterwards — and a
    Feature ID never changes once issued.

    So this one joins the registry in the stop-the-run category, for the same
    underlying reason: the damage would be to something that cannot be undone.
    """
    (pack.state_dir / "review-queue.json").write_text("garbage", encoding="utf-8")

    report = run(pack, [make_item()], monkeypatch)

    assert report.errors
    assert "review queue" in report.errors[0]
    assert report.minted == []


# ---------------------------------------------------------------------------
# 6. Notification failures  ·  graceful degradation
# ---------------------------------------------------------------------------


def test_a_failing_notifier_never_fails_the_harvest(pack, monkeypatch):
    """By the time notification runs, the knowledge is already committed.

    Losing a harvest because an SMTP server was down would be an absurd trade.
    """
    class Exploding:
        name = "exploding"

        def send(self, notification):
            raise ConnectionRefusedError("smtp.invalid:587 refused")

    use_channels(monkeypatch, [Exploding()])

    report = run(pack, [make_item()], monkeypatch, notify=True)

    assert len(report.minted) == 1
    assert report.errors == []


def test_a_github_api_failure_is_recorded_not_raised(pack, monkeypatch):
    """The Issue channel is the durable one, and it still cannot fail the run."""
    from urllib.error import HTTPError

    class ApiDown:
        name = "github-issue"

        def send(self, notification):
            raise HTTPError(
                "https://api.github.com", 503, "Service Unavailable", {}, None
            )

    use_channels(monkeypatch, [ApiDown()])

    report = run(pack, [make_item()], monkeypatch, notify=True)

    assert report.errors == []
    assert report.notification_failures


def test_a_notification_failure_does_not_stop_the_other_channels(pack, monkeypatch):
    """One dead channel must not silence the one that still works."""
    delivered = []

    class Broken:
        name = "broken"

        def send(self, notification):
            raise ConnectionError("down")

    class Working:
        name = "working"

        def send(self, notification):
            delivered.append(notification)

    use_channels(monkeypatch, [Broken(), Working()])

    run(pack, [make_item()], monkeypatch, notify=True)

    assert delivered


# ---------------------------------------------------------------------------
# 7. The digest survives a bad run
# ---------------------------------------------------------------------------


def test_a_digest_is_written_even_when_the_run_had_errors(pack, monkeypatch):
    """The record of a failure is the thing most worth having.

    A digest written only on success means the weeks you most need to read
    about are the weeks with nothing to read.
    """
    monkeypatch.setattr(store_module, "_atomic_write", disk_full)

    run(pack, [make_item()], monkeypatch)

    digests = list(pack.digests_dir.glob("*.md"))
    assert digests
    assert "Errors" in digests[0].read_text(encoding="utf-8")


def test_the_run_log_is_appended_even_when_the_run_had_errors(pack, monkeypatch):
    """A weekly commit is what keeps the cron from being auto-disabled."""
    monkeypatch.setattr(store_module, "_atomic_write", disk_full)

    run(pack, [make_item()], monkeypatch)

    assert pack.run_log_path.exists()
    assert "run-" in pack.run_log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 8. Permission bits, honestly
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    RUNNING_AS_ROOT, reason="root bypasses permission bits; injected instead"
)
def test_a_read_only_pack_directory_is_reported(pack, monkeypatch):
    """The real-kernel version of the injected permission test.

    Skipped under root — which is both CI and the development container — and
    that is the point of writing it this way rather than letting a `chmod` test
    pass without exercising anything. The injected variants above carry the
    actual coverage.
    """
    import stat

    os.chmod(pack.knowledge_dir.parent, stat.S_IRUSR | stat.S_IXUSR)
    try:
        report = run(pack, [make_item()], monkeypatch)
        assert report.errors
    finally:
        os.chmod(pack.knowledge_dir.parent, 0o755)


# ---------------------------------------------------------------------------
# One pack must not take the run down with it (M8, O-2)
# ---------------------------------------------------------------------------


def _two_pack_repo(tmp_path: Path) -> Path:
    from conftest import make_pack

    repo = tmp_path / "repo"
    packs_dir = repo / "domain-packs"
    packs_dir.mkdir(parents=True)
    make_pack(packs_dir, "alpha", "ALF")
    make_pack(packs_dir, "beta", "BET")
    return repo


def _fail_during_harvest(monkeypatch, pack_name, exc=None):
    """Blow up one pack's harvest phase; record which packs were attempted.

    Patches `ke.pipeline.run_stages`, **not** `ke.harvest.harvest_pack`.

    M9-2 split the pipeline into a harvest phase and a publish phase so the
    cross-pack scan can run once between them, and the CLI now drives
    `harvest_all`. `harvest_pack` is no longer on the path these tests exercise,
    so patching it would leave four tests passing against a function nobody
    calls — a guard that is never invoked, which is exactly what M8 learned to
    distrust.

    `pack_name=None` fails every pack. Returns the list of attempted names.
    """
    import ke.pipeline as pipeline_module
    from ke.pipeline import HARVEST_STAGES

    tried: list[str] = []
    real = pipeline_module.run_stages
    failure = exc or RuntimeError("disk on fire")

    def fake_run_stages(ctx, stages=HARVEST_STAGES):
        if stages is HARVEST_STAGES:
            tried.append(ctx.pack.name)
            if pack_name is None or ctx.pack.name == pack_name:
                raise failure
        return real(ctx, stages)

    monkeypatch.setattr("ke.pipeline.run_stages", fake_run_stages)
    return tried


def test_a_failing_pack_does_not_stop_the_next_one(tmp_path, monkeypatch, capsys):
    """Packs are independent by construction; the run must reflect it.

    Before M8 the loop returned on the first failure, so a stuck lock on Fabric
    silently cost a week of Azure. Invisible with one pack, where "abort the
    pack" and "abort the run" are the same thing.
    """
    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    tried = _fail_during_harvest(monkeypatch, "alpha")

    code = cli.main(["harvest", "--repo-root", str(repo)])

    assert tried == ["alpha", "beta"], "beta was skipped after alpha failed"
    assert code != 0, "a failed pack must not report success"


def test_a_locked_pack_does_not_stop_the_next_one(tmp_path, monkeypatch, capsys):
    """A lock is per-pack state, so a stuck one is a per-pack problem.

    `LockError` was once the *only* exception the loop caught, and it returned
    immediately — so the one failure the author had thought about was also the
    one that cost every other pack its run.
    """
    import contextlib as ctxlib

    from ke import __main__ as cli
    from ke.lock import LockError

    repo = _two_pack_repo(tmp_path)

    def fake_lock(state_dir, holder=""):
        if state_dir.parent.name == "alpha":
            raise LockError("held by another process")
        return ctxlib.nullcontext()

    monkeypatch.setattr("ke.lock.pack_lock", fake_lock)

    code = cli.main(["harvest", "--repo-root", str(repo)])
    out = capsys.readouterr()

    assert "alpha" in out.err
    assert code != 0
    # beta was not locked, so it must still have been harvested and published.
    assert (repo / "domain-packs" / "beta" / "indexes" / "INDEX.md").is_file()
    assert not (repo / "domain-packs" / "alpha" / "indexes" / "INDEX.md").is_file()


def test_the_failed_packs_are_named_again_at_the_end(tmp_path, monkeypatch, capsys):
    """A skipped pack must not read as a clean run to somebody skimming a log.

    The per-pack error scrolls past several screens of successful output from
    the packs that worked.
    """
    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    _fail_during_harvest(monkeypatch, "alpha")

    cli.main(["harvest", "--repo-root", str(repo)])

    assert "1 pack(s) failed and were skipped: alpha" in capsys.readouterr().err


def test_a_keyboard_interrupt_still_stops_everything(tmp_path, monkeypatch):
    """`Exception`, not `BaseException`. Ctrl-C must not be swallowed as
    "one pack failed, carrying on"."""
    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    tried = _fail_during_harvest(monkeypatch, "alpha", exc=KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        cli.main(["harvest", "--repo-root", str(repo)])

    assert tried == ["alpha"]


# ---------------------------------------------------------------------------
# Lock scope under the interleaved execution model (M9-2)
# ---------------------------------------------------------------------------


def _lock_path(repo: Path, pack: str) -> Path:
    return repo / "domain-packs" / pack / "state" / ".harvest.lock"


def test_every_pack_stays_locked_through_the_publish_phase(tmp_path, monkeypatch):
    """The property the new execution model requires, and the reason it changed.

    `harvest_all` interleaves packs: alpha and beta both harvest, then the
    cross-pack scan runs, then both publish. If alpha's lock were released when
    alpha finished *harvesting* — which is what a per-pack acquire/release loop
    does — a concurrent run could start minting into alpha while this run was
    still publishing alpha's indexes from the objects it had just read.

    So the lock has to span harvest **and** publish for every pack. This test
    fails if the scope is narrowed back.
    """
    from ke import __main__ as cli
    import ke.pipeline as pipeline_module
    from ke.pipeline import PUBLISH_STAGES

    repo = _two_pack_repo(tmp_path)
    seen_during_publish: list[tuple[str, bool, bool]] = []
    real = pipeline_module.run_stages

    def traced(ctx, stages=PUBLISH_STAGES):
        if stages is PUBLISH_STAGES:
            seen_during_publish.append(
                (
                    ctx.pack.name,
                    _lock_path(repo, "alpha").exists(),
                    _lock_path(repo, "beta").exists(),
                )
            )
        return real(ctx, stages)

    monkeypatch.setattr("ke.pipeline.run_stages", traced)
    cli.main(["harvest", "--repo-root", str(repo)])

    assert seen_during_publish, "no pack reached the publish phase"
    for pack_name, alpha_locked, beta_locked in seen_during_publish:
        assert alpha_locked, f"alpha unlocked while publishing {pack_name}"
        assert beta_locked, f"beta unlocked while publishing {pack_name}"


def test_every_lock_is_released_when_the_run_finishes(tmp_path):
    """A run that completes must leave nothing behind for the next one."""
    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    cli.main(["harvest", "--repo-root", str(repo)])

    assert not _lock_path(repo, "alpha").exists()
    assert not _lock_path(repo, "beta").exists()


def test_every_lock_is_released_when_a_pack_raises(tmp_path, monkeypatch):
    """Held in an ExitStack, so an exception mid-run still unwinds every lock.

    Widening the scope from one pack to all of them widens the blast radius of
    getting release wrong: a leaked lock now strands the whole repository for an
    hour rather than one pack.
    """
    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    _fail_during_harvest(monkeypatch, None)

    cli.main(["harvest", "--repo-root", str(repo)])

    assert not _lock_path(repo, "alpha").exists()
    assert not _lock_path(repo, "beta").exists()


def test_a_second_run_cannot_touch_a_pack_the_first_still_holds(tmp_path):
    """Per-pack exclusivity is what actually protects the registry.

    Two runs never write the same pack: the second gets `LockError` for anything
    the first holds, reports it and skips it. It may still take packs the first
    does not hold — the guarantee is per-pack exclusivity, not whole-run
    mutual exclusion — and that is sufficient, because a Feature ID is minted
    from one pack's registry.
    """
    from ke import __main__ as cli
    from ke.lock import pack_lock

    repo = _two_pack_repo(tmp_path)

    with pack_lock(_lock_path(repo, "alpha").parent, holder="another run"):
        code = cli.main(["harvest", "--repo-root", str(repo)])

    assert code != 0
    # alpha was held by the other run, so this run must not have published it.
    assert not (repo / "domain-packs" / "alpha" / "indexes" / "INDEX.md").is_file()
    # beta was free, so it must still have been harvested.
    assert (repo / "domain-packs" / "beta" / "indexes" / "INDEX.md").is_file()


def test_a_stale_lock_cannot_permanently_block_the_repository(tmp_path):
    """Widening the scope must not create a way to strand every pack at once.

    Reclaim is unchanged from M6 and still per-pack, so a crashed run leaves
    locks that the next run reclaims after `STALE_AFTER_SECONDS` — for all of
    them, not just the one it died on.
    """
    import json
    import time

    from ke import __main__ as cli

    repo = _two_pack_repo(tmp_path)
    ancient = time.time() - STALE_AFTER_SECONDS - 60
    for name in ("alpha", "beta"):
        path = _lock_path(repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"holder": "crashed", "acquired_at": ancient}))

    code = cli.main(["harvest", "--repo-root", str(repo)])

    assert code == 0, "a stale lock blocked the run"
    assert (repo / "domain-packs" / "alpha" / "indexes" / "INDEX.md").is_file()
    assert (repo / "domain-packs" / "beta" / "indexes" / "INDEX.md").is_file()


def test_a_single_pack_run_is_unchanged(tmp_path):
    """One pack: acquire one lock, no cross-pack scan, publish as always.

    The interleaving only exists for multiple packs, so the single-pack path
    must not have acquired any new behaviour along with it.
    """
    from ke import __main__ as cli

    repo = tmp_path / "repo"
    packs_dir = repo / "domain-packs"
    packs_dir.mkdir(parents=True)
    from conftest import make_pack

    make_pack(packs_dir, "solo", "SOL")

    code = cli.main(["harvest", "--repo-root", str(repo)])

    assert code == 0
    assert (repo / "domain-packs" / "solo" / "indexes" / "INDEX.md").is_file()
    assert not _lock_path(repo, "solo").exists()
