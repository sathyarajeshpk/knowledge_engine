"""The weekly digest.

The digest is the only output of an unattended run that a human actually reads,
which makes it the one place where "the pipeline succeeded" and "the pipeline
did the right thing" have to be told apart in writing. These tests pin the three
rules `ke.digest` documents for itself, because each one exists to prevent a
specific way a scheduled system goes quietly wrong:

1. **Written even on an empty run** — otherwise "nothing was published this
   week" and "the harvest never ran" produce identical evidence.
2. **The backlog is always in it, high up** — a unified review queue that is
   mentioned in a footnote is a queue nobody works.
3. **Problems before successes** — a digest that leads with "12 new items" and
   buries a dead source below the fold has reported the wrong thing first.

Everything here is offline and deterministic. No AI (ADR-0004): the digest is
counting and formatting, which is exactly why it can be asserted on this
precisely.
"""

from __future__ import annotations

from datetime import date

import pytest

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.digest import DigestData, gather, iso_week, render, subject, write
from ke.harvest import harvest_pack
from ke.pack import Pack
from ke.report import HarvestReport

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


def run(pack, items, monkeypatch, **kwargs):
    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=list(items))
    )
    return harvest_pack(pack, clock=CLOCK, **kwargs)


def data(**overrides) -> DigestData:
    defaults = dict(
        pack_name="test-pack",
        week="2026-W31",
        run_id="run-2026-08-02T06:00:00Z",
        report=HarvestReport(pack_name="test-pack"),
        new_objects=[],
        review_total=0,
        review_breakdown={},
        unhealthy_sources=[],
    )
    defaults.update(overrides)
    return DigestData(**defaults)


# ---------------------------------------------------------------------------
# ISO weeks — the digest's identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "day,expected",
    [
        (date(2026, 8, 2), "2026-W31"),
        # 1 January is not always week 1, and the ISO year is not always the
        # calendar year. Naming a file `2027-W01` for a day in 2026 would put
        # two different weeks' digests in the same file a year apart.
        (date(2027, 1, 1), "2026-W53"),
        (date(2026, 1, 1), "2026-W01"),
        (date(2026, 12, 31), "2026-W53"),
    ],
)
def test_the_iso_week_is_the_iso_week(day, expected):
    assert iso_week(day) == expected


def test_the_week_is_zero_padded():
    """`2026-W07`, not `2026-W7`, so digests sort lexicographically."""
    assert iso_week(date(2026, 2, 12)) == "2026-W07"


# ---------------------------------------------------------------------------
# Rule 1: a digest is written even when nothing happened
# ---------------------------------------------------------------------------


def test_an_empty_harvest_still_writes_a_digest(pack, monkeypatch):
    """Absence of news must produce evidence, not absence of a file."""
    run(pack, [], monkeypatch)

    digests = sorted(pack.digests_dir.glob("*.md"))
    assert [p.name for p in digests] == ["2026-W31.md"]


def test_an_empty_digest_says_the_sources_were_read(pack, monkeypatch):
    """"Nothing new" must be distinguishable from "nothing was checked"."""
    run(pack, [], monkeypatch)

    text = (pack.digests_dir / "2026-W31.md").read_text(encoding="utf-8")
    assert "Every source was read successfully" in text


def test_a_second_run_in_the_same_week_overwrites_rather_than_accumulates(
    pack, monkeypatch
):
    """The digest describes the week, not the run.

    Two files for one week would be two answers to the same question, and the
    reader has no way to know which is current.
    """
    run(pack, [make_item()], monkeypatch)
    run(pack, [make_item()], monkeypatch)

    assert [p.name for p in sorted(pack.digests_dir.glob("*.md"))] == ["2026-W31.md"]


def test_the_digest_is_written_before_notifications_are_sent(pack, monkeypatch):
    """Order matters: the durable record must exist before the ephemeral one.

    A notification that fails is an inconvenience. A notification that succeeds
    while the digest it describes was never written leaves a reader with a
    summary and no way to reach the detail.
    """
    from ke.pipeline import STAGES

    names = [stage.__name__ for stage in STAGES]
    assert names.index("write_digest") < names.index("send_notifications")


