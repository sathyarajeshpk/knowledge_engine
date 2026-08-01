"""Tests for M4: deterministic classification and the staged pipeline.

Classification is the first code to write the **engine-proposed** field class,
which is the one `overrides` has to hold against. It is also the first stage
whose output could plausibly churn every object in the pack, so most of these
tests are about *not* writing rather than about writing.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.classify import (
    Proposal,
    _is_unset,
    applicable,
    extract_version,
    propose,
    unmatched_fields,
)
from ke.harvest import harvest_pack, load_existing_objects
from ke.models import Difficulty, FeatureId, LearningPriority, Tier, Workload
from ke.pack import Pack
from ke.pipeline import STAGES, HarvestContext, run_stages
from ke.store import build_object, load_object

from tests.test_pipeline import CLOCK, make_item

RULES = {
    "tier": [
        {"name": "ga", "any": ["generally available"], "none": ["preview"], "value": 1},
        {"name": "preview", "any": ["preview"], "value": 2},
    ],
    "category": [
        {"name": "warehouse", "any": ["warehouse"], "value": "data-warehouse"},
        {"name": "lake", "any": ["direct lake", "onelake"], "value": "data-engineering"},
    ],
    "learning_priority": [{"name": "core", "any": ["direct lake"], "value": "high"}],
    "difficulty": [{"name": "adv", "any": ["api"], "value": "advanced"}],
    "workload": [{"name": "heavy", "any": ["migration"], "value": "heavy"}],
    "tags": [
        {"any": ["direct lake"], "value": "direct-lake"},
        {"any": ["preview"], "value": "preview"},
    ],
}


def an_object(title="Direct Lake general availability", **overrides):
    item = make_item(title=title)
    obj = build_object(item, FeatureId.parse("TST-2026-07-001"))
    return replace(obj, **overrides) if overrides else obj


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


# ---------------------------------------------------------------------------
# Determinism — the property that keeps the weekly diff readable
# ---------------------------------------------------------------------------


def test_the_same_object_and_rules_always_classify_the_same():
    obj = an_object()
    first = [str(p) for p in propose(obj, RULES)]
    second = [str(p) for p in propose(obj, RULES)]
    assert first == second


def test_first_matching_rule_wins():
    """Order is the pack author's priority statement, not a scoring input."""
    obj = an_object(title="Warehouse and Direct Lake together")
    category = [p for p in propose(obj, RULES) if p.field == "category"][0]
    assert category.value == "data-warehouse"  # listed first


def test_an_exclusion_blocks_a_rule():
    obj = an_object(title="Direct Lake generally available (preview)")
    tier = [p for p in propose(obj, RULES) if p.field == "tier"][0]
    assert tier.value is Tier.LEARN_SOON  # the GA rule was excluded by "preview"


def test_every_proposal_records_which_rule_made_it():
    """"Why is this tier 1?" must be answerable without reading engine code."""
    for proposal in propose(an_object(), RULES):
        assert proposal.matched_by


def test_tags_are_sorted_so_order_is_never_a_diff():
    obj = an_object(title="Direct Lake preview for everyone")
    tags = [p for p in propose(obj, RULES) if p.field == "tags"][0]
    assert tags.value == tuple(sorted(tags.value))


def test_yaml_scalars_become_the_enums_the_model_expects():
    obj = an_object(title="Direct Lake generally available with api and migration")
    by_field = {p.field: p.value for p in propose(obj, RULES)}
    assert by_field["tier"] is Tier.ACT_NOW
    assert by_field["learning_priority"] is LearningPriority.HIGH
    assert by_field["difficulty"] is Difficulty.ADVANCED
    assert by_field["workload"] is Workload.HEAVY


