"""Tests for M1: clock, identity, normalisation, adapters and orchestration.

Every test here runs offline. Adapters receive a `FakeFetcher` holding a recorded
document, which is the whole point of injecting the fetcher: an adapter that
reached for the network itself could only be tested against the live internet,
and would be untestable the day that source went down.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from ke.clock import FrozenClock, SystemClock
from ke.discover import ReviewItem, discover_all, health_summary
from ke.identity import IdentityBasis, compute_identity, normalise_title
from ke.models import (
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    HealthState,
    SourceAuthority,
    SourceHealth,
    SourceRole,
    SourceStatus,
)
from ke.normalize import (
    canonical_url,
    content_hash,
    html_to_text,
    parse_date,
    parse_date_cell,
    slugify,
    truncate_summary,
)
from ke.sources.base import (
    FetchResult,
    SourceDefinition,
    SourceError,
    sort_items,
)
from ke.sources.html_table import HtmlTableSource

CLOCK = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))

#: Modelled on the real page structure measured during source validation:
#: h2 sections are feature areas, updates are table rows, dates are in cells as
#: "Month YYYY".
WHATS_NEW_HTML = """
<html><body>
<h2>In this article</h2>
<ul><li><a href="#ga">Generally available features</a></li></ul>

<h2 id="ga">Generally available features</h2>
<table>
  <tr><th>Month</th><th>Feature</th><th>Description</th></tr>
  <tr>
    <td>July 2026</td>
    <td><a href="/en-us/fabric/direct-lake-ga">Direct Lake general availability</a></td>
    <td>Direct Lake mode is now generally available for production workloads.</td>
  </tr>
  <tr>
    <td>June 2026</td>
    <td><a href="/en-us/fabric/warehouse-mirroring?utm_source=rss">Warehouse mirroring</a></td>
    <td>Mirror an external warehouse into Fabric with no pipeline.</td>
  </tr>
</table>

<h2 id="pbi">Power BI</h2>
<table>
  <tr>
    <td>July 2026</td>
    <td><a href="https://learn.microsoft.com/en-us/power-bi/new-visual">New card visual</a></td>
    <td>A redesigned card visual ships in Power BI Desktop.</td>
  </tr>
