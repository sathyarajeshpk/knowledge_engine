"""Tests for M2: minting, deduplication, storage, review and the full pipeline.

Every test runs offline against a temporary pack. The properties pinned here are
the ones that are unrecoverable if they break -- a reused Feature ID, a lost
queued item, a half-written object -- rather than the ones that merely produce a
wrong number.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import yaml

from ke.acquisition.identity import compute_identity
from ke.clock import FrozenClock
from ke.dedupe import SeenIndex, Verdict, classify, jaccard
from ke.harvest import harvest_pack, load_existing_objects
from ke.ids import IdError, IdRegistry
from ke.indexer import render_index, write_indexes
from ke.models import (
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    FeatureId,
    IdentityConfidence,
    Lifecycle,
    Provenance,
    RawItem,
    SourceAuthority,
    SourceRepresentation,
)
from ke.pack import Pack
from ke.review import ReviewQueue
from ke.store import build_object, render_feature_document, write_object

CLOCK = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))


def make_item(title="Direct Lake general availability", url=None, **overrides) -> RawItem:
    # Derived from the WHOLE title: deriving it from the first word made three
    # distinct fixtures share one URL, and therefore one identity.
    slug = "-".join(title.lower().split())
    url = url if url is not None else f"https://learn.invalid/{slug}"
    identity = compute_identity(canonical_url=url, title=title)
    defaults = dict(
        source_name="fabric-whats-new",
        source_url=url,
        announcement_url=url,
        source_authority=SourceAuthority.OFFICIAL_MICROSOFT,
        title=title,
        summary=f"A short original summary about {title}.",
        discovered_date=date(2026, 8, 2),
        published_date=date(2026, 7, 1),
        date_confidence=DateConfidence.EXACT,
        date_precision=DatePrecision.MONTH,
        identity=identity,
        identity_confidence=IdentityConfidence.HIGH,
        lifecycle=Lifecycle.APPROVED,
        provenance=Provenance(
            source_name="fabric-whats-new",
            source_representation=SourceRepresentation.HTML,
            adapter_type=AdapterType.HTML,
            parser_version=1,
            extraction_method=ExtractionMethod.HTML_TABLE_ROW,
            discovered_at=CLOCK.now(),
            identity_basis=identity.basis,
            identity_key=identity.key,
            run_id=CLOCK.run_id(),
        ),
    )
    defaults.update(overrides)
    return RawItem(**defaults)


# ---------------------------------------------------------------------------
# Feature ID minting — the irreversible part
# ---------------------------------------------------------------------------


def test_ids_are_minted_from_the_publication_month(tmp_path):
    registry = IdRegistry(prefix="TST")
    feature_id = registry.mint(make_item())
    assert str(feature_id) == "TST-2026-07-001"


def test_ids_fall_back_to_first_discovery_not_this_run(tmp_path):
    """Review latency must never move a permanent identifier."""
    item = make_item(
        published_date=None,
        date_confidence=DateConfidence.INFERRED,
        discovered_date=date(2026, 11, 20),      # approved months later
        first_discovered_date=date(2026, 7, 3),  # when it actually appeared
    )
    assert str(IdRegistry(prefix="TST").mint(item)) == "TST-2026-07-001"


def test_counters_are_per_month(tmp_path):
    registry = IdRegistry(prefix="TST")
    july = registry.mint(make_item(title="A one", published_date=date(2026, 7, 1)))
    august = registry.mint(make_item(title="B two", published_date=date(2026, 8, 1)))
    july_again = registry.mint(make_item(title="C three", published_date=date(2026, 7, 1)))
    assert (str(july), str(august), str(july_again)) == (
        "TST-2026-07-001", "TST-2026-08-001", "TST-2026-07-002",
    )


def test_a_registry_round_trips(tmp_path):
    path = tmp_path / "id-registry.json"
    registry = IdRegistry(prefix="TST")
    feature_id = registry.mint(make_item())
    registry.record(feature_id, "2026/07/TST-2026-07-001-x")
    registry.save(path)

    reloaded = IdRegistry.load(path, "TST")
    assert reloaded.counters == registry.counters
    assert reloaded.path_for(feature_id) == "2026/07/TST-2026-07-001-x"


def test_an_inconsistent_registry_refuses_to_load(tmp_path):
    """Minting against a half-read registry is how IDs get reused."""
    path = tmp_path / "id-registry.json"
    path.write_text(json.dumps({
        "prefix": "TST",
        "counters": {"2026-07": 1},
        "paths": {"TST-2026-07-009": "2026/07/whatever"},  # 009 > counter 1
    }))
    with pytest.raises(IdError, match="inconsistent"):
        IdRegistry.load(path, "TST")


def test_a_malformed_registry_refuses_to_load(tmp_path):
    path = tmp_path / "id-registry.json"
    path.write_text("{not json")
    with pytest.raises(IdError):
        IdRegistry.load(path, "TST")


def test_registry_output_is_deterministic(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    for path in (first, second):
        registry = IdRegistry(prefix="TST")
        for title in ("Zeta feature", "Alpha feature", "Mu feature"):
            fid = registry.mint(make_item(title=title))
            registry.record(fid, f"2026/07/{fid}")
        registry.save(path)
    assert first.read_bytes() == second.read_bytes()


# ---------------------------------------------------------------------------
# Deduplication — exact layers resolve, judgement layers only flag
# ---------------------------------------------------------------------------


def test_a_known_identity_is_not_new():
    item = make_item()
    seen = SeenIndex()
    seen.remember(item, "TST-2026-07-001")
    assert classify([item], seen)[0].verdict is Verdict.KNOWN_IDENTITY


def test_republication_at_a_new_url_is_caught_by_content():
    original = make_item()
    seen = SeenIndex()
    seen.remember(original, "TST-2026-07-001")
    moved = make_item(url="https://learn.invalid/moved-elsewhere")
    assert classify([moved], seen)[0].verdict is Verdict.KNOWN_CONTENT


def test_a_near_duplicate_is_flagged_but_still_stored():
    """ADR-0014: never auto-drop. A silent drop leaves no trace."""
    seen = SeenIndex()
    seen.remember(make_item(title="Direct Lake general availability"), "TST-2026-07-001")
    decision = classify(
        [make_item(title="Direct Lake availability general", url="https://x.invalid/b")],
        seen,
    )[0]
    assert decision.verdict is Verdict.NEAR_DUPLICATE
    assert decision.is_new is True          # still stored
    assert decision.needs_review is True    # but flagged


def test_duplicates_within_one_run_are_collapsed():
    """The same feature legitimately appears twice on one page."""
    item = make_item()
    decisions = classify([item, item], SeenIndex())
    assert [d.verdict for d in decisions] == [Verdict.NEW, Verdict.KNOWN_IDENTITY]


def test_jaccard_is_symmetric_and_bounded():
    assert jaccard("a b c", "a b c") == 1.0
    assert jaccard("a b", "c d") == 0.0
    assert jaccard("a b", "b c") == jaccard("b c", "a b")


def test_a_damaged_seen_index_degrades_rather_than_fails(tmp_path):
    """Re-examining known items is cheap; refusing to run is not."""
    path = tmp_path / "seen.json"
    path.write_text("{corrupt")
    assert SeenIndex.load(path).identities == {}


# ---------------------------------------------------------------------------
# Storage — an object is a pair of files or it is nothing
# ---------------------------------------------------------------------------


def test_an_object_is_written_as_a_pair(tmp_path):
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(tmp_path, obj, item.summary, max_summary_words=120)
    assert (directory / "feature.md").exists()
    assert (directory / "metadata.yaml").exists()


def test_writing_never_leaves_half_an_object(tmp_path, monkeypatch):
    """The failure that produced 222 orphaned feature.md files."""
    import ke.store as store_module

    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    monkeypatch.setattr(
        store_module, "render_metadata",
        lambda _obj: (_ for _ in ()).throw(RuntimeError("serialisation blew up")),
    )
    with pytest.raises(RuntimeError):
        write_object(tmp_path, obj, item.summary, max_summary_words=120)

    assert not list(tmp_path.rglob("feature.md")), "a half-object was left behind"


def test_an_existing_object_is_never_overwritten(tmp_path):
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    write_object(tmp_path, obj, item.summary, max_summary_words=120)
    with pytest.raises(FileExistsError):
        write_object(tmp_path, obj, item.summary, max_summary_words=120)


def test_the_feature_document_respects_the_copyright_budget():
    """`ke validate` counts every word below the heading, source line included."""
    item = make_item(summary=" ".join(["word"] * 400))
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    text = render_feature_document(obj, item.summary, max_words=120)
    body = text.split("\n", 1)[1]
    assert len(body.split()) <= 120


def test_metadata_has_no_yaml_anchors(tmp_path):
    """A human reads this in the GitHub UI; `&id001` is noise."""
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(tmp_path, obj, item.summary, max_summary_words=120)
    text = (directory / "metadata.yaml").read_text()
    assert "&id0" not in text and "*id0" not in text


def test_stored_metadata_round_trips(tmp_path):
    from ke.models import KnowledgeObject

    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    directory = write_object(tmp_path, obj, item.summary, max_summary_words=120)
    raw = yaml.safe_load((directory / "metadata.yaml").read_text())
    assert KnowledgeObject.from_metadata_dict(raw).id == obj.id


# ---------------------------------------------------------------------------
# The review queue — held back, never dropped
# ---------------------------------------------------------------------------


def test_queuing_preserves_the_first_discovery_date(tmp_path):
    """Re-queuing weekly must not push a Feature ID's month forward."""
    queue = ReviewQueue(entries={})
    item = make_item(discovered_date=date(2026, 7, 3))
    queue.enqueue(item)

    later = make_item(discovered_date=date(2026, 9, 30))
    assert queue.enqueue(later) is False        # already present
    entry = queue.entries[item.identity.key]
    assert entry.first_discovered_date == date(2026, 7, 3)


