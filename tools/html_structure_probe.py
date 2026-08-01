"""Decide whether a candidate HTML page can be parsed at all.

**Diagnostic, not an adapter.** It answers one question before any adapter is
written: *is this page server-rendered with dated, structured entries, or is it
a JavaScript application that ships an empty shell?*

The roadmap page is the cautionary case — 196 KB of HTML that a browser turns
into a rich page and a fetcher turns into nothing. Building an adapter against
that would produce a pipeline that silently discovers zero items.

For each page it reports:

1. **Render mode** - how much of the byte count is actual visible text, and
   whether the markup looks like an app shell (few headings, many scripts, low
   text ratio).
2. **Structure** - headings, tables, lists, links: the anchors an extractor
   would key off.
3. **Dates** - ADR-0005 mints Feature IDs from the publication month, so a page
   with no parseable dates is unusable however pretty its markup.
4. **A sample extraction** - what a naive heading+date walker would actually
   pull out, so the report shows real candidate entries rather than a promise.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TIMEOUT = 30
BROWSER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PAGES = [
    {"name": "learn-fabric-whats-new",
     "url": "https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new"},
    {"name": "learn-powerbi-whats-new",
     "url": "https://learn.microsoft.com/en-us/power-bi/fundamentals/desktop-latest-update"},
    {"name": "learn-fabric-known-issues",
     "url": "https://learn.microsoft.com/en-us/fabric/known-issues/"},
    # Control: a page already believed to be a JavaScript app. If the probe
    # cannot tell this apart from the pages above, the probe is wrong.
    {"name": "fabric-roadmap-CONTROL",
     "url": "https://roadmap.fabric.microsoft.com/"},
]

#: Month-name and ISO date forms Microsoft Learn actually uses.
DATE_PATTERNS = [
    re.compile(r"\b(January|February|March|April|May|June|July|August|September|"
               r"October|November|December)\s+\d{4}\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
]


class StructureParser(HTMLParser):
    """Collect the anchors an extractor would key off, plus visible text."""

    SKIP_TEXT_IN = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.headings: dict[str, list[str]] = {"h1": [], "h2": [], "h3": [], "h4": []}
        self.counts: dict[str, int] = {}
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []
        self._stack: list[str] = []
        self._capture_heading: str | None = None
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        self._stack.append(tag)
        self.counts[tag] = self.counts.get(tag, 0) + 1
        if tag in self.headings:
            self._capture_heading = tag
            self._heading_buf = []
        if tag == "meta":
            attributes = dict(attrs)
            key = attributes.get("name") or attributes.get("property")
            if key and attributes.get("content"):
                self.meta[key] = attributes["content"]

    def handle_endtag(self, tag):
        if self._stack and tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass
        if tag == self._capture_heading:
            text = " ".join("".join(self._heading_buf).split())
            if text:
                self.headings[tag].append(text)
            self._capture_heading = None

    def handle_data(self, data):
        if self._capture_heading:
            self._heading_buf.append(data)
        if not any(t in self.SKIP_TEXT_IN for t in self._stack):
            stripped = data.strip()
            if stripped:
                self.text_parts.append(stripped)

    @property
    def visible_text(self) -> str:
        return " ".join(self.text_parts)


@dataclass
class PageResult:
    name: str
    url: str
    ok: bool = False
    status: int | None = None
    error: str | None = None
    total_bytes: int = 0
    visible_text_chars: int = 0
    text_ratio: float = 0.0
    script_tags: int = 0
    headings: dict[str, int] = field(default_factory=dict)
    tables: int = 0
    list_items: int = 0
    links: int = 0
    date_matches: int = 0
    sample_dates: list[str] = field(default_factory=list)
    ms_date: str | None = None
    sample_h2: list[str] = field(default_factory=list)
    sample_entries: list[str] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if not self.ok:
            return "UNREACHABLE"
        # A JavaScript shell ships plenty of bytes and almost no readable text.
        if self.text_ratio < 0.02 or self.visible_text_chars < 2000:
            return "JS APP SHELL — not parseable"
        if self.date_matches == 0:
            return "server-rendered but UNDATED — unusable for IDs"
        if sum(self.headings.values()) < 5:
            return "server-rendered, dated, but FLAT — weak structure"
        return "SERVER-RENDERED, DATED, STRUCTURED — parseable"


def probe(page: dict[str, str]) -> PageResult:
    result = PageResult(name=page["name"], url=page["url"])
    request = Request(page["url"], headers={"User-Agent": BROWSER_AGENT, "Accept": "text/html"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            result.status = response.status
            result.ok = 200 <= response.status < 300
    except HTTPError as exc:
        result.status, result.error = exc.code, f"HTTP {exc.code} {exc.reason}"
        return result
    except (URLError, TimeoutError, OSError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    html = body.decode("utf-8", errors="replace")
    result.total_bytes = len(body)

    parser = StructureParser()
    try:
        parser.feed(html)
    except Exception as exc:  # noqa: BLE001 - malformed markup is data, not a crash
        result.error = f"parse warning: {type(exc).__name__}: {exc}"

    text = parser.visible_text
    result.visible_text_chars = len(text)
    result.text_ratio = round(len(text) / max(len(html), 1), 4)
    result.script_tags = parser.counts.get("script", 0)
    result.headings = {k: len(v) for k, v in parser.headings.items()}
    result.tables = parser.counts.get("table", 0)
    result.list_items = parser.counts.get("li", 0)
    result.links = parser.counts.get("a", 0)
    result.ms_date = parser.meta.get("ms.date") or parser.meta.get("updated_at")
    result.sample_h2 = parser.headings["h2"][:8]

    found: list[str] = []
    for pattern in DATE_PATTERNS:
        found.extend(pattern.findall(text) if pattern.groups == 0 else
                     [m.group(0) for m in pattern.finditer(text)])
    result.date_matches = len(found)
    result.sample_dates = list(dict.fromkeys(found))[:8]

    # What would a naive "H2 that looks like a month" walker actually yield?
    month_heading = DATE_PATTERNS[0]
    for heading in parser.headings["h2"] + parser.headings["h3"]:
        if month_heading.search(heading):
            result.sample_entries.append(heading)
    result.sample_entries = result.sample_entries[:10]
    return result


def render(results: list[PageResult]) -> str:
    lines = ["## HTML structure validation\n"]
    lines.append("| Page | Status | Bytes | Text | Ratio | Scripts | H2/H3 | Tables | Dates | Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        h = f"{r.headings.get('h2', 0)}/{r.headings.get('h3', 0)}"
        lines.append(
            f"| `{r.name}` | {r.status or '—'} | {r.total_bytes:,} | "
            f"{r.visible_text_chars:,} | {r.text_ratio} | {r.script_tags} | {h} | "
            f"{r.tables} | {r.date_matches} | **{r.verdict}** |"
        )

    lines.append("\n### Detail\n")
    for r in results:
        lines.append(f"**`{r.name}`** — {r.url}")
        if r.error:
            lines.append(f"- {r.error}")
        if r.ok:
            lines.append(f"- visible text {r.visible_text_chars:,} chars of "
                         f"{r.total_bytes:,} bytes (ratio {r.text_ratio})")
            lines.append(f"- {r.script_tags} script tags · {r.links} links · "
                         f"{r.list_items} list items · {r.tables} tables")
            if r.ms_date:
                lines.append(f"- `ms.date` metadata: {r.ms_date}")
            if r.sample_dates:
                lines.append(f"- dates seen: {', '.join(r.sample_dates)}")
            if r.sample_h2:
                lines.append("- sample H2 headings:")
                for h in r.sample_h2:
                    lines.append(f"  - {h}")
            if r.sample_entries:
                lines.append("- **month-like headings a walker would extract:**")
                for e in r.sample_entries:
                    lines.append(f"  - {e}")
            else:
                lines.append("- no month-like headings found")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    results = [probe(page) for page in PAGES]
    report = render(results)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)
    with open("html-structure-results.json", "w", encoding="utf-8") as handle:
        json.dump([asdict(r) for r in results], handle, indent=2)

    parseable = sum(1 for r in results if r.verdict.startswith("SERVER-RENDERED"))
    print(f"\n{parseable}/{len(results)} pages are server-rendered, dated and structured",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
