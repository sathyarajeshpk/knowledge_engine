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
from ke.acquisition.discover import ReviewItem, discover_all, health_summary
from ke.acquisition.identity import IdentityBasis, compute_identity, normalise_title
from ke.models import (
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    HealthState,
    IdentityConfidence,
    Lifecycle,
    SourceAuthority,
    SourceHealth,
    SourceRepresentation,
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
from ke.acquisition.sources.base import (
    FetchResult,
    SourceDefinition,
    SourceError,
    sort_items,
)
from ke.acquisition.sources.html_table import HtmlTableSource
from ke.acquisition.sources.markdown_table import (
    MarkdownTableSource,
    resolve_doc_link,
    strip_markdown,
)

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


def test_a_disabled_fallback_is_skipped_rather_than_polled():
    """Retiring a link in a chain must actually retire it.

    Without this, `status: disabled` on a fallback is decorative: the chain would
    still poll it, and a secondary deliberately taken out of service because
    failing over to it *damages the pack* would fire anyway during the next
    outage -- automatically, with no human present.
    """

    class PrimaryFails:
        def __init__(self):
            self.calls = []

        def fetch(self, url):
            self.calls.append(url)
            raise SourceError("HTTP 403 Forbidden")

    fetcher = PrimaryFails()
    definition = html_definition(
        fallbacks=(
            markdown_definition(status=SourceStatus.DISABLED),
            html_definition(name="manual", role=SourceRole.MANUAL_REVIEW),
        )
    )
    result = discover_all([definition], clock=CLOCK, fetcher=fetcher)

    assert "fabric-whats-new-markdown" in result.skipped
    assert "fabric-whats-new-markdown" not in result.health
    assert not any(".md" in url for url in fetcher.calls), "the disabled link was polled"
    # The chain still fails loudly rather than silently reporting nothing.
    assert len(result.review_items) == 1


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


# ---------------------------------------------------------------------------
# The Markdown adapter - M1's secondary source
#
# Same authoritative content as the HTML primary, from different
# infrastructure. The interesting risk is not parsing: it is that identity
# rests on canonical URLs, and here those must be *reconstructed* from relative
# document paths. Most of these tests are about that reconstruction.
# ---------------------------------------------------------------------------

#: Modelled on `MicrosoftDocs/fabric-docs/docs/fundamentals/whats-new.md`,
#: including its real shapes: YAML front matter, a "## Section" heading, an
#: alignment row, a date column in one table and none in another, a relative
#: `../` link, a same-directory link, an absolute link and an anchor-only link.
WHATS_NEW_MD = """---
title: What's new in Microsoft Fabric?
ms.date: 07/28/2026
---

# What's new in Microsoft Fabric?

## In this article

## Generally available features

| Month | Feature | Description |
|:------|:--------|:------------|
| July 2026 | [Direct Lake general availability](../onelake/direct-lake-ga.md#modes) | Direct Lake mode is generally available. |
| June 2026 | [Warehouse mirroring](mirroring.md) | Mirror an external warehouse. |

## Features currently in preview

| Feature | Description |
|:--------|:------------|
| **Fabric IQ Plan** | See the [Gateway December 2025 release](https://blog.fabric.microsoft.com/gateway-dec) notes. |
| [Anchored only](#somewhere) | A link that goes nowhere resolvable. |
"""


def markdown_definition(**overrides) -> SourceDefinition:
    defaults = dict(
        name="fabric-whats-new-markdown",
        adapter=AdapterType.MARKDOWN,
        url=(
            "https://raw.githubusercontent.com/MicrosoftDocs/fabric-docs/"
            "main/docs/fundamentals/whats-new.md"
        ),
        authority=SourceAuthority.OFFICIAL_MICROSOFT,
        parser_version=1,
        role=SourceRole.SECONDARY,
        options={
            "doc_path": "docs/fundamentals/whats-new.md",
            "docs_prefix": "docs/",
            "rendered_base_url": "https://learn.microsoft.com/en-us/fabric",
        },
    )
    defaults.update(overrides)
    return SourceDefinition(**defaults)


def markdown_items():
    return MarkdownTableSource(
        markdown_definition(), FakeFetcher(WHATS_NEW_MD), CLOCK
    ).discover()


def test_markdown_adapter_extracts_one_item_per_table_row():
    titles = [item.title for item in markdown_items()]
    assert "Direct Lake general availability" in titles
    assert "Warehouse mirroring" in titles
    assert "Fabric IQ Plan" in titles


def test_markdown_alignment_rows_and_headers_are_not_updates():
    titles = [item.title for item in markdown_items()]
    assert not any(set(title) <= set(":- ") for title in titles)
    assert "Feature" not in titles


@pytest.mark.parametrize(
    "target,expected",
    [
        # Up out of `fundamentals/`, into a sibling directory; anchor dropped.
        (
            "../onelake/direct-lake-ga.md#modes",
            "https://learn.microsoft.com/en-us/fabric/onelake/direct-lake-ga",
        ),
        # Same directory as the document itself.
        (
            "mirroring.md",
            "https://learn.microsoft.com/en-us/fabric/fundamentals/mirroring",
        ),
        # Already absolute: canonicalised, not rebuilt.
        (
            "https://blog.fabric.microsoft.com/x?utm_source=rss",
            "https://blog.fabric.microsoft.com/x",
        ),
        ("#in-page-anchor", None),      # not a document
        ("../../media/diagram.png", None),  # not Markdown
        ("../../../outside.md", None),  # escapes the docs root
        ("", None),
    ],
)
def test_relative_document_links_resolve_to_the_url_they_render_as(target, expected):
    assert (
        resolve_doc_link(
            target,
            doc_path="docs/fundamentals/whats-new.md",
            docs_prefix="docs/",
            base_url="https://learn.microsoft.com/en-us/fabric",
        )
        == expected
    )


def test_an_unresolvable_link_yields_no_url_rather_than_a_guess():
    """The property this adapter's design turns on.

    A fabricated canonical URL would be worse than none: identity would look
    durable (ADR-0023's strongest basis) while resting on an invention, and the
    resulting Feature ID is permanent. Falling back to a weaker basis is honest.
    """
    item = next(i for i in markdown_items() if i.title == "Anchored only")
    assert item.identity.basis is not IdentityBasis.CANONICAL_URL
    assert item.source_url == markdown_definition().url


def test_a_resolved_link_gives_the_same_identity_as_the_html_primary():
    """What makes this a genuine fallback rather than a second source.

    If the two representations produced different identities for the same
    update, failing over would mint duplicate permanent Feature IDs -- the exact
    outcome the fallback chain exists to avoid.
    """
    from_markdown = next(
        i for i in markdown_items() if i.title == "Warehouse mirroring"
    )
    from_html = compute_identity(
        canonical_url="https://learn.microsoft.com/en-us/fabric/fundamentals/mirroring"
    )
    assert from_markdown.identity.key == from_html.key


def test_the_two_representations_agree_on_identity_even_when_titles_differ():
    """A known and accepted divergence, pinned so it is not read as a bug.

    The adapters title rows differently: HTML takes the first linked text,
    Markdown takes the first content cell. On a row whose feature name is bold
    text and whose only link sits in the description, they therefore disagree.

    That is tolerable precisely because identity does *not* come from the title
    here -- both resolve the same canonical URL, so failing over produces a
    revision at worst, never a duplicate permanent Feature ID. If identity ever
    stops agreeing across representations, the fallback chain is unsafe.
    """
    from_markdown = next(i for i in markdown_items() if i.title == "Fabric IQ Plan")
    from_html = HtmlTableSource(
        html_definition(), FakeFetcher(PROSE_MONTH_HTML), CLOCK
    ).discover()[0]

    assert from_markdown.title != from_html.title
    assert from_markdown.identity.key == from_html.identity.key


def test_markdown_link_syntax_does_not_leak_into_the_knowledge_text():
    item = next(i for i in markdown_items() if i.title.startswith("Direct Lake"))
    assert "[" not in item.title and "](" not in item.title
    assert "**" not in item.summary


def test_strip_markdown_keeps_the_label_and_falls_back_to_the_target():
    assert strip_markdown("**[Direct Lake](../a.md)** is `GA`") == "Direct Lake is GA"
    assert strip_markdown("[](../a.md)") == "../a.md"


def test_markdown_dates_come_only_from_a_dedicated_cell():
    """The same rule as the HTML adapter, for the same permanent-ID reason."""
    dated = next(i for i in markdown_items() if i.title.startswith("Direct Lake"))
    assert dated.published_date == date(2026, 7, 1)
    assert dated.date_precision is DatePrecision.MONTH
    assert dated.date_confidence is DateConfidence.EXACT
    assert "date from cell 0" in dated.provenance.selector

    prose = next(i for i in markdown_items() if i.title == "Fabric IQ Plan")
    assert prose.published_date is None  # "December 2025" is inside a sentence
    assert prose.date_confidence is DateConfidence.INFERRED
    assert "no date cell" in prose.provenance.selector


def test_the_markdown_section_heading_becomes_a_tag():
    item = next(i for i in markdown_items() if i.title == "Fabric IQ Plan")
    assert "Features currently in preview" in item.raw_tags


def test_the_table_of_contents_heading_is_not_a_section():
    assert all(
        "In this article" not in item.raw_tags for item in markdown_items()
    )


def test_markdown_items_record_their_representation_and_adapter():
    """The discovery chain, end to end on one item."""
    provenance = markdown_items()[0].provenance
    assert provenance.source_representation is SourceRepresentation.MARKDOWN
    assert provenance.adapter_type is AdapterType.MARKDOWN
    assert provenance.extraction_method is ExtractionMethod.MARKDOWN_TABLE_ROW
    assert provenance.parser_version == 1
    assert provenance.source_role is SourceRole.SECONDARY
    assert provenance.run_id == "run-2026-08-02T06-00-00Z"


def test_html_items_record_the_html_representation():
    """The distinction is only useful if the two representations differ."""
    items = HtmlTableSource(html_definition(), FakeFetcher(WHATS_NEW_HTML), CLOCK).discover()
    assert items[0].provenance.source_representation is SourceRepresentation.HTML


def test_a_markdown_source_without_a_base_url_refuses_to_run():
    """Better to fail the source than to emit items with invented identities."""
    source = MarkdownTableSource(
        markdown_definition(options={"doc_path": "docs/x.md"}),
        FakeFetcher(WHATS_NEW_MD),
        CLOCK,
    )
    with pytest.raises(SourceError, match="rendered_base_url"):
        source.discover()


def test_an_empty_markdown_document_is_a_parser_break():
    source = MarkdownTableSource(
        markdown_definition(), FakeFetcher("# Heading only\n\nNo tables.\n"), CLOCK
    )
    with pytest.raises(SourceError, match="structure"):
        source.discover()


def test_markdown_output_is_deterministic():
    assert [i.identity.key for i in markdown_items()] == [
        i.identity.key for i in markdown_items()
    ]


# ---------------------------------------------------------------------------
# Identity confidence and collisions — Recommendation C
#
# The rule these tests exist to protect:
#
#     A collision is never a merge.
#
# Measured against production, one announcement URL is cited by 18 rows covering
# 11 genuinely different features. Under the old behaviour those collapsed into
# one identity, and M2 would have minted ONE permanent Feature ID with the other
# ten features silently absent — not flagged, not queued, absent.
# ---------------------------------------------------------------------------

#: Two independent features announced in one blog post — the production shape.
SHARED_ANNOUNCEMENT_HTML = """
<html><h2>Data Factory in Microsoft Fabric</h2><table>
  <tr><td>July 2026</td>
      <td><a href="https://blog.example.invalid/copy-job">Edit Copy job via JSON payloads</a></td>
      <td>Edit a Copy job by posting JSON.</td></tr>
  <tr><td>July 2026</td>
      <td><a href="https://blog.example.invalid/copy-job">Switch between full and incremental copy</a></td>
      <td>Choose a copy mode per run.</td></tr>
  <tr><td>July 2026</td>
      <td><a href="https://learn.microsoft.com/en-us/fabric/solo">A feature of its own</a></td>
      <td>This one has its own documentation page.</td></tr>
</table></html>
"""


def shared_announcement_result():
    return discover_all(
        [html_definition()], clock=CLOCK, fetcher=FakeFetcher(SHARED_ANNOUNCEMENT_HTML)
    )


def test_a_shared_announcement_is_never_silently_merged():
    """The defect this milestone exists to prevent."""
    result = shared_announcement_result()

    assert len(result.collisions) == 1
    collision = result.collisions[0]
    assert collision.feature_count == 2
    assert collision.announcement_url == "https://blog.example.invalid/copy-job"
    # Both features survive as items; neither is dropped.
    assert len(result.items) == 3


def test_a_shared_announcement_yields_medium_confidence_and_is_not_minted():
    result = shared_announcement_result()
    shared = [i for i in result.items if "copy" in i.title.lower()]

    assert len(shared) == 2
    for item in shared:
        assert item.identity_confidence is IdentityConfidence.MEDIUM
        assert item.mints_automatically is False
        assert "distinct features" in item.confidence_reason


def test_an_exclusive_announcement_is_high_confidence_and_mints():
    result = shared_announcement_result()
    solo = next(i for i in result.items if i.title == "A feature of its own")

    assert solo.identity_confidence is IdentityConfidence.HIGH
    assert solo.mints_automatically is True
    assert result.mintable == [solo]
    assert len(result.needs_review) == 2


def test_collision_titles_are_published_not_normalised():
    """The review queue is read by a person, not by the matcher."""
    collision = shared_announcement_result().collisions[0]
    assert "Edit Copy job via JSON payloads" in collision.titles


def test_an_unresolvable_link_is_medium_never_high():
    """A weak anchor may be identifiable, but it must not mint on its own."""
    item = next(i for i in markdown_items() if i.title == "Anchored only")
    assert item.identity.basis is IdentityBasis.TITLE_HASH
    assert item.identity_confidence is IdentityConfidence.MEDIUM
    assert item.mints_automatically is False
    assert item.announcement_url is None


def test_announcement_url_is_null_rather_than_the_document_we_read():
    """`source_url` falls back; `announcement_url` must not (ADR-0027).

    Recording the page we were reading as this feature's announcement would
    invent a citation that does not exist.
    """
    item = next(i for i in markdown_items() if i.title == "Anchored only")
    assert item.source_url.endswith(".md")  # the fallback, for provenance
    assert item.announcement_url is None  # but no announcement is claimed


def test_a_non_official_source_never_mints_even_at_high_confidence():
    """The gate is identity confidence AND authority, deliberately both."""
    result = discover_all(
        [html_definition(authority=SourceAuthority.THIRD_PARTY)],
        clock=CLOCK,
        fetcher=FakeFetcher(WHATS_NEW_HTML),
    )
    high = [i for i in result.items if i.identity_confidence is IdentityConfidence.HIGH]
    assert high, "expected some high-confidence items"
    assert result.mintable == []


def test_confidence_never_changes_an_items_identity():
    """Confidence gates minting; it must not leak into the ID.

    If grading changed identity, an item's permanent Feature ID would depend on
    what else happened to be discovered in the same run.
    """
    alone = HtmlTableSource(
        html_definition(), FakeFetcher(SHARED_ANNOUNCEMENT_HTML), CLOCK
    ).discover()
    graded = shared_announcement_result().items

    assert sorted(i.identity.key for i in alone) == sorted(
        i.identity.key for i in graded
    )


def test_confidence_is_deterministic():
    first = shared_announcement_result()
    second = shared_announcement_result()
    assert [
        (i.identity.key, i.identity_confidence) for i in first.items
    ] == [(i.identity.key, i.identity_confidence) for i in second.items]


def test_every_item_carries_a_reason_for_its_confidence():
    """A person triaging must not have to re-derive the rule."""
    for item in shared_announcement_result().items:
        assert item.confidence_reason


def test_collisions_are_ordered_worst_first():
    """Triage should meet the announcement hiding the most knowledge first."""
    from ke.acquisition.confidence import collisions as find_collisions

    items = markdown_items() + shared_announcement_result().items
    found = find_collisions(items)
    counts = [c.feature_count for c in found]
    assert counts == sorted(counts, reverse=True)


# ---------------------------------------------------------------------------
# Knowledge Lifecycle — the acquisition axis (ADR-0029)
# ---------------------------------------------------------------------------


def test_grading_moves_items_out_of_discovered():
    result = shared_announcement_result()
    assert all(i.lifecycle is not Lifecycle.DISCOVERED for i in result.items)


def test_the_gate_decides_approved_versus_queued():
    result = shared_announcement_result()
    for item in result.items:
        expected = Lifecycle.APPROVED if item.mints_automatically else Lifecycle.QUEUED
        assert item.lifecycle is expected


def test_queuing_never_blocks_the_rest_of_the_run():
    """One ambiguous row must not stall a weekly harvest."""
    result = shared_announcement_result()
    assert len(result.needs_review) == 2
    assert len(result.mintable) == 1, "high-confidence items still came through"


def test_lifecycle_transitions_move_forward_only():
    from ke.models import is_valid_transition

    assert is_valid_transition(Lifecycle.QUEUED, Lifecycle.APPROVED)
    assert is_valid_transition(Lifecycle.APPROVED, Lifecycle.MINTED)
    assert is_valid_transition(Lifecycle.MINTED, Lifecycle.SUPERSEDED)
    # Backwards is a bug, not a state change.
    assert not is_valid_transition(Lifecycle.MINTED, Lifecycle.DISCOVERED)
    assert not is_valid_transition(Lifecycle.QUEUED, Lifecycle.MINTED)
    assert not is_valid_transition(Lifecycle.ARCHIVED, Lifecycle.APPROVED)


def test_a_repeated_transition_is_allowed():
    """Re-running discovery over a queued item must not be an error.

    Without this the weekly run could never be idempotent.
    """
    from ke.models import is_valid_transition

    assert is_valid_transition(Lifecycle.QUEUED, Lifecycle.QUEUED)


def test_lifecycle_is_independent_of_object_status():
    """An object can be fully acquired and no longer current."""
    from ke.models import ObjectStatus

    assert Lifecycle.MINTED is not ObjectStatus.REPLACED
    assert set(Lifecycle) & {str(s) for s in ObjectStatus} == set()


# ---------------------------------------------------------------------------
# Minting rules the maintainer decided explicitly
# ---------------------------------------------------------------------------


def test_an_exact_date_is_not_required_to_mint():
    """Month-precision knowledge is still knowledge.

    Date precision is metadata, not an identity requirement — so an item with a
    month-precise or even inferred date still mints, provided its identity is
    trustworthy.
    """
    items = HtmlTableSource(
        html_definition(), FakeFetcher(PROSE_MONTH_HTML), CLOCK
    ).discover()
    graded = discover_all(
        [html_definition()], clock=CLOCK, fetcher=FakeFetcher(PROSE_MONTH_HTML)
    )
    undated = graded.items[0]

    assert items[0].published_date is None
    assert undated.date_confidence is DateConfidence.INFERRED
    assert undated.identity_confidence is IdentityConfidence.HIGH
    assert undated.mints_automatically is True


def test_review_latency_cannot_shift_a_feature_id():
    """A queued item minted later keeps the month it was first seen."""
    from dataclasses import replace as replace_item

    item = markdown_items()[0]
    first_seen = date(2026, 3, 9)
    requeued = replace_item(
        item,
        published_date=None,
        date_confidence=DateConfidence.INFERRED,
        discovered_date=date(2026, 9, 20),   # approved months later
        first_discovered_date=first_seen,
    )
    assert requeued.id_basis_date == first_seen


def test_fallback_execution_is_independent_of_minting():
    """Where knowledge came from must not change what is safe to mint.

    The chain decides the source; the gate decides trust. If falling back
    changed grading, an outage would quietly alter which knowledge became
    permanent.
    """

    class PrimaryFailsMarkdownWorks:
        def fetch(self, url):
            if url.endswith(".md"):
                return FetchResult(WHATS_NEW_MD, 200, 5, url)
            raise SourceError("HTTP 403 Forbidden")

    direct = discover_all(
        [markdown_definition(role=SourceRole.PRIMARY)],
        clock=CLOCK,
        fetcher=FakeFetcher(WHATS_NEW_MD),
    )
    via_fallback = discover_all(
        [html_definition(fallbacks=(markdown_definition(),))],
        clock=CLOCK,
        fetcher=PrimaryFailsMarkdownWorks(),
    )

    assert [(i.identity.key, i.identity_confidence) for i in direct.items] == [
        (i.identity.key, i.identity_confidence) for i in via_fallback.items
    ]


def test_the_markdown_adapter_is_reachable_through_a_fallback_chain():
    """Registration in `ADAPTERS`, proven rather than assumed."""

    class HtmlFailsMarkdownWorks:
        def fetch(self, url):
            if url.endswith(".md"):
                return FetchResult(WHATS_NEW_MD, 200, 5, url)
            raise SourceError("HTTP 403 Forbidden")

    definition = html_definition(fallbacks=(markdown_definition(),))
    result = discover_all([definition], clock=CLOCK, fetcher=HtmlFailsMarkdownWorks())

    assert result.review_items == []
    assert result.items, "the Markdown secondary produced nothing"
    assert result.health["fabric-whats-new"].state is HealthState.FAILED
    assert result.health["fabric-whats-new-markdown"].state is HealthState.DEGRADED