def test_an_invalid_rule_value_degrades_one_field_only():
    """A bad rule must not fail every object it touches."""
    bad = {"tier": [{"name": "broken", "any": ["direct lake"], "value": "not-a-tier"}]}
    proposals = propose(an_object(), bad)
    tier = [p for p in proposals if p.field == "tier"]
    assert tier and tier[0].value is None
    assert any(p.field == "reading_time" for p in proposals)  # the rest still works


def test_release_waves_are_normalised_to_one_spelling():
    assert extract_version(an_object(title="Shipped in 2026 Release Wave 1")) == (
        "2026 Release Wave 1"
    )
    assert extract_version(an_object(title="Nothing here")) is None


# ---------------------------------------------------------------------------
# Regression — the two bugs this milestone produced
# ---------------------------------------------------------------------------


def test_a_field_at_its_default_counts_as_unset():
    """M4. `applicable` tested falsiness, so enum defaults looked already-set.

    `tier` defaults to `AWARENESS` (3) and `difficulty` to `INTERMEDIATE` —
    both truthy — so classification silently wrote nothing. **All 222 objects
    came back `tier: 3`**, which was the default rather than a decision, and it
    looked exactly like a working classifier.

    **Why it was invisible:** the pipeline reported "222 classified" because
    `reading_time` did change. The counts looked healthy.
    """
    obj = an_object()
    assert _is_unset(obj, "tier") is True          # still Tier.AWARENESS
    assert _is_unset(obj, "difficulty") is True    # still Difficulty.INTERMEDIATE
    assert _is_unset(obj, "category") is True      # None
    assert _is_unset(obj, "tags") is True          # ()

    decided = replace(obj, tier=Tier.ACT_NOW, category="data-warehouse")
    assert _is_unset(decided, "tier") is False
    assert _is_unset(decided, "category") is False


def test_classification_does_not_feed_on_its_own_output(pack, monkeypatch):
    """M4. `_haystack` included `category` and `tags` — its own output.

    So a second harvest matched rules the first could not, and **reclassified
    four more objects than the first run**. Classification must be a pure
    function of the knowledge, not of whether it has run before.
    """
    # A rule set where classification's own output would match a rule the raw
    # knowledge does not: `data-engineering` is a *category value*, and the
    # workload rule looks for it. With the output fed back in, the second run
    # sees it and fires a rule the first could not.
    feedback_rules = dict(RULES)
    feedback_rules["workload"] = [
        {"name": "self-referential", "any": ["data-engineering"], "value": "heavy"}
    ]
    (pack.root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n"
        + yaml.safe_dump({"classification": feedback_rules}),
        encoding="utf-8",
    )
    reloaded = Pack.load(pack.root)

    items = [make_item(title=f"Direct Lake feature {n}") for n in range(3)]
    run(reloaded, items, monkeypatch)
    first = {
        o.id: (o.tier, o.category, o.tags, o.workload)
        for o, _ in load_existing_objects(reloaded)
    }

    for _ in range(3):
        report = run(reloaded, items, monkeypatch)
        assert report.classified == [], "classification reclassified its own output"

    assert {
        o.id: (o.tier, o.category, o.tags, o.workload)
        for o, _ in load_existing_objects(reloaded)
    } == first


# ---------------------------------------------------------------------------
# Override handling — the user's veto
# ---------------------------------------------------------------------------


def test_a_locked_field_is_never_proposed_over():
    obj = an_object(tier=Tier.AWARENESS, overrides=("tier",))
    updates = applicable(obj, propose(obj, RULES))
    assert "tier" not in updates


def test_an_already_decided_field_is_left_alone():
    """Engine-proposed means write-if-absent, so a rule tweak cannot churn."""
    obj = an_object(category="administration")
    updates = applicable(obj, propose(obj, RULES))
    assert "category" not in updates


def test_reading_time_tracks_the_text_because_it_is_engine_owned():
    obj = an_object(reading_time=99)
    updates = applicable(obj, propose(obj, RULES))
    assert updates["reading_time"] != 99


