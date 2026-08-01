"""Search and retrieval.

The properties worth pinning here are not "does it find things" — a filter that
never matched anything would be noticed in a minute. They are the ways a search
can be **confidently wrong**:

* a filter that silently does nothing, so the result set is larger than the
  caller believes and every extra row looks legitimate;
* a filter that quietly narrows, so something real is missing and nothing says
  so;
* an ordering that shifts between runs, so pasting results into notes produces a
  diff that means nothing.

Each of those returns a plausible answer. That is what makes them worth tests.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ke.models import (
    ArtifactType,
    Difficulty,
    GenerationEntry,
    GenerationStatus,
    LearningPriority,
    LearningStatus,
    ObjectStatus,
    Revision,
    Tier,
)
from ke.retrieve import (
    Query,
    matches,
    render_object,
    render_results,
    resolve,
    search,
    sort_key,
)

from tests.test_models import make_object


def an_object(**overrides):
    """A stored object with every filterable field set to something known."""
    defaults = dict(
        title="Direct Lake general availability",
        category="data-engineering",
        tags=("direct-lake", "semantic-model"),
        tier=Tier.LEARN_SOON,
        learning_priority=LearningPriority.HIGH,
        difficulty=Difficulty.INTERMEDIATE,
        learning_status=LearningStatus.NOT_STARTED,
        status=ObjectStatus.ACTIVE,
        source_name="fabric-blog",
        published_date=date(2026, 5, 1),
        needs_review=False,
    )
    defaults.update(overrides)
    return make_object(**defaults)


# ---------------------------------------------------------------------------
# Every filter must actually filter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,hit,miss",
    [
        ("tier", Tier.LEARN_SOON, Tier.ACT_NOW),
        ("learning_priority", LearningPriority.HIGH, LearningPriority.LOW),
        ("difficulty", Difficulty.INTERMEDIATE, Difficulty.BEGINNER),
        ("learning_status", LearningStatus.NOT_STARTED, LearningStatus.LEARNED),
        ("status", ObjectStatus.ACTIVE, ObjectStatus.DEPRECATED),
        ("category", "data-engineering", "governance"),
        ("tag", "direct-lake", "spark"),
        ("source", "fabric-blog", "fabric-whats-new"),
        ("text", "direct lake", "cosmos"),
        ("needs_review", False, True),
    ],
)
def test_each_filter_accepts_a_match_and_rejects_a_non_match(field, hit, miss):
    """Both directions, per filter.

    Only asserting the match would pass for a predicate that returns True
    unconditionally — which is exactly what a filter reading the wrong attribute
    tends to do.
    """
    obj = an_object()

    assert matches(obj, Query(**{field: hit}))
    assert not matches(obj, Query(**{field: miss}))


def test_every_query_field_has_a_predicate():
    """A field with no predicate would raise on use, or worse, be skipped.

    Asserted structurally so that adding a field to `Query` without adding its
    predicate fails here rather than at the first invocation by a user.
    """
    from ke.retrieve import _PREDICATES

    assert set(Query.__dataclass_fields__) == set(_PREDICATES)


def test_an_empty_query_matches_everything():
    assert matches(an_object(), Query())
    assert Query().is_empty


def test_filters_compose_by_and():
    obj = an_object()

    assert matches(obj, Query(tier=Tier.LEARN_SOON, category="data-engineering"))
    # One wrong term is enough to exclude — an OR would still match here.
    assert not matches(obj, Query(tier=Tier.LEARN_SOON, category="governance"))


# ---------------------------------------------------------------------------
# Text folding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    ["direct lake", "Direct-Lake", "DIRECT LAKE", "direct  lake", "  direct lake  "],
)
def test_text_search_ignores_case_and_punctuation(needle):
    """`direct lake` must find the tag `direct-lake`.

    Without folding, whether a search works depends on whether the person typing
    remembered the hyphen — and it fails by returning fewer results, silently,
    which is the failure mode that does not look like one.
    """
    assert matches(an_object(), Query(text=needle))


def test_text_search_covers_tags_and_category_not_only_the_title():
    obj = an_object(title="Something unrelated")

    assert matches(obj, Query(text="semantic model"))   # a tag
    assert matches(obj, Query(text="data engineering"))  # the category


def test_text_search_does_not_match_across_unrelated_words():
    """Folding must not make everything match everything."""
    assert not matches(an_object(), Query(text="cosmos db"))


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_date_filters_are_inclusive_at_both_ends():
    """A range that excludes its own endpoints loses items nobody looks for."""
    obj = an_object(published_date=date(2026, 5, 1))

    assert matches(obj, Query(since=date(2026, 5, 1)))
    assert matches(obj, Query(until=date(2026, 5, 1)))


def test_an_object_with_no_publication_date_is_filtered_by_discovery_date():
    """The same precedence the Feature ID uses, so the two cannot disagree.

    An object minted into `2026-08` because its publication date was unknown must
    not then be invisible to `--since 2026-08-01`.
    """
    obj = an_object(published_date=None, discovered_date=date(2026, 8, 2))

    assert matches(obj, Query(since=date(2026, 8, 1)))
    assert not matches(obj, Query(until=date(2026, 7, 31)))


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def stale_object():
    base = an_object()
    return replace(
        base,
        revisions=(
            *base.revisions,
            Revision(revision=2, date=date(2026, 6, 1), changed_fields=("title",),
                     summary="Source retitled"),
        ),
        generation={
            ArtifactType.TUTORIAL: GenerationEntry(
                status=GenerationStatus.GENERATED,
                path="artifacts/tutorial.md",
                generated_from_revision=1,
            )
        },
    )


def test_the_stale_filter_finds_objects_whose_knowledge_moved_on():
    assert matches(stale_object(), Query(stale=True))


def test_an_object_with_no_artifacts_is_not_stale():
    """Regression for the bound-method bug.

    `stale_artifacts` was a method, so `bool(obj.stale_artifacts)` was true for
    every object in the pack. `--stale` matched all 222 of them and looked
    entirely reasonable doing it.
    """
    assert not matches(an_object(), Query(stale=True))
    assert matches(an_object(), Query(stale=False))


def test_an_artifact_generated_from_the_current_revision_is_not_stale():
    base = an_object()
    current = replace(
        base,
        generation={
            ArtifactType.TUTORIAL: GenerationEntry(
                status=GenerationStatus.GENERATED,
                path="artifacts/tutorial.md",
                generated_from_revision=base.current_revision,
            )
        },
    )
    assert not matches(current, Query(stale=True))


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


def test_results_are_ordered_by_tier_then_recency():
    urgent_old = an_object(tier=Tier.ACT_NOW, published_date=date(2026, 1, 1))
    minor_new = an_object(tier=Tier.AWARENESS, published_date=date(2026, 8, 1))

    assert sort_key(urgent_old) < sort_key(minor_new)


def test_two_objects_in_the_same_tier_and_month_order_by_id():
    """The final tiebreak. Without it, ordering depends on directory iteration.

    Search output gets pasted into notes and diffed; a result set that reorders
    itself between identical runs produces a diff that means nothing (ADR-0022).
    """
    from ke.models import FeatureId

    first = an_object(id=FeatureId.parse("MSF-2026-05-001"))
    second = an_object(id=FeatureId.parse("MSF-2026-05-002"))

    assert sort_key(first) < sort_key(second)


# ---------------------------------------------------------------------------
# Against a real pack on disk
# ---------------------------------------------------------------------------


@pytest.fixture
def pack(tmp_path):
    import ke.pipeline as pipeline_module
    from ke.acquisition import DiscoveryResult
    from ke.harvest import harvest_pack
    from ke.pack import Pack

    from tests.test_pipeline import CLOCK, make_item

    root = tmp_path / "domain-packs" / "test-pack"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')
    loaded = Pack.load(root)

    items = [make_item(title=f"Feature number {n}") for n in range(5)]
    original = pipeline_module.discover_all
    pipeline_module.discover_all = lambda *a, **k: DiscoveryResult(items=items)
    try:
        harvest_pack(loaded, clock=CLOCK)
    finally:
        pipeline_module.discover_all = original
    return loaded


def test_search_reads_the_pack_from_disk(pack):
    found = search(pack, Query())

    assert len(found) == 5
    assert all(str(obj.id).startswith("TST-") for obj in found)


def test_search_is_deterministic_across_repeated_calls(pack):
    assert [o.id for o in search(pack, Query())] == [
        o.id for o in search(pack, Query())
    ]


def test_the_limit_is_applied_after_ordering(pack):
    """Truncating before sorting would return an arbitrary subset, sorted.

    The result would look perfectly ordered and be the wrong five objects.
    """
    everything = search(pack, Query())
    limited = search(pack, Query(), limit=2)

    assert limited == everything[:2]


def test_search_finds_nothing_rather_than_failing_on_no_match(pack):
    assert search(pack, Query(text="nothing here matches this")) == []


def test_resolve_prefers_the_pack_whose_prefix_matches(pack):
    found = search(pack, Query())[0]
    resolved_pack, obj, _ = resolve([pack], str(found.id))

    assert resolved_pack is pack
    assert obj.id == found.id


def test_resolve_raises_for_an_unknown_id(pack):
    with pytest.raises(KeyError):
        resolve([pack], "TST-1999-01-999")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_empty_results_say_so_rather_than_printing_nothing():
    """A blank response is indistinguishable from a crash."""
    assert "No objects matched" in render_results([])


def test_the_result_footer_reports_how_many_were_withheld():
    objects = [an_object()]

    assert "1 object(s) of 40" in render_results(objects, total=40)
    assert "of" not in render_results(objects, total=1)


def test_a_stale_marker_is_explained_when_it_appears():
    """A bare `*` in terminal output is a puzzle, not information."""
    rendered = render_results([stale_object()])

    assert rendered.lstrip().startswith("*")
    assert "stale artifact" in rendered


def test_no_stale_marker_legend_when_nothing_is_stale():
    assert "stale artifact" not in render_results([an_object()])


def test_the_object_view_shows_the_tier_name_not_the_number_twice():
    """`Tier` is an IntEnum, so `str(tier)` is the digit.

    Rendering "2 (2)" told the reader nothing, twice.
    """
    rendered = render_object(an_object(tier=Tier.LEARN_SOON), "/some/path")

    assert "2 (learn-soon)" in rendered


def test_the_object_view_says_when_nothing_has_been_generated():
    """An empty artifact section reads as a bug; an explicit line reads as fact."""
    assert "none generated" in render_object(an_object(), "/p")


def test_the_object_view_marks_stale_artifacts():
    rendered = render_object(stale_object(), "/p")

    assert "tutorial" in rendered and "(stale)" in rendered


def test_the_object_view_shows_locked_fields(pack):
    """`overrides` is the user's most consequential setting and easy to forget."""
    rendered = render_object(an_object(overrides=("difficulty",)), "/p")

    assert "locked fields" in rendered and "difficulty" in rendered
