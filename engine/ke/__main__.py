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
from ke.pack import Pack, PackError, find_repo_root
from ke.validate import Finding, Level, has_errors, validate_repo


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
    packs = Pack.discover(repo_root)
    scanned = sum(1 for pack in packs for _ in pack.iter_object_dirs())

    if not findings:
        print(f"ok: {len(packs)} pack(s), {scanned} knowledge object(s), no findings")
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
        f"\n{len(packs)} pack(s), {scanned} knowledge object(s): "
        f"{errors} error(s), {warnings} warning(s)"
        + (" (strict: warnings fail)" if strict else "")
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
