"""Regression tests: one per bug that reached a running system.

Every test here corresponds to a defect that was **actually shipped and actually
happened**, not one that was imagined. Each names the milestone it escaped from,
what it cost, and — the part that matters most — *why the existing tests could
not see it*.

That last part is the reason this file is separate from the feature suites. A
regression test whose only claim is "this used to be broken" tends to get
refactored away by someone who cannot tell it from a duplicate. A regression test
that records the blind spot survives, because it explains itself.

The rule when adding here: the test must **fail against the old behaviour**.
Every one below was verified that way before being committed.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
import yaml

import ke.clock as clock_module
import ke.store as store_module
from ke.clock import SystemClock
from ke.ids import IdRegistry
from ke.models import FeatureId, KnowledgeObject
from ke.review import ReviewQueue
from ke.store import build_object, write_object
from ke.validate import has_errors, validate_repo

from tests.test_pipeline import CLOCK, make_item


# ---------------------------------------------------------------------------
# M1 — the run ID was a timestamp, not an identifier
# ---------------------------------------------------------------------------


def test_run_id_does_not_change_when_the_clock_does(monkeypatch):
    """M1. `SystemClock.run_id()` recomputed from the wall clock every call.

    A harvest crossing a second boundary stamped its items with two different
    run IDs, so half the objects silently failed to join back to the run that
    produced them — and the run log exists precisely to make that join possible.

    **Why the tests missed it:** `FrozenClock` is stable by construction, so the
    test clock did not have the bug. Every existing assertion passed.
    """
    clock = SystemClock()
    first = clock.run_id()

    later = datetime.now(timezone.utc) + timedelta(seconds=120)

    class Advanced:
        @staticmethod
        def now(tz=None):
            return later

    monkeypatch.setattr(clock_module, "datetime", Advanced)
    assert clock.now() == later, "the wall clock should have moved"
    assert clock.run_id() == first, "the run ID must not follow it"


# ---------------------------------------------------------------------------
# M2 — an object is a pair of files, or it is nothing
# ---------------------------------------------------------------------------


def test_a_failed_metadata_write_leaves_no_orphan_article(tmp_path, monkeypatch):
    """M2. `write_object` wrote `feature.md`, then `metadata.yaml`.

    A serialisation error between them left half an object at a
    permanent-looking path. **The first real harvest produced 222 orphaned
    `feature.md` files with no metadata.**

    **Why the tests missed it:** every test wrote objects through the happy
    path. Nothing exercised a failure *between* the two writes, because nothing
    modelled the two writes as a thing that could be interrupted.
    """
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))

    monkeypatch.setattr(
        store_module,
        "render_metadata",
        lambda _obj: (_ for _ in ()).throw(RuntimeError("serialisation failed")),
    )
    with pytest.raises(RuntimeError):
        write_object(tmp_path, obj, item.summary, max_summary_words=120)

    orphans = list(tmp_path.rglob("feature.md"))
    assert not orphans, f"left {len(orphans)} orphaned article(s) behind"


def test_a_failed_article_write_leaves_no_orphan_metadata(tmp_path, monkeypatch):
    """The mirror case. Neither file may survive alone."""
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))

    monkeypatch.setattr(
        store_module,
        "render_feature_document",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("render failed")),
    )
    with pytest.raises(RuntimeError):
        write_object(tmp_path, obj, item.summary, max_summary_words=120)

    assert not list(tmp_path.rglob("metadata.yaml"))


# ---------------------------------------------------------------------------
# M2 — the registry and the validator disagreed about paths
# ---------------------------------------------------------------------------


def test_a_freshly_harvested_pack_validates(tmp_path, monkeypatch):
    """M2. The registry recorded paths relative to the pack root.

    `ke validate` expects them relative to `knowledge/`, so **all 222 objects
    failed validation** on the first run that wrote any.

    **Why the tests missed it:** storage was tested, and validation was tested,
    but never one after the other. The bug lived exactly in the seam. This test
    is deliberately end-to-end for that reason — it harvests, then validates,
    and asserts the validator is happy with what the harvester wrote.

    Root cause worth remembering: **two independent computations of one path.**
    """
    import ke.harvest as harvest_module
    from ke.acquisition import DiscoveryResult
    from ke.harvest import harvest_pack
    from ke.pack import Pack

    repo_root = tmp_path
    pack_root = repo_root / "domain-packs" / "test-pack"
    (pack_root / "state").mkdir(parents=True)
    (pack_root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (pack_root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')

    items = [make_item(title=f"Feature alpha {n}") for n in range(3)]
    monkeypatch.setattr(
        harvest_module, "discover_all", lambda *a, **k: DiscoveryResult(items=items)
    )
    report = harvest_pack(Pack.load(pack_root), clock=CLOCK)
    assert len(report.minted) == 3

    findings = validate_repo(repo_root, None)
    assert not has_errors(findings, strict=True), "\n".join(str(f) for f in findings)


def test_the_registry_records_the_path_the_validator_expects(tmp_path):
    """The same bug, isolated: the registry path must be the canonical form."""
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    registry = IdRegistry(prefix="TST")
    registry.record(obj.id, obj.knowledge_subpath)

    recorded = registry.path_for(obj.id)
    assert recorded == obj.knowledge_subpath
    assert not recorded.startswith("knowledge/"), (
        "the registry stores paths relative to knowledge/, not to the pack root"
    )


# ---------------------------------------------------------------------------
# M2 — the documented workflow was impossible
# ---------------------------------------------------------------------------


def test_the_key_printed_in_the_report_can_be_pasted_into_approve():
    """M2. `review-queue.md` prints digests with `sha256:` stripped.

    `ke review approve` matched against the full key, so copying a key out of
    the report and pasting it back **always failed**. The only documented way to
    drain the queue did not work.

    **Why the tests missed it:** `ReviewQueue.approve()` was tested with the key
    the test itself held — the full one. Nothing followed the workflow a human
    would follow.
    """
    from ke.indexer import render_review_queue

    queue = ReviewQueue(entries={})
    item = make_item()
    queue.enqueue(item)

    # Take the key exactly as a human would read it out of the rendered report.
    report = render_review_queue(queue.pending, "test-pack")
    printed_key = report.split("| `")[1].split("`")[0]

    assert queue.approve(printed_key).title == item.title


# ---------------------------------------------------------------------------
# M2 — metadata.yaml has to be readable by a person
# ---------------------------------------------------------------------------


def test_metadata_contains_no_yaml_anchors(tmp_path):
    """M2. PyYAML emitted `&id001` / `*id001` for shared date objects.

    A date is shared between `discovered_date` and the revision that recorded
    it, so real output carried anchors. `metadata.yaml` is read by humans in the
    GitHub UI, where anchor syntax is noise.

    **Why the tests missed it:** nothing asserted on the rendered *text*. Every
    check round-tripped through the parser, which resolves anchors invisibly.
    """
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(tmp_path, obj, item.summary, max_summary_words=120)
    text = (directory / "metadata.yaml").read_text(encoding="utf-8")

    assert "&id0" not in text and "*id0" not in text
    # And it must still parse back to the same object.
    assert KnowledgeObject.from_metadata_dict(yaml.safe_load(text)).id == obj.id


# ---------------------------------------------------------------------------
# M2 — the copyright budget counts the whole body
# ---------------------------------------------------------------------------


def test_a_maximum_length_summary_still_validates(tmp_path, monkeypatch):
    """M2. Discovery truncates to `max_summary_words`; the validator then counts
    **everything below the heading**, source line included.

    An item arriving at exactly the limit would have written a document over it.
    Caught before it shipped, pinned here because the two limits are set in
    different modules and will drift again.
    """
    from ke.acquisition import DiscoveryResult
    import ke.harvest as harvest_module
    from ke.harvest import harvest_pack
    from ke.pack import Pack

    pack_root = tmp_path / "domain-packs" / "test-pack"
    (pack_root / "state").mkdir(parents=True)
    (pack_root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (pack_root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')

    item = make_item(summary=" ".join(["word"] * 120))  # exactly at the limit
    monkeypatch.setattr(
        harvest_module, "discover_all", lambda *a, **k: DiscoveryResult(items=[item])
    )
    harvest_pack(Pack.load(pack_root), clock=CLOCK)

    findings = validate_repo(tmp_path, None)
    copyright_errors = [f for f in findings if f.code == "COPY001"]
    assert not copyright_errors, "a summary at the limit produced an over-limit body"


# ---------------------------------------------------------------------------
# M2 — state file failure policies are deliberate and differ
# ---------------------------------------------------------------------------


def test_a_corrupt_registry_refuses_rather_than_guesses(tmp_path):
    """ADR-0032. A reused Feature ID can never be undone, so this fails loudly."""
    from ke.ids import IdError

    path = tmp_path / "id-registry.json"
    path.write_text(json.dumps({
        "prefix": "TST",
        "counters": {"2026-07": 2},
        "paths": {"TST-2026-07-050": "2026/07/x"},  # 050 exceeds the counter
    }))
    with pytest.raises(IdError):
        IdRegistry.load(path, "TST")


def test_a_corrupt_dedup_cache_does_not_stop_the_run(tmp_path):
    """ADR-0032, the other direction. This file is rebuildable, so it degrades."""
    from ke.dedupe import SeenIndex

    path = tmp_path / "seen.json"
    path.write_text("{ not json at all")
    assert SeenIndex.load(path).identities == {}


def test_a_corrupt_queue_refuses_rather_than_losing_decisions(tmp_path):
    """ADR-0032. It holds human decisions and original discovery dates."""
    path = tmp_path / "review-queue.json"
    path.write_text("{ not json at all")
    with pytest.raises(ValueError):
        ReviewQueue.load(path)
