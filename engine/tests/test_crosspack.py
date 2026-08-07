"""Cross-pack behaviour: duplicates, isolation, references, and order.

M8 adds a second pack, which makes a whole class of question askable for the
first time. The properties pinned here are the ones whose failure would be
**permanent or silent**:

* **Order independence.** Harvesting Azure then Fabric must produce exactly the
  same result as Fabric then Azure — same Feature IDs, same duplicate report.
  A system where run order changes permanent identifiers has no defensible
  answer to "why is this MSF-2026-08-004 and not -005?".
* **Symmetry.** A duplicate is a fact about two packs. Surfacing it from only
  one side would make the review queue depend on which pack you happened to
  open.
* **Non-interference.** Detecting a duplicate must not modify either object.
  The engine reports; a human decides.
* **Isolation.** One pack failing must not damage or block another.

The live repository has **zero** cross-pack duplicates — Azure and Fabric draw
on genuinely independent sources, which is the point of choosing Azure. That
makes real data useless as evidence here, so every duplicate in this file is
constructed deliberately.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.crosspack import (
    CROSS_PACK_STATE,
    Resolutions,
    dangling_references,
    find_duplicates,
    outstanding,
    render_evidence,
)
from ke.harvest import harvest_pack, load_objects_with_dirs
from ke.pack import Pack
from ke.store import load_object, update_object

from tests.test_pipeline import CLOCK, make_item

TODAY = date(2026, 8, 2)


def build_pack(repo: Path, name: str, prefix: str) -> Pack:
    root = repo / "domain-packs" / name
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        f"name: {name}\nid_prefix: {prefix}\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text(f'{{"prefix": "{prefix}"}}\n')
    return Pack.load(root)


def harvest(pack: Pack, items, monkeypatch):
    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=list(items))
    )
    return harvest_pack(pack, clock=CLOCK)


@pytest.fixture
def repo(tmp_path) -> Path:
    (tmp_path / "domain-packs").mkdir()
    return tmp_path


@pytest.fixture
def two_packs(repo):
    return build_pack(repo, "alpha", "ALF"), build_pack(repo, "beta", "BET")


#: The same real-world announcement, reachable at one canonical URL, reported by
#: two different sources. This is what a genuine cross-pack duplicate looks like.
SHARED_URL = "https://learn.invalid/shared-announcement"


def shared_item(source: str):
    return make_item(title="A feature both packs found", url=SHARED_URL,
                     source_name=source)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_two_packs_sharing_a_canonical_url_are_reported(two_packs, monkeypatch):
    alpha, beta = two_packs
    harvest(alpha, [shared_item("alpha-source")], monkeypatch)
    harvest(beta, [shared_item("beta-source")], monkeypatch)

    pairs = find_duplicates([alpha, beta])

    assert len(pairs) == 1
    assert pairs[0].basis == "canonical-url"
    assert set(pairs[0].packs) == {"alpha", "beta"}


def test_packs_with_different_knowledge_report_nothing(two_packs, monkeypatch):
    """The normal case, and the one the live repository is in."""
    alpha, beta = two_packs
    harvest(alpha, [make_item(title="Something about Alpha")], monkeypatch)
    harvest(beta, [make_item(title="Something else entirely")], monkeypatch)

    assert find_duplicates([alpha, beta]) == []


def test_a_duplicate_within_one_pack_is_not_a_cross_pack_duplicate(repo, monkeypatch):
    """Within-pack dedupe already handles that, and did so before minting.

    Reporting it here too would double-count a problem that cannot exist.
    """
    alpha = build_pack(repo, "alpha", "ALF")
    harvest(alpha, [shared_item("a"), shared_item("a")], monkeypatch)

    assert find_duplicates([alpha]) == []


def test_both_objects_are_minted_and_kept(two_packs, monkeypatch):
    """Detection does not suppress the second mint.

    Suppressing it would make the surviving object depend on run order, and
    would silently deny one pack knowledge its own sources reported.
    """
    alpha, beta = two_packs
    harvest(alpha, [shared_item("alpha-source")], monkeypatch)
    harvest(beta, [shared_item("beta-source")], monkeypatch)

    assert len(load_objects_with_dirs(alpha)) == 1
    assert len(load_objects_with_dirs(beta)) == 1


# ---------------------------------------------------------------------------
# Order independence — the property that makes the rest trustworthy
# ---------------------------------------------------------------------------


def build_and_harvest(repo: Path, order: list[str], monkeypatch) -> dict:
    """Harvest two packs in the given order; return what ended up on disk."""
    packs = {
        "alpha": build_pack(repo, "alpha", "ALF"),
        "beta": build_pack(repo, "beta", "BET"),
    }
    for name in order:
        harvest(packs[name], [shared_item(f"{name}-source"),
                              make_item(title=f"Unique to {name}")], monkeypatch)
    return {
        "ids": {
            name: sorted(str(o.id) for o, _ in load_objects_with_dirs(pack))
            for name, pack in packs.items()
        },
        "duplicates": [
            (p.basis, p.short_key) for p in find_duplicates(list(packs.values()))
        ],
    }


def test_harvest_order_does_not_change_feature_ids(tmp_path, monkeypatch):
    """The one that would be unrecoverable if it broke.

    Feature IDs are permanent. If pack order decided them, the same repository
    harvested on two machines would disagree about identity forever.

    This holds **by construction**: each pack mints from its own registry and its
    own per-month counter, so there is no shared state for order to perturb. The
    test is a regression guard against that changing — a future shared counter,
    or a cross-pack check that suppressed a mint, would both fail here.
    """
    forwards = tmp_path / "forwards"
    backwards = tmp_path / "backwards"
    for root in (forwards, backwards):
        (root / "domain-packs").mkdir(parents=True)

    first = build_and_harvest(forwards, ["alpha", "beta"], monkeypatch)
    second = build_and_harvest(backwards, ["beta", "alpha"], monkeypatch)

    assert first["ids"] == second["ids"]


def test_harvest_order_does_not_change_the_duplicate_report(tmp_path, monkeypatch):
    forwards = tmp_path / "f"
    backwards = tmp_path / "b"
    for root in (forwards, backwards):
        (root / "domain-packs").mkdir(parents=True)

    first = build_and_harvest(forwards, ["alpha", "beta"], monkeypatch)
    second = build_and_harvest(backwards, ["beta", "alpha"], monkeypatch)

    assert first["duplicates"] == second["duplicates"]
    assert len(first["duplicates"]) == 1


def test_the_pair_key_is_canonical_whichever_pack_is_listed_first(two_packs, monkeypatch):
    """`find_duplicates([a, b])` and `find_duplicates([b, a])` are the same call.

    Order independence here rests on **two** mechanisms — packs are sorted before
    reading, and each pair's sides are sorted by Feature ID — and either alone is
    sufficient. Mutation testing showed that removing one leaves this test
    passing, and removing both fails it. So this pins the *property*, not either
    implementation of it, which is the honest description.
    """
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    forwards = find_duplicates([alpha, beta])
    backwards = find_duplicates([beta, alpha])

    assert forwards == backwards


def test_detection_is_byte_identical_across_repeated_calls(two_packs, monkeypatch):
    """ADR-0022. A report that reorders itself makes its own diff meaningless."""
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a"), make_item(title="One")], monkeypatch)
    harvest(beta, [shared_item("b"), make_item(title="Two")], monkeypatch)

    first = [render_evidence(p) for p in find_duplicates([alpha, beta])]
    second = [render_evidence(p) for p in find_duplicates([alpha, beta])]

    assert first == second


# ---------------------------------------------------------------------------
# Symmetry
# ---------------------------------------------------------------------------


def test_the_duplicate_is_surfaced_from_both_packs(two_packs, monkeypatch):
    """A fact about two packs must be visible from either one."""
    from ke.reviewq import TaskKind, collect

    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    from_alpha = [t for t in collect(alpha) if t.kind is TaskKind.CROSS_PACK]
    from_beta = [t for t in collect(beta) if t.kind is TaskKind.CROSS_PACK]

    assert len(from_alpha) == 1 and len(from_beta) == 1
    assert from_alpha[0].key == from_beta[0].key  # the same pair, one key


def test_each_side_names_its_counterpart(two_packs, monkeypatch):
    from ke.reviewq import TaskKind, collect

    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    task = [t for t in collect(alpha) if t.kind is TaskKind.CROSS_PACK][0]

    assert "beta" in task.reason
    assert task.detail["other"].startswith("beta:")


# ---------------------------------------------------------------------------
# The evidence must be sufficient to decide on
# ---------------------------------------------------------------------------


def test_the_review_item_carries_enough_to_decide_without_opening_a_file(
    two_packs, monkeypatch
):
    """A review item that says "these might match" makes the reader redo the work."""
    alpha, beta = two_packs
    harvest(alpha, [shared_item("alpha-source")], monkeypatch)
    harvest(beta, [shared_item("beta-source")], monkeypatch)

    evidence = render_evidence(find_duplicates([alpha, beta])[0])

    assert "canonical-url" in evidence          # what matched
    assert SHARED_URL in evidence               # the URLs
    assert "alpha-source" in evidence           # provenance
    assert "beta-source" in evidence
    assert "ALF-" in evidence and "BET-" in evidence   # both Feature IDs
    assert "A feature both packs found" in evidence    # titles
    assert "Both objects are kept" in evidence         # what the engine did not do
    assert "ke supersede" in evidence                  # the way to act on it


# ---------------------------------------------------------------------------
# Resolution: recorded once, never resurfaced, nothing modified
# ---------------------------------------------------------------------------


def test_acknowledging_stops_it_being_surfaced(repo, two_packs, monkeypatch):
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)
    packs = [alpha, beta]

    assert len(outstanding(packs, repo)) == 1

    resolutions = Resolutions.load(repo)
    resolutions.acknowledge(find_duplicates(packs)[0], today=TODAY)
    resolutions.save(repo)

    assert outstanding(packs, repo) == []
    assert find_duplicates(packs)                # still a duplicate, just handled


def test_acknowledging_from_one_side_clears_both(repo, two_packs, monkeypatch):
    """Otherwise the same decision has to be made twice, which nobody does."""
    from ke.reviewq import TaskKind, apply_action, collect
    from ke.reviewq import Action

    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    task = [t for t in collect(alpha) if t.kind is TaskKind.CROSS_PACK][0]
    apply_action(alpha, task, Action.RESOLVE)

    assert [t for t in collect(alpha) if t.kind is TaskKind.CROSS_PACK] == []
    assert [t for t in collect(beta) if t.kind is TaskKind.CROSS_PACK] == []


def test_acknowledging_modifies_neither_object(repo, two_packs, monkeypatch):
    """The engine reports; it does not decide. Asserted on the bytes."""
    from ke.reviewq import Action, TaskKind, apply_action, collect

    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    before = {
        path: (Path(path) / "metadata.yaml").read_bytes()
        for pack in (alpha, beta)
        for _, path in load_objects_with_dirs(pack)
    }

    task = [t for t in collect(alpha) if t.kind is TaskKind.CROSS_PACK][0]
    apply_action(alpha, task, Action.RESOLVE)

    after = {
        path: (Path(path) / "metadata.yaml").read_bytes()
        for pack in (alpha, beta)
        for _, path in load_objects_with_dirs(pack)
    }
    assert before == after


def test_the_resolution_records_the_evidence_not_just_the_key(repo, two_packs, monkeypatch):
    """So the record still means something after one object is retitled."""
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    resolutions = Resolutions.load(repo)
    resolutions.acknowledge(find_duplicates([alpha, beta])[0], today=TODAY, note="same feature")
    path = resolutions.save(repo)

    stored = json.loads(path.read_text(encoding="utf-8"))["acknowledged"]
    entry = next(iter(stored.values()))
    assert entry["basis"] == "canonical-url"
    assert entry["acknowledged_on"] == "2026-08-02"
    assert entry["note"] == "same feature"
    assert {o["pack"] for o in entry["objects"]} == {"alpha", "beta"}


def test_the_resolution_store_lives_outside_every_pack(repo, two_packs, monkeypatch):
    """A fact about two packs belongs to neither.

    Storing it in both is how two copies come to disagree.
    """
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    resolutions = Resolutions.load(repo)
    resolutions.acknowledge(find_duplicates([alpha, beta])[0], today=TODAY)
    path = resolutions.save(repo)

    assert path == repo / CROSS_PACK_STATE
    assert "domain-packs" not in str(path)


def test_a_corrupt_resolution_store_degrades_rather_than_stopping(repo, two_packs, monkeypatch):
    """Worst case is re-showing a decision somebody made. Annoying, not harmful.

    Contrast the ID registry, where guessing is unrecoverable and the run stops.
    """
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)
    (repo / CROSS_PACK_STATE).parent.mkdir(parents=True, exist_ok=True)
    (repo / CROSS_PACK_STATE).write_text("{ not json", encoding="utf-8")

    assert len(outstanding([alpha, beta], repo)) == 1


# ---------------------------------------------------------------------------
# Pack isolation
# ---------------------------------------------------------------------------


def test_each_pack_mints_from_its_own_registry(two_packs, monkeypatch):
    alpha, beta = two_packs
    harvest(alpha, [make_item(title="One")], monkeypatch)
    harvest(beta, [make_item(title="One")], monkeypatch)

    alpha_ids = [str(o.id) for o, _ in load_objects_with_dirs(alpha)]
    beta_ids = [str(o.id) for o, _ in load_objects_with_dirs(beta)]

    assert all(i.startswith("ALF-") for i in alpha_ids)
    assert all(i.startswith("BET-") for i in beta_ids)
    # Same sequence number in both: the counters are genuinely independent.
    assert alpha_ids[0].split("-")[-1] == beta_ids[0].split("-")[-1]


def test_one_pack_failing_does_not_stop_another(repo, monkeypatch):
    """Failure isolation across packs, at the level the CLI loops."""
    import ke.store as store_module

    alpha = build_pack(repo, "alpha", "ALF")
    beta = build_pack(repo, "beta", "BET")

    real = store_module._atomic_write
    monkeypatch.setattr(
        store_module, "_atomic_write",
        lambda p, t: (_ for _ in ()).throw(OSError("alpha's disk is full"))
        if "ALF-" in str(p) else real(p, t),
    )

    alpha_report = harvest(alpha, [make_item(title="One")], monkeypatch)
    beta_report = harvest(beta, [make_item(title="Two")], monkeypatch)

    assert alpha_report.errors and alpha_report.minted == []
    assert beta_report.errors == [] and len(beta_report.minted) == 1


def test_a_broken_pack_does_not_hide_another_packs_duplicates(repo, two_packs, monkeypatch):
    """One damaged object must not suppress the whole cross-pack report."""
    alpha, beta = two_packs
    harvest(alpha, [shared_item("a"), make_item(title="Extra")], monkeypatch)
    harvest(beta, [shared_item("b")], monkeypatch)

    damaged = sorted(alpha.knowledge_dir.rglob("metadata.yaml"))
    # Damage one that is NOT part of the pair.
    for path in damaged:
        if "extra" in path.parent.name:
            path.write_text("!!! not yaml", encoding="utf-8")

    assert len(find_duplicates([alpha, beta])) == 1


# ---------------------------------------------------------------------------
# Cross-pack references
# ---------------------------------------------------------------------------


def test_a_reference_into_another_pack_resolves(two_packs, monkeypatch):
    """M8 permits cross-pack relationships, so they must not read as dangling."""
    from dataclasses import replace

    alpha, beta = two_packs
    harvest(alpha, [make_item(title="Alpha thing")], monkeypatch)
    harvest(beta, [make_item(title="Beta thing")], monkeypatch)

    beta_id = str(load_objects_with_dirs(beta)[0][0].id)
    obj, directory = load_objects_with_dirs(alpha)[0]
    linked = replace(obj, prerequisites=(beta_id,))
    update_object(directory, linked, obj.title, max_summary_words=120)

    assert dangling_references([alpha, beta]) == []


def test_a_reference_to_nothing_is_still_caught(two_packs, monkeypatch):
    """Permitting cross-pack links must not turn off the check."""
    from dataclasses import replace

    alpha, beta = two_packs
    harvest(alpha, [make_item(title="Alpha thing")], monkeypatch)
    harvest(beta, [make_item(title="Beta thing")], monkeypatch)

    obj, directory = load_objects_with_dirs(alpha)[0]
    linked = replace(obj, prerequisites=("BET-1999-01-999",))
    update_object(directory, linked, obj.title, max_summary_words=120)

    problems = dangling_references([alpha, beta])
    assert problems == [(str(obj.id), "prerequisites", "BET-1999-01-999")]


# ---------------------------------------------------------------------------
# Cost of the report
# ---------------------------------------------------------------------------


def test_render_report_scans_the_other_packs_once(two_packs, monkeypatch) -> None:
    """`render_report` must not run the cross-pack scan twice.

    It needs both the task list and a tally of it, and the first version got the
    tally by calling `collect()` a second time -- running every provider again,
    including the one that reads every object in every pack. Measured on a
    ten-pack repository, that duplicate scan was half of a 147-second index
    rebuild, spent recomputing an answer it already had.

    Counting calls rather than timing them: a timing assertion on a shared
    runner is a flake, and the property here is exactly "how many times", not
    "how fast".
    """
    from ke import crosspack
    from ke.reviewq import render_report

    pack, _ = two_packs

    calls = []
    real = crosspack.find_duplicates
    monkeypatch.setattr(
        crosspack, "find_duplicates", lambda packs: (calls.append(1), real(packs))[1]
    )

    render_report(pack)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# The scan happens once per run, not once per pack (M9, TD-15)
# ---------------------------------------------------------------------------


def _count_pack_reads(fn):
    """Run `fn`, counting full-pack reads through `ke.harvest`."""
    import ke.harvest as harvest_module

    real = harvest_module.load_objects_with_dirs
    calls = []

    def counted(pack):
        calls.append(pack.name)
        return real(pack)

    harvest_module.load_objects_with_dirs = counted
    try:
        fn()
    finally:
        harvest_module.load_objects_with_dirs = real
    return calls


def test_a_multi_pack_harvest_scans_each_pack_a_bounded_number_of_times(
    two_packs, monkeypatch
) -> None:
    """The cross-pack scan must not be repeated per pack.

    This is a **performance property expressed as a test**, and it exists
    because reverting the hoist breaks no other test in the suite — correctness
    tests cannot see the difference between scanning once and scanning N times,
    which is precisely how the quadratic got in and survived eight milestones.

    The bound is `2 * packs` reads per pack, matching the benchmark's linear
    budget: enough headroom for the harvest-side read plus one scan read each,
    and not enough for anything quadratic. At 2 packs the old design did 4
    reads inside index rebuild alone.
    """
    from ke.harvest import harvest_all

    packs = list(two_packs)
    calls = _count_pack_reads(lambda: harvest_all(packs, clock=CLOCK))

    budget = 2 * len(packs) * len(packs)
    assert len(calls) <= budget, (
        f"{len(calls)} full-pack reads for {len(packs)} packs; "
        f"the per-pack scan is back"
    )


def test_the_cross_pack_scan_sees_every_pack_after_it_has_harvested(
    two_packs, monkeypatch
) -> None:
    """TD-16: no pack may be published against a stale view of the others.

    In the old design each pack published inside its own harvest, so pack 1's
    cross-pack list was computed before pack 2 had harvested — every pack but
    the last reported a duplicate one run late. Splitting harvest from publish
    means the scan runs after all harvesting and every pack sees the same
    finished state.
    """
    import ke.harvest as harvest_module
    from ke.harvest import harvest_all
    from ke.pipeline import HARVEST_STAGES

    packs = list(two_packs)
    order: list[str] = []

    real_run = None
    import ke.pipeline as pipeline_module

    real_run = pipeline_module.run_stages

    def traced(ctx, stages=HARVEST_STAGES):
        order.append(("harvest" if stages is HARVEST_STAGES else "publish", ctx.pack.name))
        return real_run(ctx, stages)

    real_scan = harvest_module._scan_cross_pack

    def traced_scan(packs_, **kw):
        order.append(("scan", None))
        return real_scan(packs_, **kw)

    monkeypatch.setattr("ke.pipeline.run_stages", traced)
    monkeypatch.setattr("ke.harvest._scan_cross_pack", traced_scan)

    harvest_all(packs, clock=CLOCK)

    phases = [p for p, _ in order]
    scan_at = phases.index("scan")
    assert "publish" not in phases[:scan_at], "a pack published before the scan ran"
    assert "harvest" not in phases[scan_at:], "a pack harvested after the scan ran"
