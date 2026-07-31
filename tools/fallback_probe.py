"""Run each source's primary and its Markdown secondary, and compare them.

**This is a diagnostic, not part of the engine.** Like the other probes it lives
in `tools/` because it exists to answer one question and then get out of the way.

The question: **is the Markdown source a genuine fallback, or merely a source
that happens to respond?**

`ke discover` cannot answer this. A fallback only runs when its primary fails,
and on a healthy day the primary never fails -- so the secondary is exercised
for the first time on precisely the day nobody is watching. That is the worst
possible moment to discover it was broken all along.

Three things are measured, in increasing order of importance:

1. **Reachability and yield.** Does it return items at all, and roughly as many
   as the primary? A secondary returning a tenth of the primary is not a
   fallback, it is a data-loss event waiting for an outage.

2. **Identity quality.** What fraction rest on a canonical URL (ADR-0023's
   strongest basis) rather than a title hash? Markdown links are relative
   document paths that must be reconstructed, so this is the number that says
   whether reconstruction is working against real markup.

3. **Cross-representation identity agreement.** Of the updates both
   representations found, how many agree on identity? This is the one that
   matters. If the two disagree, failing over to the secondary mints **new
   permanent Feature IDs for knowledge already stored** -- the exact outcome the
   fallback chain exists to prevent, arriving through the mechanism meant to
   prevent it.

A run that reports high yield and low agreement means the fallback is dangerous,
not healthy. Read line 3 first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from ke.clock import SystemClock  # noqa: E402
from ke.discover import build_source  # noqa: E402
from ke.identity import IdentityBasis  # noqa: E402
from ke.models import SourceRole  # noqa: E402
from ke.pack import Pack  # noqa: E402
from ke.sources.base import HttpFetcher, SourceError  # noqa: E402

FETCHER = HttpFetcher()


def run(definition, clock) -> tuple[list, str | None]:
    """Fetch and parse one source, returning items or the reason it failed."""
    try:
        source = build_source(definition, FETCHER, clock, max_summary_words=120)
    except SourceError as exc:
        return [], str(exc)
    try:
        return source.discover(), None
    except SourceError as exc:
        return [], str(exc)
    except Exception as exc:  # noqa: BLE001 - a probe reports bugs, it does not raise
        return [], f"{type(exc).__name__}: {exc}"


def durable_share(items) -> str:
    if not items:
        return "n/a"
    durable = sum(1 for i in items if i.identity.basis is IdentityBasis.CANONICAL_URL)
    return f"{durable}/{len(items)} ({durable * 100 // len(items)}%)"


def compare(primary: list, secondary: list) -> None:
    """Report identity agreement between two representations of one source."""
    primary_keys = {i.identity.key for i in primary}
    secondary_keys = {i.identity.key for i in secondary}
    shared = primary_keys & secondary_keys

    print(f"    identity agreement: {len(shared)} key(s) in both representations")
    if secondary_keys:
        print(
            f"      of the secondary's {len(secondary_keys)}, "
            f"{len(shared) * 100 // len(secondary_keys)}% match the primary"
        )

    # Items the secondary found under an identity the primary never produced are
    # the failover risk: each one would become a *new* permanent Feature ID.
    only_secondary = secondary_keys - primary_keys
    if only_secondary:
        print(f"      {len(only_secondary)} secondary-only key(s) — first 5:")
        examples = [i for i in secondary if i.identity.key in only_secondary][:5]
        for item in examples:
            print(f"        [{item.identity.basis}] {item.title[:70]}")


def main() -> int:
    clock = SystemClock()
    repo_root = Path(__file__).resolve().parents[1]

    for pack in Pack.discover(repo_root):
        print(f"\n=== {pack.name} ===")

        for definition in pack.source_definitions:
            secondaries = [
                f for f in definition.fallbacks if f.role is SourceRole.SECONDARY
            ]
            if not secondaries:
                continue

            print(f"\n  {definition.name} ({definition.adapter})")
            primary, error = run(definition, clock)
            print(f"    primary:   {len(primary)} item(s)" + (f" — {error}" if error else ""))
            print(f"    durable identities: {durable_share(primary)}")

            for secondary_definition in secondaries:
                print(
                    f"\n  {secondary_definition.name} "
                    f"({secondary_definition.adapter}, {secondary_definition.role})"
                )
                secondary, error = run(secondary_definition, clock)
                print(
                    f"    secondary: {len(secondary)} item(s)"
                    + (f" — {error}" if error else "")
                )
                print(f"    durable identities: {durable_share(secondary)}")
                dated = sum(1 for i in secondary if i.published_date)
                print(f"    dated: {dated}/{len(secondary) or 1}")

                if primary and secondary:
                    compare(primary, secondary)

    print("\nRead the identity-agreement line first: high yield with low")
    print("agreement means failing over would mint duplicate permanent IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
