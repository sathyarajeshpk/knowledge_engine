"""Tests for M3: the update path.

The engine has written 222 objects. From here it must be able to *revisit* them
without destroying the work their owner has put in. That is the promise
ADR-0008's field ownership model made in M0, and this is the first milestone
that can actually break it.

The single most important test in this file is
`test_every_user_owned_byte_survives_a_source_change`. If it fails, the weekly
job is unsafe to run unattended and the engine should be stopped.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
import yaml

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.clock import FrozenClock
from ke.harvest import harvest_pack, load_objects_with_dirs
from ke.models import (
    DateConfidence,
    KnowledgeObject,
    LearningStatus,
    Lifecycle,
)
from ke.pack import Pack
from ke.revisions import (
    FROZEN_AFTER_MINT,
    UPDATABLE_FIELDS,
    apply_update,
    detect_changes,
    is_material,
    user_owned_snapshot,
)
from ke.store import load_object

from tests.test_pipeline import CLOCK, make_item


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


def run(pack, items, monkeypatch, clock=CLOCK):
    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=list(items))
    )
    return harvest_pack(pack, clock=clock)


def only_object(pack):
    paths = sorted(pack.knowledge_dir.rglob("metadata.yaml"))
    assert len(paths) == 1, f"expected one object, found {len(paths)}"
    return paths[0].parent


# ---------------------------------------------------------------------------
# 1. Detect existing objects
# ---------------------------------------------------------------------------


def test_a_second_sighting_updates_rather_than_mints(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)

    reworded = replace(item, title="Direct Lake is now generally available")
    report = run(pack, [reworded], monkeypatch)

    assert report.minted == [], "a known item must never mint a second ID"
    assert len(report.updated) == 1
    assert len(list(pack.knowledge_dir.rglob("metadata.yaml"))) == 1


def test_an_unchanged_sighting_writes_nothing(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    before = (only_object(pack) / "metadata.yaml").read_bytes()

    report = run(pack, [item], monkeypatch)

    assert report.updated == []
    assert report.unchanged == 1
    assert (only_object(pack) / "metadata.yaml").read_bytes() == before


# ---------------------------------------------------------------------------
# 2 & 3. Engine-owned fields update; user-owned fields never do
# ---------------------------------------------------------------------------


def test_every_user_owned_byte_survives_a_source_change(pack, monkeypatch):
    """**The most important test in the repository.**

    Simulates what actually happens: you spend weeks adding learning state,
    notes and relationships to an object, then the source rewrites its
    announcement and the weekly job runs unattended.

    Every user-owned field must come back byte-identical. If this fails, stop
    the engine — it is destroying work that exists nowhere else.
    """
    item = make_item()
    run(pack, [item], monkeypatch)
    directory = only_object(pack)

    # A human puts real work into the object.
    stored = load_object(directory)
    edited = replace(
        stored,
        learning_status=LearningStatus.IN_PROGRESS,
        notes="Tested this against our staging capacity. Watch the refresh cost.",
        prerequisites=("TST-2025-11-004",),
        builds_on=("TST-2025-11-004",),
        related_topics=("TST-2026-01-002", "TST-2026-01-003"),
        overrides=("difficulty",),
        difficulty="advanced",
    )
    (directory / "metadata.yaml").write_text(
        yaml.safe_dump(edited.to_metadata_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    before = user_owned_snapshot(load_object(directory))
    assert before["notes"], "the fixture must actually have user content"

    # The source rewrites everything the engine owns.
    changed = replace(
        item,
        title="Direct Lake reaches general availability for all workloads",
        summary="A completely rewritten summary with different wording throughout.",
        published_date=date(2026, 9, 15),
    )
    report = run(pack, [changed], monkeypatch)
    assert report.errors == []
    assert len(report.updated) == 1

    after_obj = load_object(directory)
    assert user_owned_snapshot(after_obj) == before, "the engine overwrote user work"
    # And the engine-owned side really did move.
    assert after_obj.title == changed.title


def test_a_locked_proposed_field_is_not_overwritten(pack, monkeypatch):
    """`overrides` is the user's veto, and it must outrank the engine."""
    item = make_item()
    run(pack, [item], monkeypatch)
    directory = only_object(pack)

    stored = load_object(directory)
    edited = replace(stored, difficulty="advanced", overrides=("difficulty",))
    (directory / "metadata.yaml").write_text(
        yaml.safe_dump(edited.to_metadata_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    run(pack, [replace(item, title="Retitled by the source")], monkeypatch)
    assert load_object(directory).difficulty == "advanced"


def test_writing_a_user_owned_field_raises_rather_than_succeeding():
    """The guard itself, asserted directly rather than trusted."""
    item = make_item()
    from ke.models import FeatureId
    from ke.store import build_object

    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    with pytest.raises(PermissionError):
        obj.with_engine_fields(notes="the engine must not write this")


def test_the_updatable_set_excludes_everything_permanent():
    """A field that defines the object's path or origin may never be updated."""
    assert not (UPDATABLE_FIELDS & FROZEN_AFTER_MINT)
    for name in ("id", "slug", "discovered_date", "revisions", "provenance"):
        assert name not in UPDATABLE_FIELDS


# ---------------------------------------------------------------------------
# 4. Detect real revisions
# ---------------------------------------------------------------------------


def test_a_revision_is_appended_only_on_real_change(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    directory = only_object(pack)
    assert len(load_object(directory).revisions) == 1  # initial ingestion

    run(pack, [item], monkeypatch)  # nothing changed
    assert len(load_object(directory).revisions) == 1

    run(pack, [replace(item, title="Something genuinely different now")], monkeypatch)
    revisions = load_object(directory).revisions
    assert len(revisions) == 2
    assert "title" in revisions[1].changed_fields
    assert revisions[1].summary


def test_reflowed_whitespace_is_not_a_revision(pack, monkeypatch):
    """`content_hash` normalises whitespace, so re-wrapping is not news."""
    item = make_item(summary="One two three four five.")
    run(pack, [item], monkeypatch)
    directory = only_object(pack)

    reflowed = replace(item, summary="One   two\nthree\n\nfour   five.")
    report = run(pack, [reflowed], monkeypatch)

    assert report.updated == []
    assert len(load_object(directory).revisions) == 1


def test_confidence_alone_is_not_material():
    """A per-run assessment must not fill the history with noise."""
    assert is_material({"identity_confidence": "medium"}) is False
    assert is_material({"title": "x"}) is True
    assert is_material({"identity_confidence": "medium", "title": "x"}) is True


def test_revisions_carry_snapshots_for_the_time_machine(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="A newer title entirely")], monkeypatch)

    latest = load_object(only_object(pack)).revisions[-1]
    assert latest.title_snapshot == "A newer title entirely"
    assert latest.summary_snapshot
    assert latest.content_hash.startswith("sha256:")


def test_detect_changes_is_empty_for_an_identical_sighting(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    assert detect_changes(load_object(only_object(pack)), item) == {}


# ---------------------------------------------------------------------------
# 5, 6, 7. Registry, idempotency, permanent IDs
# ---------------------------------------------------------------------------


def test_the_feature_id_never_changes_across_updates(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    original_id = load_object(only_object(pack)).id
    original_path = only_object(pack)

    for title in ("Second wording", "Third wording entirely", "Fourth and final"):
        run(pack, [replace(item, title=title)], monkeypatch)

    assert load_object(only_object(pack)).id == original_id
    assert only_object(pack) == original_path, "the object directory moved"


def test_five_consecutive_runs_are_stable(pack, monkeypatch):
    """Idempotency across many runs, not just two."""
    items = [make_item(title=f"Feature alpha {n}") for n in range(4)]
    run(pack, items, monkeypatch)

    registry_bytes = pack.registry_path.read_bytes()
    object_bytes = {
        path: path.read_bytes() for path in sorted(pack.knowledge_dir.rglob("*"))
        if path.is_file()
    }

    for _ in range(4):
        report = run(pack, items, monkeypatch)
        assert report.minted == []
        assert report.updated == []
        assert report.unchanged == 4

    assert pack.registry_path.read_bytes() == registry_bytes
    assert {
        path: path.read_bytes() for path in sorted(pack.knowledge_dir.rglob("*"))
        if path.is_file()
    } == object_bytes


def test_the_registry_does_not_grow_on_an_update(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    import json

    before = json.loads(pack.registry_path.read_text())

    run(pack, [replace(item, title="Retitled")], monkeypatch)
    after = json.loads(pack.registry_path.read_text())

    assert after["counters"] == before["counters"]
    assert after["paths"] == before["paths"]


def test_indexes_reflect_an_update(pack, monkeypatch):
    item = make_item()
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="A brand new title for the index")], monkeypatch)

    index = (pack.indexes_dir / "INDEX.md").read_text(encoding="utf-8")
    assert "A brand new title for the index" in index


# ---------------------------------------------------------------------------
# 8. Only meaningful diffs
# ---------------------------------------------------------------------------


def test_update_object_does_not_rewrite_identical_bytes(tmp_path):
    """The second line of defence, tested directly because the first hides it.

    `detect_changes` normally stops a no-op run before `update_object` is even
    called, so an end-to-end test cannot tell whether this comparison works —
    removing it entirely leaves the pipeline tests green. It matters when a
    change is detected but renders identically, and it is cheap insurance
    against the weekly diff filling with noise.
    """
    from ke.models import FeatureId
    from ke.store import build_object, update_object, write_object

    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(tmp_path, obj, item.summary, max_summary_words=120)
    stamps = {p: p.stat().st_mtime_ns for p in sorted(directory.iterdir())}

    changed = update_object(directory, obj, item.summary, max_summary_words=120)

    assert changed is False
    assert {p: p.stat().st_mtime_ns for p in sorted(directory.iterdir())} == stamps


def test_an_unchanged_run_touches_no_object_file(pack, monkeypatch):
    """The property that makes the weekly diff worth reading.

    If a no-op run rewrote identical bytes, every week would show 222 changed
    files and a real change would be invisible in the noise.

    Note this exercises `detect_changes` rather than `update_object`'s byte
    comparison — the former short-circuits before the latter is reached. The
    comparison itself is covered by
    `test_update_object_does_not_rewrite_identical_bytes`.
    """
    items = [make_item(title=f"Feature beta {n}") for n in range(3)]
    run(pack, items, monkeypatch)

    stamps = {
        path: path.stat().st_mtime_ns
        for path in sorted(pack.knowledge_dir.rglob("*"))
        if path.is_file()
    }
    run(pack, items, monkeypatch)

    unchanged = {
        path: path.stat().st_mtime_ns
        for path in sorted(pack.knowledge_dir.rglob("*"))
        if path.is_file()
    }
    assert unchanged == stamps, "a no-op run rewrote object files"


def test_only_the_changed_object_is_rewritten(pack, monkeypatch):
    items = [make_item(title=f"Feature gamma {n}") for n in range(3)]
    run(pack, items, monkeypatch)

    stamps = {
        path: path.stat().st_mtime_ns
        for path in sorted(pack.knowledge_dir.rglob("*.yaml"))
    }
    changed_items = [items[0], replace(items[1], title="Only this one moved"), items[2]]
    run(pack, changed_items, monkeypatch)

    rewritten = [
        path for path, stamp in stamps.items()
        if path.stat().st_mtime_ns != stamp
    ]
    assert len(rewritten) == 1, f"{len(rewritten)} objects rewritten, expected 1"


def test_two_sources_reporting_one_feature_do_not_flip_the_object(pack, monkeypatch):
    """Found against production: 70 "updates" on a run that changed nothing.

    The same feature is legitimately listed by two sources with slightly
    different metadata. Both sightings share an identity, so both matched the
    stored object — and both ran the update, writing it twice per harvest and
    leaving it flipping between the two renderings forever.

    The symptom was a permanently dirty git diff and an `updated` count that
    never reached zero, which would have made the weekly diff useless.
    """
    from ke.acquisition.identity import compute_identity

    url = "https://learn.invalid/one-feature"
    identity = compute_identity(canonical_url=url, title="One feature")
    from_html = make_item(title="One feature", url=url)
    from_feed = replace(
        from_html,
        source_name="blog-feed",
        identity=identity,
        published_date=date(2026, 6, 1),   # same feature, different metadata
        summary="The blog's wording for the same feature.",
    )

    run(pack, [from_html, from_feed], monkeypatch)
    directory = only_object(pack)
    first = (directory / "metadata.yaml").read_bytes()

    for _ in range(3):
        report = run(pack, [from_html, from_feed], monkeypatch)
        assert report.updated == [], "the object flipped between two sources"

    assert (directory / "metadata.yaml").read_bytes() == first


def test_a_no_op_harvest_touches_only_the_run_log(pack, monkeypatch):
    """The requirement: repeated harvests create only meaningful diffs.

    The run log is the one deliberate exception — it is appended on every run,
    including empty ones, because a scheduled workflow with no commits for 60
    days is disabled by GitHub (ADR-0031).
    """
    items = [make_item(title=f"Feature delta {n}") for n in range(3)]
    run(pack, items, monkeypatch)

    stamps = {
        path: path.read_bytes()
        for path in sorted(pack.root.rglob("*"))
        if path.is_file()
    }
    run(pack, items, monkeypatch)

    changed = [
        path for path, content in stamps.items() if path.read_bytes() != content
    ]
    # Two files, both deliberate. The run log keeps the cron alive; the digest
    # is written every week so that "no updates" and "the harvest did not run"
    # stay distinguishable. Neither is a knowledge object.
    assert sorted(p.name for p in changed) == ["2026-W31.md", "run-log.md"], (
        f"a no-op harvest changed {[p.name for p in changed]}"
    )
    assert not any("knowledge" in str(p) for p in changed)


def test_an_updated_pack_still_validates(pack, monkeypatch, tmp_path):
    from ke.validate import has_errors, validate_repo

    item = make_item()
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="Updated and still valid")], monkeypatch)

    findings = validate_repo(tmp_path, None)
    assert not has_errors(findings, strict=True), "\n".join(str(f) for f in findings)