def test_approve_then_archive_is_rejected():
    queue = ReviewQueue(entries={})
    item = make_item()
    queue.enqueue(item)
    queue.approve(item.identity.key)
    queue.archive(item.identity.key)           # approved -> archived is legal
    with pytest.raises(ValueError):
        queue.approve(item.identity.key)       # archived is terminal


def test_a_short_key_from_the_report_can_be_pasted_back():
    """The queue prints the digest without its `sha256:` prefix."""
    queue = ReviewQueue(entries={})
    item = make_item()
    queue.enqueue(item)
    short = item.identity.key[7:19]
    assert queue.approve(short).title == item.title


def test_an_ambiguous_key_is_refused_rather_than_guessed():
    queue = ReviewQueue(entries={})
    for title in ("Alpha one", "Beta two"):
        queue.enqueue(make_item(title=title))
    with pytest.raises(KeyError, match="matches 2"):
        queue.approve("")


def test_the_queue_round_trips(tmp_path):
    path = tmp_path / "review-queue.json"
    queue = ReviewQueue(entries={})
    queue.enqueue(make_item())
    queue.save(path)
    assert len(ReviewQueue.load(path).entries) == 1


def test_a_damaged_queue_fails_loudly(tmp_path):
    """Unlike seen.json, losing the queue loses human decisions and dates."""
    path = tmp_path / "review-queue.json"
    path.write_text("{corrupt")
    with pytest.raises(ValueError):
        ReviewQueue.load(path)


