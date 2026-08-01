"""Probe candidate knowledge sources and report what they actually return.

**This is a diagnostic, not part of the engine.** It lives in `tools/` rather
than `engine/ke/` on purpose: it is throwaway-grade code whose only job is to
answer "is this URL real, and is it usable?" so that M1's source list can be
pinned to verified endpoints instead of plausible-looking guesses.

It runs on a GitHub Actions runner because the development environment blocks
`*.microsoft.com`.

For each candidate it reports the four things that decide whether a source is
usable at all:

1. **Reachability** - status, redirects, content type, size.
2. **Parseability** - does it parse as a feed, and how many entries?
3. **Date availability** - how many entries carry a publication date.
   ADR-0005 mints Feature IDs from the publication month, so a feed without
   dates silently degrades every ID to `inferred`. This is the check that
   matters most and the one a naive "does the URL 200?" probe would miss.
4. **Content volume** - summaries or full articles. ADR-0003 forbids storing
   full third-party article text, so a full-content feed needs truncation at
   ingest rather than being stored as delivered.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import feedparser
except ImportError:  # pragma: no cover - the workflow installs it
    feedparser = None

TIMEOUT = 30
USER_AGENT = "knowledge-engine-source-probe/0.1 (+https://github.com/sathyarajeshpk/knowledge_engine)"

#: Microsoft fronts several properties with a CDN that rejects non-browser
#: agents. A 403 therefore does not prove a URL is dead -- it may prove only
#: that we introduced ourselves honestly. Every failure is retried with a
#: browser agent so the report can distinguish "gone" from "blocked".
BROWSER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Candidate endpoints. Nothing here is trusted until this probe says so.
#: `kind` records what we hope it is; the probe reports what it actually is.
CANDIDATES: list[dict[str, str]] = [
    # --- Microsoft Fabric blog -------------------------------------------
    {"name": "fabric-blog-rss", "kind": "feed", "topic": "fabric",
     "url": "https://blog.fabric.microsoft.com/en-US/blog/feed/"},
    {"name": "fabric-blog-rss-lower", "kind": "feed", "topic": "fabric",
     "url": "https://blog.fabric.microsoft.com/en-us/blog/feed/"},
    {"name": "fabric-blog-root-feed", "kind": "feed", "topic": "fabric",
     "url": "https://blog.fabric.microsoft.com/feed/"},
    {"name": "fabric-microsoft-com-feed", "kind": "feed", "topic": "fabric",
     "url": "https://www.microsoft.com/en-us/microsoft-fabric/blog/feed/"},

    # --- Power BI blog ----------------------------------------------------
    {"name": "powerbi-blog-rss", "kind": "feed", "topic": "power-bi",
     "url": "https://powerbi.microsoft.com/en-us/blog/feed/"},
    {"name": "powerbi-blog-root-feed", "kind": "feed", "topic": "power-bi",
     "url": "https://powerbi.microsoft.com/blog/feed/"},
    {"name": "power-platform-pbi-feed", "kind": "feed", "topic": "power-bi",
     "url": "https://www.microsoft.com/en-us/power-platform/blog/power-bi/feed/"},

    # --- Microsoft Learn --------------------------------------------------
    {"name": "learn-fabric-whats-new", "kind": "html", "topic": "fabric",
     "url": "https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new"},
    {"name": "learn-search-rss-fabric", "kind": "feed", "topic": "fabric",
     "url": "https://learn.microsoft.com/api/search/rss?search=fabric&locale=en-us"},
    {"name": "learn-fabric-release-plan", "kind": "html", "topic": "fabric",
     "url": "https://learn.microsoft.com/en-us/fabric/release-plan/"},

    # --- Documentation repositories (public GitHub, structured and dated) -
    {"name": "fabric-docs-commits-atom", "kind": "feed", "topic": "fabric",
     "url": "https://github.com/MicrosoftDocs/fabric-docs/commits/main.atom"},
    {"name": "powerbi-docs-commits-atom", "kind": "feed", "topic": "power-bi",
     "url": "https://github.com/MicrosoftDocs/powerbi-docs/commits/main.atom"},
    {"name": "fabric-docs-api", "kind": "json", "topic": "fabric",
     "url": "https://api.github.com/repos/MicrosoftDocs/fabric-docs/commits?per_page=5"},

    # --- Community --------------------------------------------------------
    {"name": "fabric-community-updates", "kind": "feed", "topic": "fabric",
     "url": "https://community.fabric.microsoft.com/t5/s/gxcuf89792/rss/board?board.id=fbc_pbiupdatesblog"},

    # --- Azure release communications ------------------------------------
    {"name": "azure-updates-rss", "kind": "feed", "topic": "azure",
     "url": "https://www.microsoft.com/releasecommunications/api/v2/azure/rss"},

    # --- Round 2: no working Power BI blog feed was found in round 1, and the
    #     release-plan URL redirected to a roadmap host worth probing directly.
    {"name": "pbi-microsoft-com-feed", "kind": "feed", "topic": "power-bi",
     "url": "https://www.microsoft.com/en-us/power-bi/blog/feed/"},
    {"name": "pbi-blog-feed-noslash", "kind": "feed", "topic": "power-bi",
     "url": "https://powerbi.microsoft.com/en-us/blog/feed"},
    {"name": "learn-search-rss-powerbi", "kind": "feed", "topic": "power-bi",
     "url": "https://learn.microsoft.com/api/search/rss?search=power+bi&locale=en-us"},
    {"name": "learn-search-rss-fabric-whatsnew", "kind": "feed", "topic": "fabric",
     "url": "https://learn.microsoft.com/api/search/rss?search=fabric+what%27s+new&locale=en-us"},
    {"name": "fabric-roadmap-host", "kind": "html", "topic": "fabric",
     "url": "https://roadmap.fabric.microsoft.com/"},
    {"name": "fabric-roadmap-feed", "kind": "feed", "topic": "fabric",
     "url": "https://roadmap.fabric.microsoft.com/feed"},
    {"name": "powerbi-docs-api", "kind": "json", "topic": "power-bi",
     "url": "https://api.github.com/repos/MicrosoftDocs/powerbi-docs/commits?per_page=5"},
]


@dataclass
class ProbeResult:
    name: str
    url: str
    kind: str
    topic: str
    ok: bool = False
    status: int | None = None
    final_url: str | None = None
    content_type: str | None = None
    bytes: int = 0
    elapsed_ms: int = 0
    error: str | None = None

    # Feed analysis
    parsed_as: str | None = None
    entries: int = 0
    entries_with_dates: int = 0
    newest_entry: str | None = None
    oldest_entry: str | None = None
    max_content_chars: int = 0
    has_full_content: bool = False
    needs_browser_agent: bool = False
    sample_titles: list[str] = field(default_factory=list)

    @property
    def date_coverage(self) -> str:
        if not self.entries:
            return "n/a"
        return f"{self.entries_with_dates}/{self.entries}"

    @property
    def usable(self) -> bool:
        """Reachable, parseable, and dated well enough to mint IDs from."""
        if not self.ok or not self.entries:
            return False
        return self.entries_with_dates == self.entries


def fetch(url: str, agent: str = USER_AGENT) -> tuple[bytes, ProbeResult]:
    result = ProbeResult(name="", url=url, kind="", topic="")
    started = time.monotonic()
    request = Request(url, headers={"User-Agent": agent, "Accept": "*/*"})
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            result.status = response.status
            result.final_url = response.geturl()
            result.content_type = response.headers.get("Content-Type", "")
            result.bytes = len(body)
            result.ok = 200 <= response.status < 300
    except HTTPError as exc:
        result.status = exc.code
        result.error = f"HTTP {exc.code} {exc.reason}"
        body = b""
    except (URLError, TimeoutError, OSError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        body = b""
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return body, result


def analyse_feed(body: bytes, result: ProbeResult) -> None:
    """Populate the feed fields. Silent no-op when the body is not a feed."""
    if feedparser is None or not body:
        return
    parsed = feedparser.parse(body)
    if parsed.get("bozo") and not parsed.entries:
        result.parsed_as = f"not a feed ({type(parsed.get('bozo_exception')).__name__})"
        return

    result.parsed_as = parsed.get("version") or "unknown"
    result.entries = len(parsed.entries)

    dates: list[datetime] = []
    for entry in parsed.entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if stamp:
            result.entries_with_dates += 1
            dates.append(datetime(*stamp[:6], tzinfo=timezone.utc))

        # ADR-0003: how much text does this feed actually ship?
        longest = len(entry.get("summary", "") or "")
        for content in entry.get("content", []) or []:
            longest = max(longest, len(content.get("value", "") or ""))
        result.max_content_chars = max(result.max_content_chars, longest)

    if dates:
        result.newest_entry = max(dates).date().isoformat()
        result.oldest_entry = min(dates).date().isoformat()

    # Rough threshold: a summary feed runs to a few hundred characters; a
    # full-content feed runs to thousands.
    result.has_full_content = result.max_content_chars > 4000
    result.sample_titles = [
        (e.get("title") or "")[:90] for e in parsed.entries[:3]
    ]


def probe(candidate: dict[str, str]) -> ProbeResult:
    body, result = fetch(candidate["url"])
    if not result.ok:
        body, retry = fetch(candidate["url"], BROWSER_AGENT)
        if retry.ok:
            retry.needs_browser_agent = True
            result = retry
    result.name = candidate["name"]
    result.kind = candidate["kind"]
    result.topic = candidate["topic"]
    if result.ok and candidate["kind"] == "feed":
        analyse_feed(body, result)
    elif result.ok and candidate["kind"] == "json":
        try:
            payload = json.loads(body)
            result.parsed_as = "json"
            entries = payload if isinstance(payload, list) else [payload]
            result.entries = len(entries)
            result.entries_with_dates = sum(
                1 for c in entries
                if c.get("commit", {}).get("author", {}).get("date")
            )
            stamps = sorted(
                c["commit"]["author"]["date"][:10] for c in entries
                if c.get("commit", {}).get("author", {}).get("date")
            )
            if stamps:
                result.oldest_entry, result.newest_entry = stamps[0], stamps[-1]
            result.sample_titles = [
                (c.get("commit", {}).get("message") or "").splitlines()[0][:90]
                for c in entries[:3]
            ]
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            result.parsed_as = f"invalid json: {exc}"
    elif result.ok:
        result.parsed_as = "html"
    return result


def render(results: list[ProbeResult]) -> str:
    lines: list[str] = []
    lines.append("## Source endpoint validation\n")
    lines.append(f"Probed {len(results)} candidates at "
                 f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    lines.append("| Source | Topic | Status | Type | Entries | Dated | Newest | Max chars | Verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in results:
        status = str(r.status) if r.status else "—"
        verdict = "USABLE" if r.usable else ("reachable" if r.ok else "FAILED")
        lines.append(
            f"| `{r.name}` | {r.topic} | {status} | {r.parsed_as or '—'} | "
            f"{r.entries or '—'} | {r.date_coverage} | {r.newest_entry or '—'} | "
            f"{r.max_content_chars or '—'} | **{verdict}**"
            f"{' (browser UA)' if r.needs_browser_agent else ''} |"
        )

    lines.append("\n### Detail\n")
    for r in results:
        lines.append(f"**`{r.name}`** — {r.url}")
        if r.error:
            lines.append(f"- FAILED: {r.error}")
        else:
            lines.append(f"- HTTP {r.status} · {r.content_type} · {r.bytes:,} bytes · {r.elapsed_ms} ms")
            if r.final_url and r.final_url != r.url:
                lines.append(f"- redirected to: {r.final_url}")
            if r.entries:
                lines.append(f"- {r.entries} entries, {r.entries_with_dates} dated "
                             f"({r.oldest_entry} … {r.newest_entry})")
                lines.append(f"- longest entry body: {r.max_content_chars:,} chars"
                             f"{' — FULL CONTENT, must truncate on ingest (ADR-0003)' if r.has_full_content else ' — summary length, safe'}")
                for title in r.sample_titles:
                    lines.append(f"  - {title}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if feedparser is None:
        print("feedparser not installed; feed analysis will be skipped", file=sys.stderr)

    results = [probe(candidate) for candidate in CANDIDATES]
    report = render(results)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(report)

    with open("source-probe-results.json", "w", encoding="utf-8") as handle:
        json.dump([asdict(r) for r in results], handle, indent=2)

    usable = sum(1 for r in results if r.usable)
    print(f"\n{usable}/{len(results)} candidates are usable "
          f"(reachable, parseable, fully dated)")
    # Always exit 0: a failing candidate is information, not a build failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