# ---------------------------------------------------------------------------
# One update per stored object per run, whichever layer matched (M9-3, TD-19)
# ---------------------------------------------------------------------------
#
# Every assertion here is on `run_id` and revision counts. None reads REV002.
# The M9-3a investigation produced a false positive by trusting a signal it had
# not checked, and REV002 is the detector these objects would later be used to
# validate — asserting on it here would be circular twice over.


TWO_SOURCE_TITLE = "One feature, two sources, two dates"


def _pack_for_update(tmp_path):
    root = tmp_path / "domain-packs" / "p"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: p\nid_prefix: PP\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "PP"}\n')
    return Pack.load(root)


def _harvest(pack, items, clock, monkeypatch):
    monkeypatch.setattr(
        pipeline_module, "discover_all",
        lambda *a, **k: DiscoveryResult(items=list(items)),
    )
    return harvest_pack(pack, clock=clock)


def _runs(obj):
    """How many revisions each run appended. The M9-3a oracle."""
    counts = {}
    for revision in obj.revisions:
        counts[revision.run_id] = counts.get(revision.run_id, 0) + 1
    return counts


RUN_ONE = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))
RUN_TWO = FrozenClock(datetime(2026, 8, 3, 6, 0, tzinfo=timezone.utc))


def _layer_one_and_layer_two(tmp_path, monkeypatch):
    """Store an object, then present two sightings that match it differently.

    `same_url` matches on identity (Layer 1). `other_url` carries the same title
    and summary at a different URL, so its identity key differs and it matches
    on content fingerprint (Layer 2). Both differ from what is stored, so both
    would record a change if both were applied.
    """
    pack = _pack_for_update(tmp_path)
    stored = make_item(title=TWO_SOURCE_TITLE, url="https://x.invalid/one",
                       published_date=date(2026, 5, 1))
    _harvest(pack, [stored], RUN_ONE, monkeypatch)

    same_url = make_item(title=TWO_SOURCE_TITLE, url="https://x.invalid/one",
                         published_date=date(2026, 7, 1))
    other_url = make_item(title=TWO_SOURCE_TITLE, url="https://x.invalid/two",
                          published_date=date(2026, 6, 1))
    assert same_url.identity.key != other_url.identity.key
    return pack, same_url, other_url