# ---------------------------------------------------------------------------
# Rule 2: the backlog is always visible
# ---------------------------------------------------------------------------


def test_the_review_backlog_appears_in_the_digest(pack, monkeypatch):
    """A queue nobody sees is a queue nobody works."""
    report = run(pack, [make_item()], monkeypatch)
    text = (pack.digests_dir / "2026-W31.md").read_text(encoding="utf-8")

    from ke.reviewq import counts

    # Unconditional on purpose. A test pack has no classification rules, so the
    # minted object is always unclassifiable and the backlog is always non-empty
    # -- guarding this with `if total:` would let it pass while asserting
    # nothing, which is how a test becomes decoration.
    total = sum(counts(pack).values())
    assert total, "the fixture must produce a backlog for this test to mean anything"
    assert f"{total} item(s) awaiting review" in text
    assert report.digest_path, "the run must record where the digest went"


def test_the_backlog_is_placed_above_the_new_items(pack):
    """Placement is the whole point — a mentioned backlog is not a seen one."""
    text = render(
        data(
            report=HarvestReport("test-pack", minted=["TST-2026-07-001"]),
            new_objects=[("TST-2026-07-001", "Something new", "1")],
            review_total=72,
            review_breakdown={"queued": 26, "unclassified": 45, "revision": 1},
        )
    )
    assert text.index("awaiting review") < text.index("## New this week")


def test_the_backlog_breakdown_names_every_kind(pack):
    text = render(data(review_total=72, review_breakdown={"queued": 26, "unclassified": 46}))
    assert "queued: 26" in text and "unclassified: 46" in text


def test_a_clean_backlog_is_not_reported_as_a_section():
    """Zero pending items should not produce a heading claiming attention."""
    assert "awaiting review" not in render(data())


# ---------------------------------------------------------------------------
# Rule 3: problems first
# ---------------------------------------------------------------------------


def test_a_dead_source_is_reported_above_the_summary():
    """A source that failed is missing knowledge, not absent knowledge."""
    text = render(data(unhealthy_sources=["fabric-blog: HTTP 503"]))
    assert text.index("could not be read") < text.index("## Summary")


def test_errors_are_reported_above_the_summary():
    text = render(data(report=HarvestReport("test-pack", errors=["the registry is corrupt"])))
    assert text.index("## ⚠️ Errors") < text.index("## Summary")
    assert "the registry is corrupt" in text


def test_a_failed_run_does_not_claim_a_quiet_week():
    """The "nothing new" reassurance must not appear when a source died.

    This is the specific sentence that would turn a broken harvest into a
    reassuring one, which is worse than no digest at all.
    """
    text = render(data(unhealthy_sources=["fabric-blog: HTTP 503"]))
    assert "Every source was read successfully" not in text


def test_a_warning_is_reported_above_the_summary_but_below_the_errors():
    """Warnings rank between "this broke" and "here are the numbers".

    A run that succeeded while producing something the reader would not assume
    -- a pack with no classification rules, say -- has to be visible without
    being dressed up as a failure.
    """
    text = render(
        data(
            report=HarvestReport(
                "test-pack",
                errors=["the registry is corrupt"],
                warnings=["new-pack: no classification rules are defined in pack.yml"],
            )
        )
    )
    assert text.index("## \u26a0\ufe0f Errors") < text.index("## Worth knowing")
    assert text.index("## Worth knowing") < text.index("## Summary")


def test_a_warning_does_not_suppress_the_quiet_week_reassurance():
    """A warning is not a failed source. The digest must not conflate them."""
    text = render(data(report=HarvestReport("test-pack", warnings=["heads up"])))
    assert "Every source was read successfully" in text


def test_a_warning_alone_does_not_make_the_subject_say_needs_attention():
    """Subject lines are triage. Over-alerting is how they stop being read."""
    assert subject(
        data(report=HarvestReport("test-pack", warnings=["heads up"]))
    ).endswith("no new knowledge")


