"""Is Microsoft Learn a *reliable* source, and is there a better one?

Discovery already proved Learn is reachable from a GitHub runner and that the
HTML adapter extracts real items from it. This asks the harder questions:

* Is access conditional on headers we might not always send?
* Are we being served a CDN challenge under any condition?
* Does robots.txt permit what we are doing?
* Is there an **official, structured** source carrying the same content?

That last one matters most. `learn.microsoft.com/fabric` is generated from the
public `MicrosoftDocs/fabric-docs` repository. If the source Markdown is
fetchable, it is strictly better than parsing rendered HTML: no CDN, no
JavaScript, no markup churn, and Git history supplies the Time Machine for
free.

Diagnostic only. Nothing here is part of the engine.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

TIMEOUT = 30
LEARN_PAGE = "https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new"
PBI_PAGE = "https://learn.microsoft.com/en-us/power-bi/fundamentals/desktop-latest-update"

BOT_UA = "knowledge-engine/0.1 (+https://github.com/sathyarajeshpk/knowledge_engine)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Header combinations, weakest to strongest. If only the richest one works, the
#: adapter is one header change away from breaking.
HEADER_SETS: list[tuple[str, dict[str, str]]] = [
    ("no-headers", {}),
    ("bot-ua-only", {"User-Agent": BOT_UA}),
    ("bot-ua-accept", {"User-Agent": BOT_UA, "Accept": "text/html,*/*"}),
    ("browser-ua-only", {"User-Agent": BROWSER_UA}),
    (
        "browser-full",
        {
            "User-Agent": BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    ),
]

#: Markers that mean "you got a challenge page, not the content". A 200 with one
#: of these is worse than a 403: it looks like success.
CHALLENGE_MARKERS = [
    "just a moment", "checking your browser", "cf-browser-verification",
    "_incapsula_", "access denied", "akamai", "captcha", "bot detection",
    "enable javascript and cookies", "attention required",
]

#: Structured alternatives to parsing rendered HTML, best first.
ALTERNATIVES = [
    {
        "name": "fabric-docs-raw-markdown",
        "url": "https://raw.githubusercontent.com/MicrosoftDocs/fabric-docs/main/docs/fundamentals/whats-new.md",
        "why": "Source Markdown behind the Learn page. No CDN, no JS, git history included.",
    },
    {
        "name": "fabric-docs-contents-api",
        "url": "https://api.github.com/repos/MicrosoftDocs/fabric-docs/contents/docs/fundamentals/whats-new.md",
        "why": "Same file via the GitHub API; carries a content SHA for change detection.",
    },
    {
        "name": "powerbi-docs-raw-markdown",
        "url": "https://raw.githubusercontent.com/MicrosoftDocs/powerbi-docs/main/powerbi-docs/fundamentals/desktop-latest-update.md",
        "why": "Source Markdown behind the Power BI updates page.",
    },
    {
        "name": "fabric-docs-whatsnew-history",
        "url": "https://api.github.com/repos/MicrosoftDocs/fabric-docs/commits?path=docs/fundamentals/whats-new.md&per_page=5",
        "why": "Commit history for that one file: a ready-made change feed.",
    },
    {
        "name": "learn-sitemap",
        "url": "https://learn.microsoft.com/sitemap.xml",
        "why": "Official sitemap; would give lastmod dates without parsing pages.",
    },
    {
        "name": "learn-fabric-toc",
        "url": "https://learn.microsoft.com/en-us/fabric/toc.json",
        "why": "Structured table of contents, if published.",
    },
]


@dataclass
class Probe:
    label: str
    url: str
    status: int | None = None
    error: str | None = None
    final_url: str | None = None
    redirected: bool = False
    content_type: str | None = None
    size: int = 0
    elapsed_ms: int = 0
    server: str | None = None
    cache_headers: dict[str, str] = field(default_factory=dict)
    set_cookie: bool = False
    challenge_markers: list[str] = field(default_factory=list)
    body_head: str = ""

    @property
    def looks_like_challenge(self) -> bool:
        return bool(self.challenge_markers)

    @property
    def verdict(self) -> str:
        if self.error:
            return f"FAILED ({self.error})"
        if self.looks_like_challenge:
            return "CHALLENGE PAGE — 200 but not content"
        if self.status and 200 <= self.status < 300:
            return "ok"
        return f"HTTP {self.status}"


def probe(url: str, headers: dict[str, str], label: str) -> Probe:
    result = Probe(label=label, url=url)
    started = time.monotonic()
    try:
        with urlopen(Request(url, headers=headers), timeout=TIMEOUT) as response:
            body = response.read()
            result.status = response.status
            result.final_url = response.geturl()
            result.redirected = response.geturl() != url
            result.content_type = response.headers.get("Content-Type")
            result.size = len(body)
            result.server = response.headers.get("Server")
            result.set_cookie = bool(response.headers.get("Set-Cookie"))
            for header in ("Cache-Control", "X-Cache", "Age", "X-Served-By", "Via"):
                value = response.headers.get(header)
                if value:
                    result.cache_headers[header] = value[:80]
            text = body.decode("utf-8", errors="replace")
            lowered = text[:6000].lower()
            result.challenge_markers = [m for m in CHALLENGE_MARKERS if m in lowered]
            result.body_head = " ".join(text[:200].split())
    except HTTPError as exc:
        result.status = exc.code
        result.error = f"HTTP {exc.code} {exc.reason}"
        result.server = exc.headers.get("Server") if exc.headers else None
    except (URLError, TimeoutError, OSError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    return result


def check_robots(path: str) -> list[str]:
    """Report whether robots.txt permits the path we actually fetch."""
    lines = ["### robots.txt\n"]
    result = probe("https://learn.microsoft.com/robots.txt", {"User-Agent": BOT_UA}, "robots")
    if result.error:
        lines.append(f"- could not fetch: {result.error}")
        return lines

    body = result.body_head
    disallows: list[str] = []
    try:
        with urlopen(
            Request("https://learn.microsoft.com/robots.txt", headers={"User-Agent": BOT_UA}),
            timeout=TIMEOUT,
        ) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    applies = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("user-agent:"):
            applies = stripped.split(":", 1)[1].strip() in ("*",)
        elif applies and stripped.lower().startswith("disallow:"):
            rule = stripped.split(":", 1)[1].strip()
            if rule:
                disallows.append(rule)

    blocked = [rule for rule in disallows if rule != "/" and path.startswith(rule)]
    lines.append(f"- fetched, {len(body)} bytes, {len(disallows)} Disallow rules for `*`")
    lines.append(f"- rules matching `{path}`: {blocked or 'none'}")
    lines.append(
        "- **verdict:** "
        + ("path is disallowed" if blocked else "path is not disallowed for `*`")
    )
    return lines


def main() -> int:
    out: list[str] = ["## Learn access diagnostic\n"]
    out.append(f"Environment: `{os.environ.get('GITHUB_ACTIONS') and 'github-actions' or 'local'}`\n")

    # 1-2, 4, 7: headers, redirects, challenges, full response detail
    out.append("### Header sensitivity\n")
    out.append("| Headers | Status | Size | Type | Server | Redirect | Verdict |")
    out.append("|---|---|---|---|---|---|---|")
    header_probes = []
    for label, headers in HEADER_SETS:
        result = probe(LEARN_PAGE, headers, label)
        header_probes.append(result)
        out.append(
            f"| `{label}` | {result.status or '—'} | {result.size:,} | "
            f"{(result.content_type or '—')[:24]} | {(result.server or '—')[:18]} | "
            f"{'yes' if result.redirected else 'no'} | **{result.verdict}** |"
        )

    working = [p for p in header_probes if p.verdict == "ok"]
    out.append("")
    if len(working) == len(header_probes):
        out.append("**No header sensitivity.** Every combination works, including no headers "
                   "at all — access does not depend on impersonating a browser.")
    elif working:
        out.append(f"**Header-sensitive.** Working: {[p.label for p in working]}. "
                   "The adapter must keep sending whatever these have in common.")
    else:
        out.append("**Learn is unreachable from here under every header combination.**")

    if header_probes[0].cache_headers:
        out.append(f"\nCache/CDN headers observed: `{header_probes[-1].cache_headers}`")
    out.append(f"Set-Cookie present: {header_probes[-1].set_cookie}")
    out.append(f"Final URL: `{header_probes[-1].final_url}`")

    # 3: robots.txt
    out.append("")
    out.extend(check_robots("/en-us/fabric/fundamentals/whats-new"))

    # 6: the Power BI page too, since its extraction quality differs
    out.append("\n### Power BI page\n")
    pbi = probe(PBI_PAGE, dict(HEADER_SETS[-1][1]), "browser-full")
    out.append(f"- {pbi.verdict} · {pbi.size:,} bytes · final `{pbi.final_url}`")

    # The important part: is there something better than parsing HTML?
    out.append("\n### Structured alternatives\n")
    out.append("| Source | Status | Size | Type | Verdict |")
    out.append("|---|---|---|---|---|")
    alt_results = []
    for alt in ALTERNATIVES:
        result = probe(alt["url"], {"User-Agent": BOT_UA, "Accept": "*/*"}, alt["name"])
        alt_results.append((alt, result))
        out.append(
            f"| `{alt['name']}` | {result.status or '—'} | {result.size:,} | "
            f"{(result.content_type or '—')[:28]} | **{result.verdict}** |"
        )

    out.append("\n#### Detail\n")
    for alt, result in alt_results:
        out.append(f"**`{alt['name']}`**")
        out.append(f"- {alt['why']}")
        out.append(f"- {result.verdict} · {result.size:,} bytes · {result.elapsed_ms} ms")
        if result.body_head and not result.error:
            out.append(f"- starts: `{result.body_head[:160]}`")
        out.append("")

    # If the raw Markdown is available, measure whether it is actually usable:
    # pipe tables and Month YYYY dates are what the adapter would key off.
    markdown = next(
        (r for a, r in alt_results if a["name"] == "fabric-docs-raw-markdown"), None
    )
    if markdown and markdown.verdict == "ok":
        try:
            with urlopen(
                Request(ALTERNATIVES[0]["url"], headers={"User-Agent": BOT_UA}), timeout=TIMEOUT
            ) as response:
                text = response.read().decode("utf-8", errors="replace")
            pipe_rows = [l for l in text.splitlines() if l.strip().startswith("|")]
            months = re.findall(
                r"\b(?:January|February|March|April|May|June|July|August|September|"
                r"October|November|December)\s+\d{4}\b",
                text,
            )
            headings = [l for l in text.splitlines() if l.startswith("## ")]
            out.append("#### Raw Markdown structure\n")
            out.append(f"- {len(text):,} characters")
            out.append(f"- {len(pipe_rows)} table rows, {len(headings)} `##` sections")
            out.append(
                f"- {len(months)} `Month YYYY` dates, distinct: "
                f"{sorted(set(months), reverse=True)[:6]}"
            )
            out.append(f"- sample sections: {[h[3:] for h in headings[:6]]}")
            out.append("")
        except Exception as exc:  # noqa: BLE001
            out.append(f"- could not analyse Markdown: {exc}\n")

    report = "\n".join(out)
    print(report)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(report)
    with open("access-diagnostic.json", "w", encoding="utf-8") as handle:
        json.dump(
            {
                "headers": [asdict(p) for p in header_probes],
                "alternatives": [asdict(r) for _, r in alt_results],
            },
            handle,
            indent=2,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
