"""Turning messy source output into the shapes the rest of the engine expects.

Every function here is pure and deterministic: same input, same output, no clock,
no network. That is what lets discovery be replayed later (ADR-0025) and what
makes the adapters testable without touching the internet.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ke.identity import TRACKING_PARAMS
from ke.models import DateConfidence, DatePrecision

MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ],
        start=1,
    )
}

#: `July 2026`. The form Microsoft Learn uses for update rows.
MONTH_YEAR = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
#: `2026-07-15`
ISO_DATE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")
#: `July 15, 2026`
MONTH_DAY_YEAR = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})\b",
    re.IGNORECASE,
)
#: A bare year, only trusted as a last resort.
BARE_YEAR = re.compile(r"\b(?P<year>20\d{2})\b")


def canonical_url(url: str) -> str:
    """Reduce a URL to the stable thing that identifies the article.

    Strips tracking parameters, fragments and trailing slashes, and lower-cases
    the scheme and host. Query parameters that survive are **sorted**, so two
    URLs differing only in parameter order canonicalise identically.

    This matters more than it looks: the canonical URL is the strongest identity
    signal (ADR-0023), so anything that makes it unstable creates duplicate
    permanent Feature IDs.
    """
    parts = urlsplit(url.strip())
    kept = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(kept), "")
    )


def url_hash(url: str) -> str:
    return "sha256:" + hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def content_hash(title: str, summary: str) -> str:
    """Fingerprint of the knowledge itself, used to detect that a source changed.

    Whitespace is collapsed so that reflowing a paragraph is not mistaken for a
    revision.
    """
    normalised = " ".join(f"{title}\n{summary}".split())
    return "sha256:" + hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    """Visible text from a fragment or document, whitespace collapsed."""
    extractor = _TextExtractor()
    extractor.feed(html)
    return " ".join(" ".join(extractor.parts).split())


def truncate_summary(text: str, max_words: int) -> str:
    """Cut a summary to the pack's word limit.

    ADR-0003 forbids storing full third-party article text, and one confirmed
    source ships 29,000-character articles. Truncation is not a nicety here; it
    is how the copyright rule is honoured at ingest rather than being caught
    later by `ke validate`.
    """
    words = text.split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",;:") + "…"


#: A cell that is *only* a date, e.g. `July 2026`. Anchored deliberately: a
#: month mentioned inside a description is not a publication date.
DATE_ONLY_CELL = re.compile(
    r"^\s*(?:(?P<iso>\d{4}-\d{2}-\d{2})"
    r"|(?P<mdy>(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},?\s+\d{4})"
    r"|(?P<my>(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4}))\s*$",
    re.IGNORECASE,
)


def parse_date_cell(cell: str) -> tuple[date | None, DatePrecision, DateConfidence]:
    """Read a date from a cell that must contain *nothing but* a date.

    This exists because `parse_date` searching a whole table row is unsafe. Real
    measurement of the Microsoft Learn source found rows like::

        |**Data Factory gateway manual update (Preview)** | The [Gateway
        December 2025 release](...) adds ...|

    Searching that row yields "December 2025" and, worse, labels it
    `EXACT` -- so the item would be stamped with a publication month scraped
    from prose, and ADR-0005 would mint a **permanent** Feature ID from it.

    Only a dedicated date cell is trusted. Anything else returns no date, and
    the caller falls back to the discovery month, which is honest.
    """
    if not DATE_ONLY_CELL.match(cell or ""):
        return None, DatePrecision.DAY, DateConfidence.INFERRED
    return parse_date(cell)


def parse_date(text: str) -> tuple[date | None, DatePrecision, DateConfidence]:
    """Read the most precise date present, reporting how precise it is.

    Tried most precise first, because a string containing "July 15, 2026" also
    contains "July 2026" and matching the looser pattern first would silently
    discard the day.

    Returns `(None, DAY, INFERRED)` when nothing parseable is found; the caller
    then falls back to the discovery date (ADR-0005).
    """
    match = ISO_DATE.search(text)
    if match:
        return (
            date(int(match["year"]), int(match["month"]), int(match["day"])),
            DatePrecision.DAY,
            DateConfidence.EXACT,
        )

    match = MONTH_DAY_YEAR.search(text)
    if match:
        return (
            date(int(match["year"]), MONTHS[match["month"].lower()], int(match["day"])),
            DatePrecision.DAY,
            DateConfidence.EXACT,
        )

    match = MONTH_YEAR.search(text)
    if match:
        # An exactly known month. `published_date` holds the first of it so
        # ordering stays deterministic; `date_precision` says how much to
        # believe (ADR-0017).
        return (
            date(int(match["year"]), MONTHS[match["month"].lower()], 1),
            DatePrecision.MONTH,
            DateConfidence.EXACT,
        )

    match = BARE_YEAR.search(text)
    if match:
        # A bare year is a weak signal -- it could be a copyright notice or a
        # product name. Exact to the year, but flagged as inferred.
        return date(int(match["year"]), 1, 1), DatePrecision.YEAR, DateConfidence.INFERRED

    return None, DatePrecision.DAY, DateConfidence.INFERRED


def slugify(title: str, max_length: int = 60) -> str:
    """A stable, filesystem-safe slug for an object directory name."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(cleaned) <= max_length:
        return cleaned or "untitled"
    return cleaned[:max_length].rsplit("-", 1)[0] or cleaned[:max_length]