# ---------------------------------------------------------------------------
# Indexes — derived data, always rebuilt
# ---------------------------------------------------------------------------


def test_index_rebuild_is_byte_identical(tmp_path):
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    first = render_index([(obj, "../knowledge/2026/07/x")], "test-pack")
    second = render_index([(obj, "../knowledge/2026/07/x")], "test-pack")
    assert first == second


def test_indexes_are_written(tmp_path):
    item = make_item()
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    written = write_indexes(tmp_path, [(obj, "../x")], [], "test-pack")
    assert {p.name for p in written} == {
        "INDEX.md", "by-source.md", "by-month.md", "review-queue.md",
    }


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def harvestable_pack(tmp_path) -> Pack:
    root = tmp_path / "domain-packs" / "test-pack"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\n"
        "dedupe:\n  near_duplicate_jaccard: 0.85\n"
        "sources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')
    return Pack.load(root)


def _harvest_with(pack, items, monkeypatch):
    """Run the pipeline with discovery replaced by a fixed item list."""
    import ke.pipeline as pipeline_module
    from ke.acquisition import DiscoveryResult

    monkeypatch.setattr(
        pipeline_module, "discover_all",
        lambda *a, **k: DiscoveryResult(items=list(items)),
    )
    return harvest_pack(pack, clock=CLOCK)