</table>
</body></html>
"""


class FakeFetcher:
    """Serves recorded documents, or raises to simulate a dead source."""

    def __init__(self, body: str | None = None, error: str | None = None) -> None:
        self._body = body
        self._error = error
        self.calls: list[str] = []

    def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        if self._error:
            raise SourceError(self._error)
        return FetchResult(
            body=self._body or "",
            status=200,
            elapsed_ms=12,
            final_url="https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new",
        )


def html_definition(**overrides) -> SourceDefinition:
    defaults = dict(
        name="fabric-whats-new",
        adapter=AdapterType.HTML,
        url="https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new",
        authority=SourceAuthority.OFFICIAL_MICROSOFT,
        parser_version=1,
    )
    defaults.update(overrides)
    return SourceDefinition(**defaults)


# ---------------------------------------------------------------------------
# Clock - injected, never read from the system
# ---------------------------------------------------------------------------


def test_frozen_clock_is_deterministic():
    clock = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))
    assert clock.today() == date(2026, 8, 2)
    assert clock.run_id() == "run-2026-08-02T06-00-00Z"
    assert clock.run_id() == clock.run_id()


def test_frozen_clock_rejects_a_naive_instant():
    """An ambiguous instant in a history is worse than no history."""
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 8, 2, 6, 0))


def test_system_clock_is_utc():
    assert SystemClock().now().tzinfo is timezone.utc


def test_no_engine_module_reads_the_clock_directly():
    """The injected-clock rule, enforced rather than trusted (ADR-0021)."""
    import pathlib

    engine = pathlib.Path(__file__).resolve().parents[1] / "ke"
    offenders = []
    for path in engine.rglob("*.py"):
        if path.name == "clock.py":
            continue
        text = path.read_text()
        if "datetime.now(" in text or "date.today(" in text:
            offenders.append(path.name)
    assert not offenders, f"these modules read the clock directly: {offenders}"


# ---------------------------------------------------------------------------
# Identity - the rule that stops duplicate permanent IDs
# ---------------------------------------------------------------------------


def test_canonical_url_wins_over_everything_else():
    identity = compute_identity(
        canonical_url="https://example.invalid/a",
        source_identifier="abc",
        title="A title",
    )
    assert identity.basis is IdentityBasis.CANONICAL_URL
    assert identity.is_durable


def test_identity_falls_through_the_hierarchy_in_order():
    assert compute_identity(source_identifier="abc", title="T").basis is (
        IdentityBasis.SOURCE_IDENTIFIER
    )
    assert compute_identity(title="Direct Lake reaches GA").basis is IdentityBasis.TITLE_HASH
    assert compute_identity(summary="only a summary").basis is (
        IdentityBasis.CONTENT_FINGERPRINT
    )


def test_an_item_with_no_identifiable_identity_is_rejected():
    """Better to fail loudly than to fabricate an identity."""
    with pytest.raises(ValueError):
        compute_identity()


def test_reordering_and_rewording_do_not_change_a_url_identity():
    """The failure this module exists to prevent.

    A table row that moves, or whose wording changes, must keep its identity --
    otherwise M2 mints a second permanent Feature ID for knowledge already
    stored, and permanent means permanent.
    """
    before = compute_identity(
        canonical_url="https://example.invalid/direct-lake", title="Announcing Direct Lake GA"
    )
    after = compute_identity(
        canonical_url="https://example.invalid/direct-lake",
        title="Direct Lake is now generally available",
    )
    assert before.key == after.key


def test_title_normalisation_survives_marketing_rewording():
    assert normalise_title("Announcing general availability of Direct Lake") == (
        normalise_title("Direct Lake is now generally available")
    )


def test_title_normalisation_still_separates_different_features():
    assert normalise_title("Direct Lake GA") != normalise_title("Warehouse mirroring GA")


def test_lifecycle_wording_collapses_when_only_nouns_differ():
    """One Feature ID per concept, with GA recorded as a revision (ADR-0009)."""
    assert normalise_title("Direct Lake preview") == normalise_title(
        "Direct Lake general availability"
    )


def test_title_identity_does_not_survive_a_verb_change():
    """A known and accepted limitation, pinned so it is not mistaken for a bug.

    Removing every lifecycle verb would mean an ever-growing noise list that
    steadily raises the risk of two different features colliding -- worse than
    missing a match. This is why the title hash is third in the hierarchy.
    """
    assert normalise_title("Direct Lake enters preview") != normalise_title(
        "Direct Lake reaches general availability"
    )


def test_tracking_parameters_do_not_change_identity():
    plain = compute_identity(canonical_url=canonical_url("https://x.invalid/a"))
    tracked = compute_identity(
        canonical_url=canonical_url("https://x.invalid/a?utm_source=rss&ocid=abc")
    )
    assert plain.key == tracked.key


def test_identity_records_what_it_matched_on():
    """Debugging a duplicate starts with 'what were we matching on?'."""
    identity = compute_identity(title="Direct Lake GA")
    assert identity.raw_value == normalise_title("Direct Lake GA")
    assert identity.is_durable is False


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_canonical_url_strips_tracking_and_normalises_shape():
    assert canonical_url("HTTPS://Learn.Microsoft.COM/en-us/x/?utm_source=rss#top") == (
        "https://learn.microsoft.com/en-us/x"
    )


def test_canonical_url_sorts_surviving_parameters():
    assert canonical_url("https://x.invalid/a?b=2&a=1") == canonical_url(
        "https://x.invalid/a?a=1&b=2"
    )


@pytest.mark.parametrize(
    "text,expected_date,precision,confidence",
    [
        ("Released 2026-07-15", date(2026, 7, 15), DatePrecision.DAY, DateConfidence.EXACT),
        ("July 15, 2026", date(2026, 7, 15), DatePrecision.DAY, DateConfidence.EXACT),
        ("July 2026", date(2026, 7, 1), DatePrecision.MONTH, DateConfidence.EXACT),
        ("Wave 2026", date(2026, 1, 1), DatePrecision.YEAR, DateConfidence.INFERRED),
        ("no date here", None, DatePrecision.DAY, DateConfidence.INFERRED),
    ],
)
def test_parse_date_reports_precision_and_confidence(text, expected_date, precision, confidence):
    assert parse_date(text) == (expected_date, precision, confidence)


def test_a_full_date_is_preferred_over_the_month_inside_it():
    """"July 15, 2026" also contains "July 2026"; the day must win."""
    assert parse_date("July 15, 2026")[1] is DatePrecision.DAY


def test_truncate_enforces_the_copyright_limit():
    """One confirmed source ships 29,000-character articles (ADR-0003)."""
    long_text = " ".join(["word"] * 500)
    result = truncate_summary(long_text, 120)
    assert len(result.split()) == 120  # the ellipsis attaches to the last word
    assert result.endswith("…")


def test_truncate_leaves_short_text_alone():
    assert truncate_summary("short enough", 120) == "short enough"


def test_html_to_text_drops_scripts_and_collapses_whitespace():
    assert html_to_text("<p>Hello</p><script>evil()</script><p>world</p>") == "Hello world"


def test_content_hash_ignores_reflowing():
    assert content_hash("T", "a b") == content_hash("T", "a\n  b")


def test_slugify_is_stable_and_bounded():
    assert slugify("Direct Lake: General Availability!") == "direct-lake-general-availability"
    assert len(slugify("x " * 100)) <= 60


# ---------------------------------------------------------------------------
# The HTML adapter - M1's primary source
# ---------------------------------------------------------------------------


def test_html_adapter_extracts_one_item_per_update_row():
    source = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK)
    items = source.discover()
    titles = [item.title for item in items]
    assert "Direct Lake general availability" in titles
    assert "Warehouse mirroring" in titles
    assert "New card visual" in titles


def test_header_rows_are_not_mistaken_for_updates():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    assert not any(item.title == "Feature" for item in items)


def test_month_dates_are_extracted_with_month_precision():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    direct_lake = next(i for i in items if i.title.startswith("Direct Lake"))
    assert direct_lake.published_date == date(2026, 7, 1)
    assert direct_lake.date_precision is DatePrecision.MONTH
    assert direct_lake.date_confidence is DateConfidence.EXACT
    # Month precision is exactly what ADR-0005 needs for minting.
    assert direct_lake.id_basis_date == date(2026, 7, 1)


def test_the_section_heading_becomes_a_tag():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    power_bi = next(i for i in items if i.title == "New card visual")
    assert "Power BI" in power_bi.raw_tags


def test_relative_links_are_resolved_and_canonicalised():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    item = next(i for i in items if i.title == "Warehouse mirroring")
    assert item.source_url == "https://learn.microsoft.com/en-us/fabric/warehouse-mirroring"


def test_every_item_carries_full_provenance():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    provenance = items[0].provenance
    assert provenance.adapter_type is AdapterType.HTML
    assert provenance.extraction_method is ExtractionMethod.HTML_TABLE_ROW
    assert provenance.parser_version == 1
    assert provenance.run_id == "run-2026-08-02T06-00-00Z"
    assert provenance.identity_basis is IdentityBasis.CANONICAL_URL
    assert provenance.identity_key == items[0].identity.key
    assert provenance.selector


def test_parser_version_comes_from_configuration():
    """Changing extraction strategy is a visible config change (ADR-0024)."""
    source = HtmlTableSource(
        html_definition(parser_version=3), FakeFetcher(WHATS_NEW_HTML), CLOCK
    )
    assert source.discover()[0].provenance.parser_version == 3


def test_an_empty_page_is_a_parser_break_not_a_quiet_week():
    """Returning [] here would be indistinguishable from 'no updates'."""
    source = HtmlTableSource(html_definition(), FakeFetcher("<html><body></body></html>"), CLOCK)
    with pytest.raises(SourceError, match="structure has probably changed"):
        source.discover()


def test_a_dead_source_raises_rather_than_returning_nothing():
    source = HtmlTableSource(html_definition(), FakeFetcher(error="HTTP 403 Forbidden"), CLOCK)
    with pytest.raises(SourceError):
        source.discover()


def test_output_is_deterministic():
    """Same inputs, byte-identical output (ADR-0022)."""
    first = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    second = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    assert [i.identity.key for i in first] == [i.identity.key for i in second]


def test_items_are_sorted_by_date_then_identity():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    dates = [i.published_date for i in items]
    assert dates == sorted(dates)


def test_undated_items_sort_last():
    undated = HtmlTableSource(
        html_definition(),
        FakeFetcher(
            "<html><h2>S</h2><table>"
            "<tr><td><a href='/b'>No date item</a></td><td>Body text</td></tr>"
            "<tr><td>July 2026</td><td><a href='/a'>Dated item</a></td><td>Body</td></tr>"
            "</table></html>"
        ),
        CLOCK,
    ).discover()
    assert undated[-1].published_date is None


# ---------------------------------------------------------------------------
# Source definitions - immutable, versioned
# ---------------------------------------------------------------------------


def test_source_definition_reads_the_adapter_version_block():
    definition = SourceDefinition.from_config(
        {
            "name": "s",
            "url": "https://x.invalid",
            "authority": "official-microsoft",
            "adapter": {"name": "html", "version": 4},
        }
    )
    assert definition.adapter is AdapterType.HTML
    assert definition.parser_version == 4


def test_disabled_and_replaced_sources_are_retained_but_not_polled():
    """Definitions are permanent; provenance points at them forever."""
    for status in (SourceStatus.DISABLED, SourceStatus.REPLACED):
        assert SourceDefinition(
            name="s",
            adapter=AdapterType.RSS,
            url="https://x.invalid",
            authority=SourceAuthority.THIRD_PARTY,
            status=status,
        ).is_pollable is False

    for status in (SourceStatus.ACTIVE, SourceStatus.DEPRECATED):
        assert SourceDefinition(
            name="s",
            adapter=AdapterType.RSS,
            url="https://x.invalid",
            authority=SourceAuthority.THIRD_PARTY,
            status=status,
        ).is_pollable is True


def test_fallback_chains_are_parsed_recursively():
    definition = SourceDefinition.from_config(
        {
            "name": "primary",
            "url": "https://x.invalid",
            "authority": "official-microsoft",
            "adapter": {"name": "html"},
            "fallback": [
                {
                    "name": "secondary",
                    "url": "https://y.invalid",
                    "authority": "official-microsoft",
                    "role": "secondary",
                    "adapter": {"name": "atom"},
                }
            ],
        }
    )
    assert definition.fallbacks[0].role is SourceRole.SECONDARY


# ---------------------------------------------------------------------------
# Orchestration - fallback chains and health
# ---------------------------------------------------------------------------


def test_a_healthy_primary_is_used_and_no_fallback_is_touched():
    fallback_fetcher = FakeFetcher("should not be used")
    definition = html_definition(
        fallbacks=(
            html_definition(name="backup", role=SourceRole.SECONDARY),
        )
    )
    result = discover_all([definition], clock=CLOCK, fetcher=FakeFetcher(WHATS_NEW_HTML))
    assert len(result.items) == 3
    assert result.health["fabric-whats-new"].state is HealthState.HEALTHY
    assert "backup" not in result.health
    assert fallback_fetcher.calls == []


def test_a_failed_primary_falls_back_and_the_result_is_degraded():
    """Falling back is not failure, but it is not business as usual either."""

    class FailThenSucceed:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            if len(self.calls) == 1:
                raise SourceError("HTTP 403 Forbidden")
            return FetchResult(WHATS_NEW_HTML, 200, 5, url)

    definition = html_definition(
        fallbacks=(html_definition(name="backup", role=SourceRole.SECONDARY),)
    )
    result = discover_all([definition], clock=CLOCK, fetcher=FailThenSucceed())

    assert len(result.items) == 3
    assert result.health["fabric-whats-new"].state is HealthState.FAILED
    assert result.health["backup"].state is HealthState.DEGRADED
    assert result.review_items == []


def test_a_fully_failed_chain_raises_a_review_item_not_an_empty_result():
    """'No updates' and 'we could not look' must never be indistinguishable."""
    definition = html_definition(
        fallbacks=(
            html_definition(name="backup", role=SourceRole.SECONDARY),
            html_definition(name="manual", role=SourceRole.MANUAL_REVIEW),
        )
    )
    result = discover_all([definition], clock=CLOCK, fetcher=FakeFetcher(error="HTTP 500"))

    assert result.items == []
    assert len(result.review_items) == 1
    review = result.review_items[0]
    assert isinstance(review, ReviewItem)
    assert "500" in review.reason
    assert SourceRole.MANUAL_REVIEW in review.attempted_roles


def test_a_failing_source_does_not_stop_the_others():
    """The rule from ADR-0019, at the orchestration level."""

    class PerUrl:
        def fetch(self, url):
            if "dead" in url:
                raise SourceError("HTTP 403 Forbidden")
            return FetchResult(WHATS_NEW_HTML, 200, 5, url)

    result = discover_all(
        [
            html_definition(name="dead", url="https://dead.invalid/x"),
            html_definition(name="alive", url="https://alive.invalid/x"),
        ],
        clock=CLOCK,
        fetcher=PerUrl(),
    )
    assert result.health["dead"].state is HealthState.FAILED
    assert result.health["alive"].state is HealthState.HEALTHY
    assert len(result.items) == 3  # the healthy source still delivered


def test_an_adapter_crash_is_recorded_rather_than_ending_the_run():
    class Exploding:
        def fetch(self, url):
            raise RuntimeError("unexpected adapter bug")

    result = discover_all([html_definition()], clock=CLOCK, fetcher=Exploding())
    assert result.health["fabric-whats-new"].state is HealthState.FAILED
    assert "adapter error" in result.attempts[0].failure_reason


def test_a_collapse_against_a_known_baseline_is_flagged_as_a_parser_break():
    """The check that stops the pipeline dying quietly."""
    known = {
        "fabric-whats-new": SourceHealth(
            source_name="fabric-whats-new", recent_item_counts=(20, 22, 19, 21)
        )
    }
    # A page that still parses but yields only one row.
    thin = (
        "<html><h2>S</h2><table>"
        "<tr><td>July 2026</td><td><a href='/a'>Only item</a></td><td>Body</td></tr>"
        "</table></html>"
    )
    result = discover_all(
        [html_definition()], clock=CLOCK, fetcher=FakeFetcher(thin), known_health=known
    )
    health = result.health["fabric-whats-new"]
    assert health.state is HealthState.DEGRADED
    assert "parser break" in health.last_failure_reason


def test_non_pollable_sources_are_skipped_but_reported():
    result = discover_all(
        [html_definition(status=SourceStatus.DISABLED)],
        clock=CLOCK,
        fetcher=FakeFetcher(WHATS_NEW_HTML),
    )
    assert result.skipped == ["fabric-whats-new"]
    assert result.attempts == []


def test_health_summary_groups_every_state():
    grouped = health_summary(
        {
            "a": SourceHealth(source_name="a", state=HealthState.HEALTHY),
            "b": SourceHealth(source_name="b", state=HealthState.FAILED),
        }
    )
    assert grouped[HealthState.HEALTHY] == ["a"]
    assert grouped[HealthState.FAILED] == ["b"]


def test_sort_items_is_stable_across_input_order():
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    assert sort_items(list(reversed(items))) == sort_items(items)


# ---------------------------------------------------------------------------
# Date extraction: only a dedicated date cell is trusted
#
# Found by measuring the real Microsoft Learn source, not by reasoning. The
# adapter originally searched the whole row for a date, which stamped a
# prose-mentioned month as an EXACT publication date -- and ADR-0005 mints a
# PERMANENT Feature ID from that month.
# ---------------------------------------------------------------------------

#: A verbatim row from the production page, reduced to its structure. There is
#: no date column; "December 2025" appears only inside a link title.
PROSE_MONTH_HTML = """
<html><h2>Data Factory in Microsoft Fabric</h2><table>
  <tr>
    <td><b>Data Factory On-premises data gateway manual update option (Preview)</b></td>
    <td>The <a href="https://blog.fabric.microsoft.com/gateway-dec">Gateway December 2025 release</a>
        adds a manual update option.</td>
  </tr>