def test_t1_one_run_appends_at_most_one_revision_per_object(tmp_path, monkeypatch):
    """T1 — the M9-3a reproduction, now prevented.

    Before the fix this object took two revisions from a single run.
    """
    pack, same_url, other_url = _layer_one_and_layer_two(tmp_path, monkeypatch)

    _harvest(pack, [same_url, other_url], RUN_TWO, monkeypatch)

    obj, _ = load_objects_with_dirs(pack)[0]
    counts = _runs(obj)
    assert max(counts.values()) == 1, f"a run appended twice: {counts}"
    assert counts[RUN_TWO.run_id()] == 1


def test_t2_the_suppressed_sighting_is_counted_not_dropped(tmp_path, monkeypatch):
    """T2 — a rejected sighting is accounted for rather than vanishing."""
    pack, same_url, other_url = _layer_one_and_layer_two(tmp_path, monkeypatch)

    report = _harvest(pack, [same_url, other_url], RUN_TWO, monkeypatch)

    assert report.unchanged >= 1, "the second sighting disappeared silently"


def test_t3_layer_one_behaviour_is_unchanged(tmp_path, monkeypatch):
    """T3 — two sightings sharing one identity key still collapse to one update.

    This is the case the original guard already handled correctly. The fix must
    not alter it.
    """
    pack = _pack_for_update(tmp_path)
    stored = make_item(title="A feature seen twice at one url",
                       url="https://x.invalid/same", published_date=date(2026, 5, 1))
    _harvest(pack, [stored], RUN_ONE, monkeypatch)

    a = make_item(title="A feature seen twice at one url",
                  url="https://x.invalid/same", published_date=date(2026, 7, 1))
    b = make_item(title="A feature seen twice at one url",
                  url="https://x.invalid/same", published_date=date(2026, 7, 1))
    assert a.identity.key == b.identity.key

    _harvest(pack, [a, b], RUN_TWO, monkeypatch)

    obj, _ = load_objects_with_dirs(pack)[0]
    assert max(_runs(obj).values()) == 1