def test_the_pipeline_mints_stores_and_indexes(harvestable_pack, monkeypatch):
    report = _harvest_with(harvestable_pack, [make_item()], monkeypatch)

    assert len(report.minted) == 1
    assert report.errors == []
    objects = load_existing_objects(harvestable_pack)
    assert len(objects) == 1
    assert (harvestable_pack.indexes_dir / "INDEX.md").exists()


def test_a_second_harvest_changes_nothing(harvestable_pack, monkeypatch):
    """Idempotency: the property that makes a weekly cron safe."""
    items = [make_item(title="Alpha one"), make_item(title="Beta two")]
    _harvest_with(harvestable_pack, items, monkeypatch)
    registry_before = harvestable_pack.registry_path.read_bytes()

    second = _harvest_with(harvestable_pack, items, monkeypatch)

    assert second.minted == []
    assert harvestable_pack.registry_path.read_bytes() == registry_before


def test_low_confidence_items_are_queued_not_minted(harvestable_pack, monkeypatch):
    queued_item = make_item(
        title="Ambiguous thing",
        identity_confidence=IdentityConfidence.MEDIUM,
        lifecycle=Lifecycle.QUEUED,
    )
    report = _harvest_with(harvestable_pack, [make_item(), queued_item], monkeypatch)

    assert len(report.minted) == 1
    assert report.queued == 1
    queue = ReviewQueue.load(harvestable_pack.state_dir / "review-queue.json")
    assert len(queue.pending) == 1


def test_queuing_never_blocks_the_rest(harvestable_pack, monkeypatch):
    """One ambiguous row must not stall a harvest."""
    items = [
        make_item(title=f"Feature number {n}") for n in range(3)
    ] + [make_item(title="Held back", identity_confidence=IdentityConfidence.MEDIUM)]
    report = _harvest_with(harvestable_pack, items, monkeypatch)
    assert len(report.minted) == 3 and report.queued == 1


def test_an_approved_item_mints_on_the_next_harvest(harvestable_pack, monkeypatch):
    item = make_item(
        title="Ambiguous thing", identity_confidence=IdentityConfidence.MEDIUM
    )
    _harvest_with(harvestable_pack, [item], monkeypatch)

    queue_path = harvestable_pack.state_dir / "review-queue.json"
    queue = ReviewQueue.load(queue_path)
    queue.approve(item.identity.key)
    queue.save(queue_path)

    report = _harvest_with(harvestable_pack, [item], monkeypatch)
    assert len(report.minted) == 1
    assert ReviewQueue.load(queue_path).entries == {}


def test_the_run_log_is_appended_even_when_nothing_is_found(harvestable_pack, monkeypatch):
    """A quiet week must still produce a commit, or GitHub disables the cron."""
    _harvest_with(harvestable_pack, [], monkeypatch)
    log = (harvestable_pack.state_dir / "run-log.md").read_text()
    assert CLOCK.run_id() in log


def test_one_bad_item_does_not_lose_the_others(harvestable_pack, monkeypatch):
    import ke.pipeline as pipeline_module

    real_build = pipeline_module.build_object
    calls = {"n": 0}

    def flaky(item, feature_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad item")
        return real_build(item, feature_id, **kwargs)

    monkeypatch.setattr(pipeline_module, "build_object", flaky)
    report = _harvest_with(
        harvestable_pack,
        [make_item(title="Bad one"), make_item(title="Good two")],
        monkeypatch,
    )
    assert len(report.minted) == 1
    assert len(report.errors) == 1
