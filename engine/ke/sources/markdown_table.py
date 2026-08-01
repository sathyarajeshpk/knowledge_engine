"""Adapter for the Markdown source behind a Microsoft Learn page.

`learn.microsoft.com/fabric` is generated from the public
`MicrosoftDocs/fabric-docs` repository. This adapter reads that source file
directly, which makes it a genuine **secondary** for the HTML primary: the same
authoritative content, from different infrastructure, with different failure
modes. The previous fallback -- a feed of docs-repo merge commits -- carried no
knowledge at all.

The trade that keeps this second rather than first (see
`docs/reviews/M1_SOURCE_VALIDATION.md`): Markdown links are **relative document
paths**::

    [Use Iceberg tables](../onelake/onelake-iceberg-tables.md#virtualize)

Identity's strongest signal is the canonical URL (ADR-0023), and the rendered
HTML supplies those already resolved. Here they must be reconstructed, which is
deterministic but is a new failure mode sitting directly on the mechanism that
prevents duplicate permanent Feature IDs. Reconstruction is therefore explicit,
configured per source, and recorded in provenance.
"""

from __future__ import annotations

import posixpath
import re

from ke.clock import Clock
from ke.identity import compute_identity
from ke.models import (
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    Provenance,
    RawItem,
    SourceRepresentation,
)
from ke.normalize import canonical_url, parse_date_cell, truncate_summary
from ke.sources.base import Fetcher, SourceDefinition, SourceError, sort_items

DEFAULT_MAX_SUMMARY_WORDS = 120

#: `[text](target)` — Markdown inline link.
MD_LINK = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<target>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
#: A table alignment row: `|:-- | --- |`. Never an update.
MD_SEPARATOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
#: `## Section heading`
MD_HEADING = re.compile(r"^(?P<hashes>#{2,4})\s+(?P<text>.+?)\s*$")
#: Bold/italic/code markers stripped from cell text.
MD_EMPHASIS = re.compile(r"(\*\*|__|\*|_|`)")


def strip_markdown(text: str) -> str:
    """Plain text from a Markdown fragment: links become their label."""
    without_links = MD_LINK.sub(lambda m: m["text"] or m["target"], text)
    return " ".join(MD_EMPHASIS.sub("", without_links).split())


def resolve_doc_link(
    target: str, *, doc_path: str, docs_prefix: str, base_url: str
) -> str | None:
    """Turn a relative `.md` link into the Learn URL it renders as.

    ``../onelake/onelake-iceberg-tables.md#anchor`` from
    ``docs/fundamentals/whats-new.md`` becomes
    ``https://learn.microsoft.com/en-us/fabric/onelake/onelake-iceberg-tables``.

    Returns `None` for anything that is not a resolvable document link, so the
    caller falls back to a weaker identity basis rather than inventing a URL.
    Fabricating a canonical URL would be worse than having none: identity would
    look durable while resting on a guess.
    """
    if not target or target.startswith("#"):
        return None
    if target.startswith(("http://", "https://")):
        return canonical_url(target)
    if not target.split("#", 1)[0].endswith(".md"):
        return None

    path = posixpath.normpath(
        posixpath.join(posixpath.dirname(doc_path), target.split("#", 1)[0])
    )
    if docs_prefix and path.startswith(docs_prefix):
        path = path[len(docs_prefix):]
    if path.startswith("../"):
        # Escaped the docs root: we cannot say where this renders.
        return None
    return canonical_url(f"{base_url.rstrip('/')}/{path[:-3].lstrip('/')}")