def test_classification_cannot_write_a_user_owned_field(pack, monkeypatch):
    """The ownership guard, exercised through the classification stage."""
    items = [make_item(title="Direct Lake is generally available")]
    run(pack, items, monkeypatch)
    directory = sorted(pack.knowledge_dir.rglob("metadata.yaml"))[0].parent

    stored = load_object(directory)
    edited = replace(stored, notes="my notes", learning_status="in-progress")
    (directory / "metadata.yaml").write_text(
        yaml.safe_dump(edited.to_metadata_dict(), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )

    run(pack, items, monkeypatch)
    after = load_object(directory)
    assert after.notes == "my notes"
    assert str(after.learning_status) == "in-progress"


def test_an_object_matching_no_rule_is_flagged_not_guessed():
    """ADR-0010: never a silent guess."""
    obj = an_object(title="Something entirely unrelated to any rule")
    assert set(unmatched_fields(propose(obj, RULES))) == {"tier", "category"}


# ---------------------------------------------------------------------------
# The staged pipeline
# ---------------------------------------------------------------------------


def test_the_stage_list_is_in_the_documented_order():
    """Order is a safety property (ADR-0031), so it is asserted."""
    names = [s.__name__ for s in STAGES]
    assert names == [
        "discover", "load_state", "deduplicate", "update_existing",
        "gate_and_mint", "classify_objects", "persist_state",
        "rebuild_indexes", "append_run_log",
    ]
    assert names.index("deduplicate") < names.index("gate_and_mint")
    assert names.index("update_existing") < names.index("gate_and_mint")
    assert names.index("gate_and_mint") < names.index("persist_state")
    assert names.index("classify_objects") < names.index("rebuild_indexes")


def test_a_stage_that_raises_stops_the_run_rather_than_half_building(pack):
    def exploding(ctx):
        raise RuntimeError("stage blew up")

    ctx = HarvestContext(pack=pack, clock=CLOCK, report=_report(pack))
    report = run_stages(ctx, (exploding,))
    assert ctx.stop is True
    assert "exploding" in report.errors[0]


def test_stages_can_be_composed_for_a_partial_run(pack, monkeypatch):
    """Inserting or omitting a stage must not require editing the others."""
    from ke.pipeline import deduplicate, discover, load_state

    monkeypatch.setattr(
        pipeline_module, "discover_all",
        lambda *a, **k: DiscoveryResult(items=[make_item()]),
    )
    ctx = HarvestContext(pack=pack, clock=CLOCK, report=_report(pack))
    report = run_stages(ctx, (discover, load_state, deduplicate))

    assert report.discovered == 1
    assert report.minted == []           # minting was simply not in the list
    assert not list(pack.knowledge_dir.rglob("*.yaml"))


def _report(pack):
    from ke.report import HarvestReport

    return HarvestReport(pack_name=pack.name)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_classification_lands_on_a_freshly_harvested_pack(pack, monkeypatch):
    # Title must contain "generally available" — the phrase the fixture's tier
    # rule matches. "general availability" is a different string.
    run(pack, [make_item(title="Direct Lake is generally available")], monkeypatch)
    obj = load_existing_objects(pack)[0][0]

    assert obj.tier is Tier.ACT_NOW
    assert obj.category == "data-engineering"
    assert obj.learning_priority is LearningPriority.HIGH
    assert "direct-lake" in obj.tags


def test_a_classified_pack_still_validates(pack, monkeypatch, tmp_path):
    from ke.validate import has_errors, validate_repo

    run(pack, [make_item(title="Direct Lake is generally available")], monkeypatch)
    findings = validate_repo(tmp_path, None)
    assert not has_errors(findings, strict=True), "\n".join(str(f) for f in findings)


def test_indexes_are_rebuilt_after_classification(pack, monkeypatch):
    run(pack, [make_item(title="Direct Lake is generally available")], monkeypatch)
    assert (pack.indexes_dir / "INDEX.md").exists()