def test_t4_the_winner_does_not_depend_on_discovery_order(tmp_path, monkeypatch):
    """T4 — the test that fails under positional first-wins.

    `sort_items` orders by `published_date`, which is the field these two
    sightings disagree about, so a positional rule would let the disputed value
    decide the winner. Presenting the same pair in both orders must select the
    same sighting.
    """
    outcomes = []
    for index, order in enumerate(("forwards", "backwards")):
        root = tmp_path / order
        (root / "domain-packs").mkdir(parents=True)
        pack, same_url, other_url = _layer_one_and_layer_two(root, monkeypatch)
        items = [same_url, other_url] if order == "forwards" else [other_url, same_url]

        _harvest(pack, items, RUN_TWO, monkeypatch)

        obj, _ = load_objects_with_dirs(pack)[0]
        assert max(_runs(obj).values()) == 1
        outcomes.append((obj.published_date, obj.source_url))

    assert outcomes[0] == outcomes[1], (
        f"discovery order changed the winner: {outcomes[0]} vs {outcomes[1]}"
    )


def test_t5_the_winner_is_the_lowest_identity_key(tmp_path, monkeypatch):
    """T5 — pins the selection rule itself, so changing it is visible.

    Asserted through the stored object rather than by reaching into the stage:
    whichever sighting won is the one whose URL the object now carries.
    """
    pack, same_url, other_url = _layer_one_and_layer_two(tmp_path, monkeypatch)
    expected = min([same_url, other_url], key=lambda i: i.identity.key)

    _harvest(pack, [same_url, other_url], RUN_TWO, monkeypatch)

    obj, _ = load_objects_with_dirs(pack)[0]
    assert obj.published_date == expected.published_date


def test_t6_unrelated_objects_each_still_get_their_own_update(tmp_path, monkeypatch):
    """T6 — grouping must not collapse across distinct objects."""
    pack = _pack_for_update(tmp_path)
    first = make_item(title="First distinct feature", url="https://x.invalid/first",
                      published_date=date(2026, 5, 1))
    second = make_item(title="Second distinct feature", url="https://x.invalid/second",
                       published_date=date(2026, 5, 1))
    _harvest(pack, [first, second], RUN_ONE, monkeypatch)

    first_v2 = make_item(title="First distinct feature", url="https://x.invalid/first",
                         published_date=date(2026, 7, 1))
    second_v2 = make_item(title="Second distinct feature", url="https://x.invalid/second",
                          published_date=date(2026, 7, 1))
    _harvest(pack, [first_v2, second_v2], RUN_TWO, monkeypatch)

    stored = load_objects_with_dirs(pack)
    assert len(stored) == 2
    for obj, _ in stored:
        assert max(_runs(obj).values()) == 1
        assert obj.published_date == date(2026, 7, 1), "an object missed its update"