class MarkdownTableSource:
    """Discovery adapter for a Markdown "What's New" document."""

    def __init__(
        self,
        definition: SourceDefinition,
        fetcher: Fetcher,
        clock: Clock,
        *,
        max_summary_words: int = DEFAULT_MAX_SUMMARY_WORDS,
    ) -> None:
        self.definition = definition
        self._fetcher = fetcher
        self._clock = clock
        self._max_summary_words = max_summary_words
        options = definition.options
        # Configured per source rather than hard-coded: a second pack pointing at
        # a different docs repository must need no code change.
        self._doc_path = options.get("doc_path", "")
        self._docs_prefix = options.get("docs_prefix", "")
        self._base_url = options.get("rendered_base_url", "")

    def discover(self) -> list[RawItem]:
        result = self._fetcher.fetch(self.definition.url)
        text = result.body

        if not self._base_url:
            raise SourceError(
                "markdown source needs `rendered_base_url` to reconstruct "
                "canonical URLs; without it identity would rest on a guess"
            )

        items: list[RawItem] = []
        section = ""
        for line in text.splitlines():
            heading = MD_HEADING.match(line)
            if heading:
                candidate = strip_markdown(heading["text"])
                if candidate and candidate.lower() != "in this article":
                    section = candidate
                continue

            stripped = line.strip()
            if not stripped.startswith("|") or MD_SEPARATOR.match(stripped):
                continue

            item = self._row_to_item(stripped, section)
            if item is not None:
                items.append(item)

        if not items:
            # Same rule as the HTML adapter: an empty result from a document
            # that should be full of rows is a parser break, not a quiet week.
            raise SourceError(
                "no table rows found in the Markdown; the document structure "
                "has probably changed"
            )
        return sort_items(items)

    def _row_to_item(self, line: str, section: str) -> RawItem | None:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not any(cells):
            return None

        # Only a dedicated date cell is trusted -- identical rule to the HTML
        # adapter, and for the identical reason: a month mentioned in prose must
        # never become a permanent Feature ID.
        published = None
        precision, confidence = DatePrecision.DAY, DateConfidence.INFERRED
        date_cell_index = None
        for index, cell in enumerate(cells):
            parsed, cell_precision, cell_confidence = parse_date_cell(strip_markdown(cell))
            if parsed is not None:
                published, precision, confidence = parsed, cell_precision, cell_confidence
                date_cell_index = index
                break

        content_cells = [
            cell for index, cell in enumerate(cells)
            if cell and index != date_cell_index
        ]
        if not content_cells:
            return None

        title = strip_markdown(content_cells[0])
        if len(title.split()) < 2 or title.lower() in ("feature", "learn more"):
            return None  # header row

        target = None
        for cell in content_cells:
            for match in MD_LINK.finditer(cell):
                resolved = resolve_doc_link(
                    match["target"],
                    doc_path=self._doc_path,
                    docs_prefix=self._docs_prefix,
                    base_url=self._base_url,
                )
                if resolved:
                    target = resolved
                    break
            if target:
                break

        summary = truncate_summary(
            strip_markdown(" ".join(content_cells[1:])) or title, self._max_summary_words
        )
        identity = compute_identity(
            canonical_url=target, title=title, summary=summary
        )

        return RawItem(
            source_name=self.definition.name,
            source_url=target or self.definition.url,
            # `target` is None when `resolve_doc_link` refused to guess. That is
            # a feature with no resolvable announcement, and it must stay
            # distinguishable from one that has a real citation (ADR-0027).
            announcement_url=target,
            source_authority=self.definition.authority,
            title=title,
            summary=summary,
            discovered_date=self._clock.today(),
            published_date=published,
            date_confidence=confidence,
            date_precision=precision,
            raw_tags=(section,) if section else (),
            identity=identity,
            provenance=Provenance(
                source_name=self.definition.name,
                source_representation=SourceRepresentation.MARKDOWN,
                adapter_type=AdapterType.MARKDOWN,
                parser_version=self.definition.parser_version,
                extraction_method=ExtractionMethod.MARKDOWN_TABLE_ROW,
                identity_basis=identity.basis,
                identity_key=identity.key,
                discovered_at=self._clock.now(),
                selector=(
                    f"## {section} > table row"
                    + (f" (date from cell {date_cell_index})" if date_cell_index is not None
                       else " (no date cell)")
                ),
                run_id=self._clock.run_id(),
                source_role=self.definition.role,
            ),
        )
