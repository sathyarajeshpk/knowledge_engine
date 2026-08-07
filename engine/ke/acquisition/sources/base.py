"""The contract every discovery adapter obeys.

One rule governs this package:

    def discover(self) -> list[RawItem]: ...

That is the whole interface. Nothing downstream of discovery ever learns whether
an item came from an RSS feed, a table row or a commit message -- except through
`Provenance`, which is data rather than control flow. Dedupe, ID minting,
storage, classification and indexing stay completely source-agnostic, which is
what makes adding a source free (ADR-0018).

Fetching is injected too. Adapters receive a `Fetcher` rather than calling the
network themselves, so every adapter is testable offline with a recorded
document, and so a future replay can serve archived snapshots through the same
interface without the adapter noticing (ADR-0025).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ke.models import (
    AdapterType,
    RawItem,
    SourceAuthority,
    SourceRole,
    SourceStatus,
)

DEFAULT_TIMEOUT = 30
USER_AGENT = (
    "knowledge-engine/0.1 (+https://github.com/sathyarajeshpk/knowledge_engine)"
)


class SourceError(Exception):
    """A source could not be fetched or parsed.

    Always caught by the orchestrator: a failing source is recorded and the run
    continues (ADR-0019). It never propagates far enough to end a run.
    """


@dataclass(frozen=True)
class FetchResult:
    body: str
    status: int
    elapsed_ms: int
    final_url: str


#: The only schemes a source may name. `urlopen` also speaks `file:`, `ftp:` and
#: `data:`, and a `pack.yml` is **data reviewed as data** (ADR-0016) -- nobody
#: reads a 29-rule pack file expecting one line of it to be a local-file read.
#:
#: `url: file:///etc/passwd` in a source entry made `HttpFetcher` return the
#: file's contents, which the pipeline then stored as a knowledge object and the
#: weekly workflow committed and pushed. Verified before this guard existed.
#: An allowlist is the right shape here rather than a denylist: the set of
#: schemes this engine legitimately needs is two, and it will stay two.
WEB_SCHEMES = ("http://", "https://")


def require_web_url(url: object, source_name: str) -> str:
    """Return `url` if a source may legitimately fetch it, else refuse.

    Checked when the pack is **loaded**, not when the fetch happens, so a
    malformed source fails on `ke validate` in CI -- on the pull request that
    introduces it -- rather than silently at 03:00 on Sunday inside a process
    holding a repository write token.
    """
    text = str(url or "")
    if not text.lower().startswith(WEB_SCHEMES):
        raise ValueError(
            f"source {source_name!r} declares url {text!r}. Only "
            f"{' and '.join(s.rstrip(':/') for s in WEB_SCHEMES)} are allowed: "
            "urlopen also speaks file:, ftp: and data:, so any other scheme "
            "would let a pack definition read the runner's filesystem into "
            "stored knowledge."
        )
    return text


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


class HttpFetcher:
    """Real HTTP, via the standard library.

    Deliberately not `requests`: `urllib` does everything needed here, and every
    dependency is a permanent cost in CI time and supply-chain surface.
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT, user_agent: str = USER_AGENT) -> None:
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch(self, url: str) -> FetchResult:
        # Checked again here, not only at pack load. This is the object that
        # actually opens the URL, and it is reachable from adapters, tests and
        # any future caller that did not come through `SourceDefinition`. A
        # guard that lives only at the configuration boundary protects the
        # configuration boundary.
        require_web_url(url, "<direct fetch>")
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept": "*/*"})
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                return FetchResult(
                    body=body,
                    status=response.status,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    final_url=response.geturl(),
                )
        except HTTPError as exc:
            raise SourceError(f"HTTP {exc.code} {exc.reason}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SourceError(f"{type(exc).__name__}: {exc}") from exc


@dataclass(frozen=True)
class SourceDefinition:
    """One configured source, as declared in `pack.yml`.

    **Immutable and permanent.** A source is never deleted from configuration,
    because provenance on every object it ever produced points at it; removing
    the definition would make historical knowledge inexplicable. Retirement is
    expressed by moving `status`, never by deletion (ADR-0024).

    `parser_version` is declared here rather than hard-coded in the adapter, so
    changing an extraction strategy is a visible configuration change and
    historical items remain attributable to the parser that produced them.
    """

    name: str
    adapter: AdapterType
    url: str
    authority: SourceAuthority
    parser_version: int = 1
    status: SourceStatus = SourceStatus.ACTIVE
    role: SourceRole = SourceRole.PRIMARY
    #: Ordered fallback chain. Tried in sequence when this source fails.
    fallbacks: tuple[SourceDefinition, ...] = ()
    #: Adapter-specific settings, kept as data so tuning needs no code change.
    options: dict[str, Any] = field(default_factory=dict)
    #: Name of the source that superseded this one, when `status` is `replaced`.
    replaced_by: str | None = None
    notes: str = ""

    @property
    def is_pollable(self) -> bool:
        """Whether the weekly run should ask this source for anything.

        `disabled` and `replaced` sources keep their definitions forever so
        provenance stays explicable, but are not polled.
        """
        return self.status in (SourceStatus.ACTIVE, SourceStatus.DEPRECATED)

    @classmethod
    def from_config(cls, raw: dict[str, Any]) -> SourceDefinition:
        """Build from a `pack.yml` entry.

        The `adapter:` block carries both name and version::

            adapter:
              name: html
              version: 1
        """
        adapter = raw.get("adapter") or {}
        if isinstance(adapter, str):  # tolerate the shorthand form
            adapter = {"name": adapter}
        return cls(
            name=raw["name"],
            adapter=AdapterType(adapter["name"]),
            url=require_web_url(raw["url"], raw.get("name", "<unnamed>")),
            authority=SourceAuthority(raw["authority"]),
            parser_version=int(adapter.get("version", 1)),
            status=SourceStatus(raw.get("status", SourceStatus.ACTIVE)),
            role=SourceRole(raw.get("role", SourceRole.PRIMARY)),
            fallbacks=tuple(
                cls.from_config(entry) for entry in (raw.get("fallback") or ())
            ),
            options=dict(raw.get("options") or {}),
            replaced_by=raw.get("replaced_by"),
            notes=raw.get("notes", ""),
        )


class Source(Protocol):
    """Every discovery adapter. The entire contract."""

    definition: SourceDefinition

    def discover(self) -> list[RawItem]: ...


def sort_items(items: list[RawItem]) -> list[RawItem]:
    """Deterministic ordering for adapter output.

    Same inputs must always produce byte-identical output (ADR-0022), so every
    adapter sorts before returning. Sorted by publication date then identity
    key: the date is what a human expects, and the key breaks ties without ever
    depending on the order the source happened to present things in.

    Items with no publication date sort last, since `None` cannot be compared
    with a date and "undated" is genuinely least certain.
    """
    return sorted(
        items,
        key=lambda item: (
            item.published_date is None,
            item.published_date or date_min(),
            item.identity.key,
        ),
    )


def date_min():
    from datetime import date

    return date.min
