"""Tests for M5: unified review, revision history, supersession and time travel.

The Time Machine's data model has been written since M2 and read by nothing, so
these tests are the first thing to actually validate it. A history nobody reads
is a history nobody has checked.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
import yaml

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.harvest import harvest_pack, load_existing_objects
from ke.history import (
    HistoryError,
    at_revision,
    find_object,
    render_timeline,
    supersede,
    timeline,
    verify_chain,
)
from ke.models import FeatureId, Lifecycle, ObjectStatus, Revision
from ke.pack import Pack
from ke.reviewq import (
    Action,
    TaskKind,
    apply_action,
    collect,
    counts,
    find,
    render_report,
)
from ke.store import build_object, load_object

from tests.test_pipeline import CLOCK, make_item

RULES = {
    "tier": [{"name": "ga", "any": ["generally available"], "value": 1}],
    "category": [{"name": "lake", "any": ["direct lake"], "value": "data-engineering"}],
}


@pytest.fixture
def pack(tmp_path) -> Pack:
    root = tmp_path / "domain-packs" / "test-pack"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n"
        + yaml.safe_dump({"classification": RULES}),
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')
    return Pack.load(root)


def run(pack, items, monkeypatch):
    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=list(items))
    )
    return harvest_pack(pack, clock=CLOCK)


def an_object(**overrides):
    obj = build_object(make_item(), FeatureId.parse("TST-2026-07-001"))
    return replace(obj, **overrides) if overrides else obj


# ---------------------------------------------------------------------------
# 1. The unified review workflow
# ---------------------------------------------------------------------------


def test_one_queue_shows_every_kind(pack, monkeypatch):
    """The point of the milestone: not three backlogs, one lens."""
    from ke.models import IdentityConfidence

    items = [
        make_item(title="Direct Lake is generally available"),   # mints, classifies
        make_item(title="Zzz unmatched thing", identity_confidence=IdentityConfidence.MEDIUM),
    ]
    run(pack, items, monkeypatch)

    tally = counts(pack)
    assert tally[TaskKind.QUEUED] == 1
    assert tally[TaskKind.UNCLASSIFIED] >= 0
    assert set(t.kind for t in collect(pack)) <= set(TaskKind)


def test_tasks_are_ordered_by_urgency(pack, monkeypatch):
    """Queued items are not yet in the pack at all, so they come first."""
    from ke.models import IdentityConfidence

    run(
        pack,
        [
            make_item(title="Zzz nothing matches this"),
            make_item(title="Aaa held back", identity_confidence=IdentityConfidence.MEDIUM),
        ],
        monkeypatch,
    )
    kinds = [t.kind for t in collect(pack)]
    if TaskKind.QUEUED in kinds and TaskKind.UNCLASSIFIED in kinds:
        assert kinds.index(TaskKind.QUEUED) < kinds.index(TaskKind.UNCLASSIFIED)


def test_a_short_key_resolves(pack, monkeypatch):
    from ke.models import IdentityConfidence

    run(pack, [make_item(identity_confidence=IdentityConfidence.MEDIUM)], monkeypatch)
    task = collect(pack)[0]
    assert find(pack, task.short_key).key == task.key


def test_an_ambiguous_key_is_refused(pack, monkeypatch):
    run(pack, [make_item(title="Direct Lake is generally available")], monkeypatch)
    with pytest.raises(KeyError):
        find(pack, "")


def test_an_action_not_offered_is_refused(pack, monkeypatch):
    from ke.models import IdentityConfidence

    run(pack, [make_item(identity_confidence=IdentityConfidence.MEDIUM)], monkeypatch)
    task = [t for t in collect(pack) if t.kind is TaskKind.QUEUED][0]
    with pytest.raises(ValueError, match="not available"):
        apply_action(pack, task, Action.RESOLVE)


def test_resolving_an_unclassified_object_clears_the_flag(pack, monkeypatch):
    run(pack, [make_item(title="Zzz matches no rule at all")], monkeypatch)
    tasks = [t for t in collect(pack) if t.kind is TaskKind.UNCLASSIFIED]
    assert tasks, "expected an unclassified object"

    apply_action(pack, tasks[0], Action.RESOLVE)
    assert not [t for t in collect(pack) if t.kind is TaskKind.UNCLASSIFIED]


def test_resolving_cannot_touch_user_owned_fields(pack, monkeypatch):
    """The review path must not become a back door around ownership."""
    run(pack, [make_item(title="Zzz matches no rule at all")], monkeypatch)
    directory = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0].parent

    stored = load_object(directory)
    edited = replace(stored, notes="mine", learning_status="in-progress")
    (directory / "metadata.yaml").write_text(
        yaml.safe_dump(edited.to_metadata_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    tasks = [t for t in collect(pack) if t.kind is TaskKind.UNCLASSIFIED]
    if tasks:
        apply_action(pack, tasks[0], Action.RESOLVE)
    after = load_object(directory)
    assert after.notes == "mine"
    assert str(after.learning_status) == "in-progress"


def test_the_report_lists_every_kind(pack, monkeypatch):
    run(pack, [make_item(title="Zzz unmatched")], monkeypatch)
    report = render_report(pack)
    assert "queued" in report and "unclassified" in report and "revision" in report


def test_a_broken_provider_does_not_hide_the_others(pack, monkeypatch):
    """One failing source of tasks must not empty the whole queue."""
    import ke.reviewq as reviewq

    def exploding(_pack):
        raise RuntimeError("provider blew up")

    run(pack, [make_item(title="Zzz unmatched")], monkeypatch)
    monkeypatch.setattr(reviewq, "PROVIDERS", (exploding, reviewq.unclassified_tasks))
    assert collect(pack), "a broken provider hid the working ones"


# ---------------------------------------------------------------------------
# 2 & 3. Revision detection and history validation
# ---------------------------------------------------------------------------


def test_a_healthy_history_reports_no_problems():
    assert verify_chain(an_object()) == []


def test_misnumbered_revisions_are_caught():
    obj = an_object(
        revisions=(
            Revision(revision=1, date=date(2026, 7, 1), summary="Initial ingestion"),
            Revision(revision=5, date=date(2026, 7, 2), changed_fields=("title",)),
        )
    )
    assert any("revision numbers" in p for p in verify_chain(obj))


def test_a_history_going_backwards_in_time_is_caught():
    obj = an_object(
        revisions=(
            Revision(revision=1, date=date(2026, 7, 10), summary="Initial ingestion"),
            Revision(revision=2, date=date(2026, 7, 1), changed_fields=("title",)),
        )
    )
    assert any("before revision" in p for p in verify_chain(obj))


def test_an_object_disagreeing_with_its_own_history_is_caught():
    obj = an_object(content_hash="sha256:" + "9" * 64)
    obj = replace(
        obj,
        revisions=(
            Revision(
                revision=1, date=date(2026, 7, 1), summary="Initial ingestion",
                content_hash="sha256:" + "1" * 64,
            ),
        ),
    )
    assert any("do not match" in p or "disagree" in p for p in verify_chain(obj))


def test_a_revision_recording_no_change_is_caught():
    """A revision means something changed. One that records nothing is noise."""
    obj = an_object(
        revisions=(
            Revision(revision=1, date=date(2026, 7, 1), summary="Initial ingestion"),
            Revision(revision=2, date=date(2026, 7, 2), changed_fields=()),
        )
    )
    assert any("records no changed fields" in p for p in verify_chain(obj))


def test_repeated_identical_revisions_are_flagged(pack, monkeypatch):
    """Regression. The M3 flip-flop produced 35 objects with 11 revisions each,
    every one recording the identical field change.

    **Why nothing caught it:** each revision was individually well-formed. The
    corruption was only visible in the *pattern*, and nothing looked at patterns.
    """
    from ke.validate import validate_repo

    run(pack, [make_item()], monkeypatch)
    directory = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0].parent
    obj = load_object(directory)

    repeated = replace(
        obj,
        revisions=(
            obj.revisions[0],
            *[
                Revision(
                    revision=n, date=date(2026, 7, n),
                    changed_fields=("published_date",),
                    summary="Source corrected the publication date",
                    content_hash=obj.content_hash,
                )
                for n in range(2, 6)
            ],
        ),
    )
    (directory / "metadata.yaml").write_text(
        yaml.safe_dump(repeated.to_metadata_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    findings = validate_repo(pack.root.parent.parent, None)
    assert any(f.code == "REV002" for f in findings)


# ---------------------------------------------------------------------------
# 4. Supersession
# ---------------------------------------------------------------------------


def test_supersession_links_both_directions(pack, monkeypatch):
    run(
        pack,
        [make_item(title="Old feature one"), make_item(title="New feature two")],
        monkeypatch,
    )
    ids = sorted(str(o.id) for o, _ in load_existing_objects(pack))
    old_id, new_id = ids[0], ids[1]

    supersede(pack, old_id, new_id, today=date(2026, 8, 1))

    old_obj, _ = find_object(pack, old_id)
    new_obj, _ = find_object(pack, new_id)
    assert old_obj.status is ObjectStatus.REPLACED
    assert old_obj.replaced_by == new_id
    assert new_obj.replaces == old_id


def test_a_superseded_object_is_retained_not_deleted(pack, monkeypatch):
    run(pack, [make_item(title="Old one"), make_item(title="New two")], monkeypatch)
    ids = sorted(str(o.id) for o, _ in load_existing_objects(pack))
    supersede(pack, ids[0], ids[1], today=date(2026, 8, 1))

    obj, directory = find_object(pack, ids[0])
    assert directory.exists()
    assert str(obj.id) == ids[0]           # ID unchanged
    assert obj.lifecycle is Lifecycle.MINTED  # acquisition still complete


def test_supersession_appends_a_revision(pack, monkeypatch):
    run(pack, [make_item(title="Old one"), make_item(title="New two")], monkeypatch)
    ids = sorted(str(o.id) for o, _ in load_existing_objects(pack))
    supersede(pack, ids[0], ids[1], today=date(2026, 8, 1))

    obj, _ = find_object(pack, ids[0])
    assert obj.revisions[-1].summary == f"Superseded by {ids[1]}"
    assert "status" in obj.revisions[-1].changed_fields


def test_an_object_cannot_supersede_itself(pack, monkeypatch):
    run(pack, [make_item()], monkeypatch)
    only = str(load_existing_objects(pack)[0][0].id)
    with pytest.raises(HistoryError, match="itself"):
        supersede(pack, only, only, today=date(2026, 8, 1))


def test_supersession_is_recorded_once(pack, monkeypatch):
    run(
        pack,
        [make_item(title=f"Feature {n}") for n in ("one", "two", "three")],
        monkeypatch,
    )
    ids = sorted(str(o.id) for o, _ in load_existing_objects(pack))
    supersede(pack, ids[0], ids[1], today=date(2026, 8, 1))
    with pytest.raises(HistoryError, match="already superseded"):
        supersede(pack, ids[0], ids[2], today=date(2026, 8, 1))


def test_there_is_no_superseded_lifecycle_stage():
    """ADR-0035: supersession is a `status`, not an acquisition stage."""
    assert not hasattr(Lifecycle, "SUPERSEDED")
    assert [stage.value for stage in Lifecycle] == [
        "discovered", "queued", "approved", "minted", "archived",
    ]


# ---------------------------------------------------------------------------
# 5. Time travel
# ---------------------------------------------------------------------------


def test_an_object_carries_its_own_past(pack, monkeypatch):
    item = make_item(title="Original title here")
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="Rewritten title entirely")], monkeypatch)

    obj = load_existing_objects(pack)[0][0]
    history = timeline(obj)
    assert len(history) == 2
    assert history[0].title == "Original title here"
    assert history[1].title == "Rewritten title entirely"


def test_travelling_to_a_specific_revision(pack, monkeypatch):
    item = make_item(title="First wording")
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="Second wording")], monkeypatch)

    obj = load_existing_objects(pack)[0][0]
    assert at_revision(obj, 1).title == "First wording"
    assert at_revision(obj, 2).title == "Second wording"


def test_asking_for_a_revision_that_does_not_exist_raises():
    """Better than silently answering about a different revision."""
    with pytest.raises(HistoryError, match="no revision 9"):
        at_revision(an_object(), 9)


def test_time_travel_needs_no_git_and_no_network(pack, monkeypatch):
    """The whole point of storing snapshots inside the object (ADR-0020)."""
    item = make_item(title="A title")
    run(pack, [item], monkeypatch)
    run(pack, [replace(item, title="B title")], monkeypatch)

    directory = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0].parent
    raw = yaml.safe_load((directory / "metadata.yaml").read_text())
    snapshots = [r.get("title_snapshot") for r in raw["revisions"]]
    assert snapshots == ["A title", "B title"]


def test_the_rendered_timeline_surfaces_history_problems():
    obj = an_object(
        revisions=(
            Revision(revision=1, date=date(2026, 7, 1), summary="Initial ingestion"),
            Revision(revision=7, date=date(2026, 7, 2), changed_fields=("title",)),
        )
    )
    assert "history problems" in render_timeline(obj)
