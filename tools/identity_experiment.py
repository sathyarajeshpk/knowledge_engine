"""Measure candidate identity schemes against production. **Analysis only.**

This implements nothing. It exists to answer one question with evidence rather
than argument, before ADR-0023 is amended:

> If a Feature ID were computed from (announcement URL + normalised feature
> title) instead of the announcement URL alone, would it be *stable*?

**Why this can be measured at all.** The same knowledge is published in two
representations — rendered HTML on Microsoft Learn and Markdown in the
`MicrosoftDocs` repository — and the two adapters title rows differently by
construction (HTML takes the first linked text, Markdown the first content
cell). That is a natural experiment: the same features, described by two
independent renderings, which is a fair proxy for "the same feature, reworded".

An identity scheme that disagrees with itself across two renderings of the same
document will also disagree with itself across two weeks of the same page — and
disagreement means duplicate permanent Feature IDs.

Reported per scheme:

* **features** — how many distinct identities the scheme yields
* **agreement** — share of the secondary's identities also produced by the
  primary. Higher is more stable.
* **merged** — identity groups containing more than one distinct feature title.
  These are the defect: distinct knowledge collapsed under one permanent ID.

The trade is visible in the two columns: URL-alone maximises agreement and
tolerates merging; composite eliminates merging and pays for it in agreement.
"""

from __future__ import annotations

import hashlib
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from ke.clock import SystemClock  # noqa: E402
from ke.discover import build_source  # noqa: E402
from ke.identity import normalise_title  # noqa: E402
from ke.pack import Pack  # noqa: E402
from ke.sources.base import HttpFetcher, SourceError  # noqa: E402

FETCHER = HttpFetcher()

#: Hosts that publish *announcements*, where one post routinely covers several
#: independent features. Used only to report how much of the pack is affected.
ANNOUNCEMENT_HOSTS = ("community.fabric.microsoft.com", "blog.fabric.microsoft.com")


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


# --- the candidate schemes ------------------------------------------------
# Each maps an item to an identity key. `None` means "this scheme cannot
# identify this item", which is itself a result worth counting.


def scheme_url_only(item) -> str:
    """Today's behaviour (ADR-0023): the announcement URL alone."""
    return digest(item.source_url)


def scheme_url_plus_title(item) -> str:
    """Proposed: announcement + feature title, the composite key."""
    return digest(f"{item.source_url}\n{normalise_title(item.title)}")


def scheme_url_title_category(item) -> str:
    """Adds the section heading — included to show why it must not be."""
    section = item.raw_tags[0] if item.raw_tags else ""
    return digest(f"{item.source_url}\n{normalise_title(item.title)}\n{section}")


def scheme_title_only(item) -> str:
    """Title alone, ignoring the announcement entirely."""
    return digest(normalise_title(item.title))


SCHEMES = {
    "url only (today)": scheme_url_only,
    "url + title": scheme_url_plus_title,
    "url + title + category": scheme_url_title_category,
    "title only": scheme_title_only,
}


def fetch(definition, clock):
    try:
        return build_source(definition, FETCHER, clock, max_summary_words=120).discover()
    except (SourceError, Exception) as exc:  # noqa: BLE001 - a probe reports, never raises
        print(f"    (could not fetch {definition.name}: {exc})")
        return []


def evaluate(name, key_of, primary, secondary) -> None:
    def group(items):
        out = defaultdict(set)
        for item in items:
            out[key_of(item)].add(normalise_title(item.title))
        return out

    primary_groups = group(primary)
    secondary_groups = group(secondary)

    merged = sum(1 for titles in secondary_groups.values() if len(titles) > 1)
    lost = sum(len(titles) - 1 for titles in secondary_groups.values() if len(titles) > 1)
    shared = set(primary_groups) & set(secondary_groups)
    agreement = len(shared) * 100 // len(secondary_groups) if secondary_groups else 0

    print(
        f"    {name:24} features={len(secondary_groups):4}  "
        f"agreement={agreement:3}%  merged-groups={merged:3}  distinct-lost={lost:3}"
    )


# ---------------------------------------------------------------------------
# Identity confidence — candidate rules, measured before being proposed.
#
# Confidence answers "how much do we trust this identity *right now*?", which is
# a different question from "what is this item's identity?". Identity must be
# permanent and run-independent. Confidence is a per-run assessment used to
# decide whether minting is safe yet, so it may legitimately use evidence that
# identity may not -- notably how many distinct features share an announcement
# in this run.
# ---------------------------------------------------------------------------

DOC_HOSTS = ("learn.microsoft.com", "docs.microsoft.com")


def confidence_of(item, titles_per_announcement) -> str:
    """Deterministic. No AI, no thresholds, no randomness."""
    durable = item.identity.basis.value in ("canonical-url", "source-identifier")
    title = normalise_title(item.title)
    shared = titles_per_announcement.get(item.source_url, 1)

    if not title and not durable:
        return "low"
    if item.identity.basis.value == "content-fingerprint":
        return "low"
    if durable and shared == 1:
        return "high"
    return "medium"


def measure_confidence(label, items) -> None:
    titles_per_announcement = defaultdict(set)
    for i in items:
        titles_per_announcement[i.source_url].add(normalise_title(i.title))
    counts = {u: len(t) for u, t in titles_per_announcement.items()}

    tally = defaultdict(int)
    doc_high = 0
    for i in items:
        c = confidence_of(i, counts)
        tally[c] += 1
        if c == "high" and any(h in i.source_url for h in DOC_HOSTS):
            doc_high += 1

    total = len(items) or 1
    print(f"\n  identity confidence — {label} ({total} items)")
    for level in ("high", "medium", "low"):
        print(f"    {level:7} {tally[level]:4}  ({tally[level] * 100 // total:3}%)")
    print(f"    of the high-confidence items, {doc_high} cite a documentation host")


def main() -> int:
    clock = SystemClock()
    repo_root = Path(__file__).resolve().parents[1]

    for pack in Pack.discover(repo_root):
        for definition in pack.source_definitions:
            secondaries = [f for f in definition.fallbacks if "markdown" in f.name]
            if not secondaries:
                continue

            print(f"\n=== {definition.name} ===")
            primary = fetch(definition, clock)
            secondary = fetch(secondaries[0], clock)
            if not secondary:
                continue

            hosted = sum(
                1 for i in secondary if any(h in i.source_url for h in ANNOUNCEMENT_HOSTS)
            )
            print(
                f"  {len(primary)} primary item(s), {len(secondary)} secondary item(s); "
                f"{hosted * 100 // len(secondary)}% cite an announcement host\n"
            )
            for name, key_of in SCHEMES.items():
                evaluate(name, key_of, primary, secondary)

            if primary:
                measure_confidence(f"{definition.name} (primary)", primary)
            measure_confidence(f"{secondaries[0].name} (secondary)", secondary)

    print("\nagreement  = share of secondary identities the primary also produced")
    print("             (proxy for stability under rewording — higher is safer)")
    print("merged     = identity groups holding >1 distinct feature title")
    print("             (the defect — distinct knowledge under one permanent ID)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
