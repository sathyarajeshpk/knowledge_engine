"""Command-line entry point: `python -m ke <command>` (or `ke <command>`).

M0 ships one command, `validate`. Later milestones add `discover`, `harvest`,
`index`, `digest`, `search`, `generate`, `migrate` and friends as subcommands
here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ke import __version__
from ke.clock import SystemClock
from ke.pack import Pack, PackError, find_repo_root
from ke.validate import Finding, Level, has_errors, scan_summary, validate_repo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ke",
        description="Knowledge Engine - build and maintain Domain Packs.",
    )
    parser.add_argument("--version", action="version", version=f"knowledge-engine {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    validate = subcommands.add_parser(
        "validate",
        help="check Domain Packs against the schema contract",
        description=(
            "Validate pack structure, metadata schema, Feature ID integrity and "
            "the ID registry. Exits non-zero if any error is found."
        ),
    )
    validate.add_argument(
        "--pack",
        metavar="NAME",
        help="validate only this pack (default: every pack in the repository)",
    )
    validate.add_argument(
        "--repo-root",
        metavar="PATH",
        type=Path,
        help="repository root (default: discovered from the working directory)",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    validate.set_defaults(handler=_run_validate)

    discover = subcommands.add_parser(
        "discover",
        help="fetch from configured sources and report what was found",
        description=(
            "Run every pollable source and its fallback chain, printing the "
            "items discovered and the resulting source health. Writes nothing: "
            "storage arrives in M2, so discovery can be verified before "
            "anything permanent is created."
        ),
    )
    discover.add_argument("--pack", metavar="NAME", help="pack to discover for")
    discover.add_argument("--repo-root", metavar="PATH", type=Path)
    discover.add_argument(
        "--source", metavar="NAME", help="run only this source (and its fallbacks)"
    )
    discover.add_argument(
        "--limit", type=int, default=15, help="items to print per source (default 15)"
    )
    discover.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="report only; the default and currently the only mode",
    )
    discover.set_defaults(handler=_run_discover)
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    repo_root = args.repo_root or find_repo_root()

    try:
        findings = validate_repo(repo_root, args.pack)
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _report(repo_root, findings, strict=args.strict)
    return 1 if has_errors(findings, strict=args.strict) else 0


def _report(repo_root: Path, findings: list[Finding], *, strict: bool) -> None:
    """Print findings grouped by location, errors first within each group."""
    pack_count, scanned = scan_summary(repo_root)

    if not findings:
        print(f"ok: {pack_count} pack(s), {scanned} knowledge object(s), no findings")
        return

    by_location: dict[str, list[Finding]] = {}
    for finding in findings:
        by_location.setdefault(finding.location, []).append(finding)

    for location in sorted(by_location):
        for finding in sorted(
            by_location[location], key=lambda f: (f.level is not Level.ERROR, f.code)
        ):
            print(finding)

    errors = sum(1 for f in findings if f.level is Level.ERROR)
    warnings = len(findings) - errors
    print(
        f"\n{pack_count} pack(s), {scanned} knowledge object(s): "
        f"{errors} error(s), {warnings} warning(s)"
        + (" (strict: warnings fail)" if strict else "")
    )


def _run_discover(args: argparse.Namespace) -> int:
    from ke.confidence import summarise as confidence_summary
    from ke.discover import discover_all, health_summary
    from ke.models import HealthState

    repo_root = args.repo_root or find_repo_root()
    try:
        packs = Pack.discover(repo_root)
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.pack:
        packs = [p for p in packs if args.pack in (p.name, p.root.name)]
        if not packs:
            print(f"error: no pack named {args.pack!r}", file=sys.stderr)
            return 2

    clock = SystemClock()
    exit_code = 0

    for pack in packs:
        definitions = pack.source_definitions
        if args.source:
            definitions = [d for d in definitions if d.name == args.source]
            if not definitions:
                print(f"error: no source named {args.source!r}", file=sys.stderr)
                return 2

        print(f"\n=== {pack.name} — {len(definitions)} source(s) ===\n")
        if not definitions:
            print("  no sources configured")
            continue

        result = discover_all(
            definitions,
            clock=clock,
            max_summary_words=pack.max_summary_words,
        )

        by_source: dict[str, list] = {}
        for item in result.items:
            by_source.setdefault(item.source_name, []).append(item)

        for name in sorted(by_source):
            items = by_source[name]
            print(f"  {name}: {len(items)} item(s)")
            for item in items[: args.limit]:
                when = item.published_date or "undated"
                gate = "mint" if item.mints_automatically else "REVIEW"
                print(
                    f"    [{when} · {item.date_precision}/{item.date_confidence}] "
                    f"{item.title[:80]}"
                )
                print(
                    f"        identity: {item.identity.basis} "
                    f"· confidence: {item.identity_confidence} ({gate}) "
                    f"· {item.source_url[:70]}"
                )
            if len(items) > args.limit:
                print(f"    … and {len(items) - args.limit} more")
            print()

        # The gate is only useful if the queue is visible. A held-back item that
        # nobody ever sees is indistinguishable from one that was dropped.
        tally = confidence_summary(result.items)
        print("  Identity confidence:")
        for level, count in tally.items():
            if count:
                print(f"    {level}: {count}")
        print(
            f"    → {len(result.mintable)} would mint automatically, "
            f"{len(result.needs_review)} queued for review"
        )

        if result.collisions:
            print(
                f"\n  Collisions — {len(result.collisions)} announcement(s) cited by "
                "several distinct features (queued, never merged):"
            )
            for collision in result.collisions[: args.limit]:
                print(f"    {collision.feature_count} features share one identity:")
                print(f"      {(collision.announcement_url or '(no announcement)')[:88]}")
                for title in collision.titles[:4]:
                    print(f"        · {title[:76]}")
                if collision.feature_count > 4:
                    print(f"        … and {collision.feature_count - 4} more")
            if len(result.collisions) > args.limit:
                print(f"    … and {len(result.collisions) - args.limit} more")
        print()

        print("  Source health:")
        for state, names in health_summary(result.health).items():
            if names:
                print(f"    {state}: {', '.join(names)}")
        for attempt in result.attempts:
            if not attempt.ok:
                print(f"    ! {attempt.source_name}: {attempt.failure_reason}")
        for review in result.review_items:
            print(f"    !! REVIEW NEEDED — {review.source_name}: {review.reason}")
            exit_code = 1
        if result.skipped:
            print(f"    not polled (retained for provenance): {', '.join(result.skipped)}")

    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