</table></html>
"""

#: The other real shape: a three-column table whose first cell is only a date.
DATE_CELL_HTML = """
<html><h2>Fabric IQ</h2><table>
  <tr><td>July 2026</td><td><b>Plan (Generally Available)</b></td>
      <td><a href="/en-us/fabric/iq/plan">Plan in Fabric IQ</a> is now GA.</td></tr>
</table></html>
"""


def test_a_month_mentioned_in_prose_is_not_treated_as_a_publication_date():
    """The defect: a permanent ID minted from a date scraped out of a sentence."""
    items = HtmlTableSource(html_definition(), FakeFetcher(PROSE_MONTH_HTML), CLOCK).discover()
    assert len(items) == 1
    item = items[0]
    assert item.published_date is None
    assert item.date_confidence is DateConfidence.INFERRED
    # Falls back to the discovery month, which is honest (ADR-0005).
    assert item.id_basis_date == CLOCK.today()
    assert "no date cell" in item.provenance.selector


def test_a_dedicated_date_cell_is_trusted():
    items = HtmlTableSource(html_definition(), FakeFetcher(DATE_CELL_HTML), CLOCK).discover()
    item = items[0]
    assert item.published_date == date(2026, 7, 1)
    assert item.date_precision is DatePrecision.MONTH
    assert item.date_confidence is DateConfidence.EXACT
    assert "date from cell 0" in item.provenance.selector


def test_the_date_cell_is_excluded_from_the_summary_and_title():
    """A bare month must not leak into the knowledge text."""
    item = HtmlTableSource(html_definition(), FakeFetcher(DATE_CELL_HTML), CLOCK).discover()[0]
    assert "July 2026" not in item.summary
    assert "July 2026" not in item.title


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("July 2026", date(2026, 7, 1)),
        ("  July 2026  ", date(2026, 7, 1)),
        ("2026-07-15", date(2026, 7, 15)),
        ("July 15, 2026", date(2026, 7, 15)),
        ("The Gateway December 2025 release", None),  # prose
        ("Released in July 2026", None),  # prose
        ("July 2026 update notes", None),  # prose
        ("", None),
    ],
)
def test_parse_date_cell_requires_the_cell_to_be_only_a_date(cell, expected):
    assert parse_date_cell(cell)[0] == expected