def test_an_enormous_error_list_is_truncated_rather_than_dumped():
    """A digest is read by a person; 4,000 error lines is not a report."""
    text = render(data(report=HarvestReport("test-pack", errors=[f"error {n}" for n in range(60)])))
    assert "… and 40 more" in text
    assert text.count("- error") == 20


# ---------------------------------------------------------------------------
# The subject line — often all that gets read
# ---------------------------------------------------------------------------


def test_the_subject_leads_with_the_problem():
    assert subject(data(unhealthy_sources=["fabric-blog"])).endswith("needs attention")
    assert subject(
        data(report=HarvestReport("test-pack", errors=["boom"]), review_total=0)
    ).endswith("needs attention")


def test_the_subject_reports_a_count_when_all_is_well():
    assert subject(data(report=HarvestReport("test-pack", minted=["TST-2026-07-001"]))).endswith(
        "1 new"
    )


def test_the_subject_says_so_when_there_is_nothing():
    assert subject(data()).endswith("no new knowledge")


def test_a_problem_outranks_new_knowledge_in_the_subject():
    """Both true at once is the case a naive implementation gets wrong."""
    assert subject(
        data(
            report=HarvestReport("test-pack", minted=["TST-2026-07-001"]),
            unhealthy_sources=["fabric-blog"],
        )
    ).endswith("needs attention")


# ---------------------------------------------------------------------------
# The numbers must be the run's numbers
# ---------------------------------------------------------------------------


def test_the_digest_counts_match_the_report(pack, monkeypatch):
    """Validating produced knowledge, not just successful execution.

    A digest that reports its own plausible-looking numbers rather than the
    run's is the most dangerous possible output of this module: it is confident,
    readable and wrong.
    """
    items = [make_item(title=f"Feature number {n}") for n in range(4)]
    report = run(pack, items, monkeypatch)

    text = (pack.digests_dir / "2026-W31.md").read_text(encoding="utf-8")
    assert f"| Discovered | {report.discovered} |" in text
    assert f"| **New knowledge objects** | **{len(report.minted)}** |" in text
    assert f"| Updated | {len(report.updated)} |" in text
    assert f"| Unchanged | {report.unchanged} |" in text


def test_every_minted_object_is_listed_by_id(pack, monkeypatch):
    report = run(pack, [make_item(title=f"Feature number {n}") for n in range(3)], monkeypatch)
    text = (pack.digests_dir / "2026-W31.md").read_text(encoding="utf-8")

    assert report.minted, "the fixture must mint something for this to mean anything"
    for feature_id in report.minted:
        assert feature_id in text


def test_gather_reads_the_pack_as_it_now_stands(pack, monkeypatch):
    """`gather` runs after persistence, so it must see what was just written."""
    report = run(pack, [make_item()], monkeypatch)
    collected = gather(pack, report, run_id="run-x", today=date(2026, 8, 2))

    assert len(collected.new_objects) == len(report.minted)
    assert collected.week == "2026-W31"


def test_new_objects_are_ordered_deterministically(pack, monkeypatch):
    """ADR-0022: byte-identical output for identical input.

    Iteration order over a directory is not a specification, and a digest that
    reorders its own rows between runs produces a Git diff every week that says
    nothing happened.
    """
    items = [make_item(title=f"Feature number {n}") for n in range(5)]
    report = run(pack, items, monkeypatch)

    first = render(gather(pack, report, "run-x", date(2026, 8, 2)))
    second = render(gather(pack, report, "run-x", date(2026, 8, 2)))
    assert first == second


def test_writing_a_digest_returns_the_path_it_wrote(pack):
    path = write(pack, data())
    assert path == pack.digests_dir / "2026-W31.md"
    assert path.read_text(encoding="utf-8").startswith("# test-pack — 2026-W31")


def test_the_digest_records_the_run_that_produced_it(pack):
    """Traceable back to `run-log.md` — a digest with no run is unverifiable."""
    assert "run-2026-08-02T06:00:00Z" in render(data())
