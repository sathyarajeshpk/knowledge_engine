"""Command-line entry point: `python -m ke <command>` (or `ke <command>`).

M0 ships one command, `validate`. Later milestones add `discover`, `harvest`,
`index`, `digest`, `search`, `generate`, `migrate` and friends as subcommands
here.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from ke import __version__
from ke.clock import SystemClock
from ke.pack import Pack, PackError, find_repo_root
from ke.validate import Finding, Level, has_errors, scan_summary, validate_repo


def build_parser() -> argparse.ArgumentParser:
    # Imported here rather than at module scope to keep `ke --help` from pulling
    # in the review machinery's dependencies, matching how every command handler
    # in this file imports what it needs.
    from ke.reviewq import TaskKind

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

    harvest = subcommands.add_parser(
        "harvest",
        help="discover, deduplicate, mint Feature IDs and store knowledge objects",
        description=(
            "The full pipeline. Discovers from every configured source, "
            "deduplicates against what is already stored, mints permanent "
            "Feature IDs for items that clear the confidence gate, writes "
            "knowledge objects, rebuilds indexes and appends the run log. "
            "Items that do not clear the gate are queued, never dropped."
        ),
    )
    harvest.add_argument("--pack", metavar="NAME", help="pack to harvest")
    harvest.add_argument("--repo-root", metavar="PATH", type=Path)
    harvest.add_argument(
        "--notify",
        action="store_true",
        help="send the digest through configured channels (off by default)",
    )
    harvest.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be minted without writing anything",
    )
    harvest.set_defaults(handler=_run_harvest)

    index = subcommands.add_parser(
        "index",
        help="rebuild pack indexes from the stored knowledge objects",
        description=(
            "Indexes are derived data and are always rebuilt in full, never "
            "patched, so they cannot drift from the objects they describe."
        ),
    )
    index.add_argument("--pack", metavar="NAME")
    index.add_argument("--repo-root", metavar="PATH", type=Path)
    index.set_defaults(handler=_run_index)

    review = subcommands.add_parser(
        "review",
        help="work the unified review queue",
        description=(
            "One workflow over every kind of pending decision: items held back "
            "from minting, objects no rule could classify, and items a source "
            "retitled. Three backlogs growing in parallel is how a review queue "
            "becomes permanent."
        ),
    )
    review.add_argument(
        "action",
        choices=("list", "next", "show", "approve", "archive", "resolve"),
        help="list/next/show to inspect; approve/archive/resolve to decide",
    )
    review.add_argument("key", nargs="?", help="task key (a short prefix is enough)")
    review.add_argument(
        "--kind",
        # Derived from the enum rather than repeated. M8 added
        # `TaskKind.CROSS_PACK`, wired up a provider for it and rendered it in
        # the queue -- and this hard-coded tuple was the one place that did not
        # follow, so `ke review --kind cross-pack` was rejected by argparse for
        # a kind the engine was producing. Listing the enum makes the next kind
        # impossible to half-add.
        choices=tuple(str(kind) for kind in TaskKind),
        help="restrict to one kind",
    )
    review.add_argument(
        "--all",
        action="store_true",
        help="apply the action to every matching task (use with --kind)",
    )
    review.add_argument("--pack", metavar="NAME")
    review.add_argument("--repo-root", metavar="PATH", type=Path)
    review.set_defaults(handler=_run_review)

    history = subcommands.add_parser(
        "history",
        help="show what an object looked like at each revision",
        description=(
            "Reconstructs an object's past from the snapshots it carries. No "
            "Git archaeology and no AI: the object holds its own history."
        ),
    )
    history.add_argument("id", help="Feature ID, e.g. MSF-2026-07-001")
    history.add_argument("--at", type=int, metavar="N", help="show one revision")
    history.add_argument("--pack", metavar="NAME")
    history.add_argument("--repo-root", metavar="PATH", type=Path)
    history.set_defaults(handler=_run_history)

    supersede = subcommands.add_parser(
        "supersede",
        help="record that one feature replaced another",
        description=(
            "Marks the old object `replaced` and links both directions. Nothing "
            "is deleted: the old object keeps its Feature ID, its history and "
            "its place in the repository."
        ),
    )
    supersede.add_argument("old", help="the Feature ID being replaced")
    supersede.add_argument("--by", required=True, metavar="ID", help="its replacement")
    supersede.add_argument("--pack", metavar="NAME")
    supersede.add_argument("--repo-root", metavar="PATH", type=Path)
    supersede.set_defaults(handler=_run_supersede)

    search = subcommands.add_parser(
        "search",
        help="find stored knowledge objects",
        description=(
            "Filters compose by AND: every option you give must match. There is "
            "no query language and no relevance ranking — a wrong ranking hides "
            "things convincingly, which is worse than no ranking at all."
        ),
    )
    search.add_argument("text", nargs="?", help="substring of title, category or tags")
    search.add_argument("--tier", type=int, choices=(1, 2, 3))
    search.add_argument("--priority", choices=("high", "medium", "low"))
    search.add_argument(
        "--difficulty", choices=("beginner", "intermediate", "advanced")
    )
    search.add_argument(
        "--learning-status",
        choices=("not-started", "in-progress", "learned", "revisit"),
    )
    search.add_argument("--status", choices=("active", "replaced", "deprecated"))
    search.add_argument("--category", metavar="NAME")
    search.add_argument("--tag", metavar="TAG")
    search.add_argument("--source", metavar="NAME")
    search.add_argument("--since", metavar="YYYY-MM-DD")
    search.add_argument("--until", metavar="YYYY-MM-DD")
    search.add_argument(
        "--needs-review", action="store_true", help="only objects flagged for review"
    )
    search.add_argument(
        "--stale", action="store_true", help="only objects with a stale artifact"
    )
    search.add_argument("--limit", type=int, metavar="N")
    search.add_argument(
        "--ids-only",
        action="store_true",
        help="print bare Feature IDs, one per line, for piping",
    )
    search.add_argument("--pack", metavar="NAME")
    search.add_argument("--repo-root", metavar="PATH", type=Path)
    search.set_defaults(handler=_run_search)

    get = subcommands.add_parser(
        "get",
        help="show one knowledge object in full",
        description=(
            "Everything the object records about itself, including artifact "
            "status. The article text is a file on disk and is deliberately not "
            "duplicated here."
        ),
    )
    get.add_argument("id", help="Feature ID, e.g. MSF-2026-07-001")
    get.add_argument("--pack", metavar="NAME")
    get.add_argument("--repo-root", metavar="PATH", type=Path)
    get.set_defaults(handler=_run_get)

    generate = subcommands.add_parser(
        "generate",
        help="assemble a context pack to paste into any AI model",
        description=(
            "Prints a self-contained document — instruction, knowledge, "
            "provenance — that produces a usable artifact when pasted into a "
            "fresh model session with no other context. The engine never calls "
            "a model itself: that is what keeps this free and vendor-neutral."
        ),
    )
    generate.add_argument(
        "type",
        help="artifact type (`ke generate list` shows them all)",
    )
    generate.add_argument("--id", metavar="FEATURE_ID", help="the knowledge object")
    generate.add_argument(
        "--attach",
        metavar="FILE",
        help="store the model's answer from FILE (or `-` for stdin)",
    )
    generate.add_argument(
        "--request",
        action="store_true",
        help="record that this artifact is wanted, without generating it",
    )
    generate.add_argument(
        "--model",
        metavar="NAME",
        help="record which model produced it (provenance only; never read)",
    )
    generate.add_argument(
        "--force",
        action="store_true",
        help="replace an existing, current artifact",
    )
    generate.add_argument("--pack", metavar="NAME")
    generate.add_argument("--repo-root", metavar="PATH", type=Path)
    generate.set_defaults(handler=_run_generate)

    status = subcommands.add_parser(
        "status",
        help="artifact coverage across the pack",
        description=(
            "What has been generated, what was requested and never produced, "
            "and what has gone stale because the source changed underneath it."
        ),
    )
    status.add_argument(
        "--stale", action="store_true", help="list only the stale artifacts"
    )
    status.add_argument(
        "--requested",
        action="store_true",
        help="list only artifacts requested but not generated",
    )
    status.add_argument(
        "--refresh",
        action="store_true",
        help="write computed staleness into metadata (never regenerates)",
    )
    status.add_argument("--pack", metavar="NAME")
    status.add_argument("--repo-root", metavar="PATH", type=Path)
    status.set_defaults(handler=_run_status)
    return parser


def _packs_for(args: argparse.Namespace) -> tuple[Path, list[Pack]] | tuple[Path, None]:
    repo_root = args.repo_root or find_repo_root()
    try:
        packs = Pack.discover(repo_root)
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return repo_root, None
    if not packs:
        # Finding nothing is an error, not an empty success. A mistyped
        # `--repo-root` -- or a checkout that did not include `domain-packs/` --
        # would otherwise make every command exit 0 having done nothing, and the
        # weekly workflow would report a green run week after week while the
        # engine harvested nothing at all.
        print(
            f"error: no domain packs found under {repo_root / 'domain-packs'}",
            file=sys.stderr,
        )
        return repo_root, None
    if getattr(args, "pack", None):
        packs = [p for p in packs if args.pack in (p.name, p.root.name)]
        if not packs:
            print(f"error: no pack named {args.pack!r}", file=sys.stderr)
            return repo_root, None
    return repo_root, packs


def _run_harvest(args: argparse.Namespace) -> int:
    from ke.harvest import harvest_pack

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    exit_code = 0
    failed: list[str] = []
    for pack in packs:
        from ke.lock import LockError, pack_lock

        try:
            # Two harvests minting at once would both allocate the same ID, and
            # a duplicate Feature ID is permanent. The workflow's concurrency
            # group covers scheduled runs; this covers everything else.
            with pack_lock(pack.state_dir, holder="ke harvest"):
                report = harvest_pack(
                    pack,
                    clock=SystemClock(),
                    dry_run=args.dry_run,
                    notify=getattr(args, "notify", False),
                )
        except Exception as exc:  # noqa: BLE001 - deliberate; see below
            # Catches `LockError` and anything `harvest_pack` raises.
            #
            # **One pack must not take the run down with it.** Packs are
            # independent by construction (ADR-0016) -- separate sources,
            # separate state, separate locks -- and until M8 this loop did not
            # reflect that: any failure in the first pack returned before the
            # second was touched, so a stuck lock on Fabric silently cost a
            # week of Azure.
            #
            # Invisible with one pack, where "abort the pack" and "abort the
            # run" are the same thing.
            #
            # `Exception`, not `BaseException`: Ctrl-C and SystemExit must still
            # stop everything. The run still exits non-zero, so a failure is
            # never reported as success -- it just no longer costs the packs
            # that were fine.
            print(f"error: {pack.name}: {exc}", file=sys.stderr)
            failed.append(pack.name)
            exit_code = 2
            continue
        print(f"\n=== {report.summary_line()} ===\n")

        if report.minted:
            print(f"  Minted {len(report.minted)} Feature ID(s):")
            for feature_id in report.minted[:20]:
                print(f"    {feature_id}")
            if len(report.minted) > 20:
                print(f"    … and {len(report.minted) - 20} more")
            print()

        if report.updated:
            print(f"  Updated {len(report.updated)} existing object(s):")
            for entry in report.updated[:20]:
                print(f"    {entry}")
            if len(report.updated) > 20:
                print(f"    … and {len(report.updated) - 20} more")
            print()

        if report.unchanged:
            print(f"  {report.unchanged} object(s) unchanged — nothing rewritten\n")

        if report.queued:
            print(f"  {report.queued} item(s) queued for review "
                  "(nothing was dropped) — see indexes/review-queue.md\n")

        if report.index_paths:
            print(f"  Rebuilt {len(report.index_paths)} index file(s)")
        if report.digest_path:
            print(f"  Digest: {report.digest_path}")
        for line in report.notifications:
            print(f"  Notified {line}")
        for line in report.notification_failures:
            # Already redacted by `notify_all`; a notifier failure is never
            # allowed to fail the run.
            print(f"  ! notification failed — {line}")
        print()

        for message in report.warnings:
            # Not an error, so the exit code is untouched: the run worked. It is
            # printed anyway because the result is not what the reader would
            # otherwise assume.
            print(f"    ! {message}")

        for message in report.review_items:
            print(f"    !! SOURCE UNREACHABLE — {message}")
            exit_code = 1
        for message in report.errors:
            print(f"    !! ERROR — {message}")
            exit_code = 1

        if args.dry_run:
            print("  (dry run: nothing was written)")

    if failed:
        # Restated at the end because the per-pack error scrolled past several
        # screens of successful output. A run that harvested three packs and
        # dropped one must not read as a clean run to somebody skimming the tail
        # of a workflow log.
        print(
            f"\n!! {len(failed)} pack(s) failed and were skipped: "
            f"{', '.join(failed)}",
            file=sys.stderr,
        )
    return exit_code


def _run_index(args: argparse.Namespace) -> int:
    from ke.harvest import load_existing_objects
    from ke.indexer import write_indexes
    from ke.review import ReviewQueue

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    for pack in packs:
        queue = ReviewQueue.load(pack.state_dir / "review-queue.json")
        written = write_indexes(
            pack.indexes_dir, load_existing_objects(pack), queue.pending,
            pack.name, pack,
        )
        print(f"{pack.name}: rebuilt {len(written)} index file(s)")
    return 0


def _run_review(args: argparse.Namespace) -> int:
    from ke.reviewq import Action, TaskKind, apply_action, collect, counts, find

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    kinds = {TaskKind(args.kind)} if args.kind else None

    for pack in packs:
        if args.action in ("list", "next"):
            _print_queue(pack, kinds, only_first=args.action == "next")
        elif args.action == "show":
            if not args.key:
                print("error: `show` needs a key", file=sys.stderr)
                return 2
            try:
                _print_task(find(pack, args.key))
            except KeyError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        else:
            code = _decide(pack, args, Action(args.action), kinds)
            if code:
                return code
    return 0


def _print_queue(pack, kinds, *, only_first: bool) -> None:
    from ke.reviewq import collect, counts

    tasks = collect(pack, kinds)
    tally = counts(pack)
    print(f"\n=== {pack.name}: {sum(tally.values())} pending ===")
    print("    " + " · ".join(f"{k}: {v}" for k, v in tally.items() if v))
    print()
    if not tasks:
        print("  Nothing pending.\n")
        return
    for task in tasks[:1] if only_first else tasks:
        seen = task.first_seen.isoformat() if task.first_seen else "—"
        print(f"  [{task.kind}] {task.short_key}  {task.title[:62]}")
        print(f"      first seen {seen} · {task.reason[:88]}")
        print(f"      actions: {', '.join(task.actions)}")
    print()
    if only_first:
        first = tasks[0]
        print(f"  Decide with: ke review {first.actions[0]} {first.short_key}\n")


def _print_task(task) -> None:
    print(f"\n{task.kind}  {task.short_key}")
    print(f"  {task.title}")
    print(f"  reason: {task.reason}")
    if task.feature_id:
        print(f"  feature: {task.feature_id}")
    for name, value in sorted(task.detail.items()):
        print(f"  {name}: {value}")
    print(f"  actions: {', '.join(task.actions)}\n")


def _decide(pack, args, action, kinds) -> int:
    from ke.reviewq import apply_action, collect, find

    if args.all:
        targets = [t for t in collect(pack, kinds) if action in t.actions]
        if not targets:
            print(f"{pack.name}: nothing to {action}")
            return 0
        done = 0
        for task in targets:
            try:
                apply_action(pack, task, action)
                done += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {task.short_key}: {exc}", file=sys.stderr)
        print(f"{pack.name}: {action}d {done} task(s)")
        return 0

    if not args.key:
        print(f"error: `{action}` needs a key, or --all with --kind", file=sys.stderr)
        return 2
    try:
        print(apply_action(pack, find(pack, args.key), action))
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _run_history(args: argparse.Namespace) -> int:
    from ke.history import HistoryError, at_revision, find_object, render_timeline

    _, packs = _packs_for(args)
    if packs is None:
        return 2
    for pack in packs:
        try:
            obj, _ = find_object(pack, args.id)
        except HistoryError:
            continue
        if args.at is not None:
            try:
                snapshot = at_revision(obj, args.at)
            except HistoryError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"\n{obj.id} at revision {snapshot.revision} ({snapshot.date})")
            print(f"  title:   {snapshot.title}")
            print(f"  summary: {snapshot.summary[:400]}")
            print(f"  note:    {snapshot.note}\n")
        else:
            print(render_timeline(obj))
        return 0
    print(f"error: no object {args.id!r} in any pack", file=sys.stderr)
    return 2


def _run_supersede(args: argparse.Namespace) -> int:
    from ke.clock import SystemClock
    from ke.history import HistoryError, supersede

    _, packs = _packs_for(args)
    if packs is None:
        return 2
    for pack in packs:
        try:
            for line in supersede(pack, args.old, args.by, today=SystemClock().today()):
                print(line)
            return 0
        except HistoryError as exc:
            last = exc
    print(f"error: {last}", file=sys.stderr)
    return 2


def _parse_day(raw: str | None, flag: str) -> "date | None":
    """Parse a `--since` / `--until` value, or explain why it will not parse.

    Raises rather than silently ignoring an unparseable date: a filter that
    quietly does nothing returns confidently wrong results, which is the worst
    behaviour available to a search command.
    """
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{flag} must be YYYY-MM-DD, not {raw!r}") from exc


def _query_from(args: argparse.Namespace) -> "Query":
    from ke.models import Difficulty, LearningPriority, LearningStatus, ObjectStatus, Tier
    from ke.retrieve import Query

    return Query(
        text=args.text,
        tier=Tier(args.tier) if args.tier else None,
        learning_priority=LearningPriority(args.priority) if args.priority else None,
        difficulty=Difficulty(args.difficulty) if args.difficulty else None,
        learning_status=(
            LearningStatus(args.learning_status) if args.learning_status else None
        ),
        status=ObjectStatus(args.status) if args.status else None,
        category=args.category,
        tag=args.tag,
        source=args.source,
        since=_parse_day(args.since, "--since"),
        until=_parse_day(args.until, "--until"),
        # `store_true` gives False when absent, but False means "only objects
        # NOT flagged", which is a different question. Absent must stay None.
        needs_review=True if args.needs_review else None,
        stale=True if args.stale else None,
    )


def _run_search(args: argparse.Namespace) -> int:
    from ke.retrieve import render_results, search

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    try:
        query = _query_from(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for pack in packs:
        found = search(pack, query)
        shown = found[: args.limit] if args.limit else found
        if args.ids_only:
            for obj in shown:
                print(obj.id)
            continue
        if len(packs) > 1:
            print(f"\n=== {pack.name} ===")
        print(render_results(shown, total=len(found)))
    return 0


def _run_get(args: argparse.Namespace) -> int:
    from ke.retrieve import render_object, resolve

    _, packs = _packs_for(args)
    if packs is None:
        return 2
    try:
        _, obj, directory = resolve(packs, args.id)
    except KeyError as exc:
        # `str(KeyError)` is the repr of its argument, quotes and all. Reaching
        # for args[0] keeps the message readable rather than 'like this'.
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 2
    print(render_object(obj, directory))
    return 0


def _run_generate(args: argparse.Namespace) -> int:
    from ke.generate import GenerateError, available_templates, build_pack, load_template
    from ke.models import ArtifactType

    if args.type == "list":
        print("\nArtifact types:\n")
        for template in available_templates():
            print(f"  {str(template.artifact_type):<26} v{template.prompt_version}  "
                  f"{template.description}")
        print("\n  ke generate tutorial --id MSF-2026-05-029\n")
        return 0

    try:
        artifact_type = ArtifactType(args.type)
    except ValueError:
        names = ", ".join(str(t) for t in ArtifactType)
        print(f"error: unknown artifact type {args.type!r}\n  try: {names}",
              file=sys.stderr)
        return 2

    if not args.id:
        print("error: --id is required (which knowledge object?)", file=sys.stderr)
        return 2

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    from ke.retrieve import resolve

    try:
        pack, obj, directory = resolve(packs, args.id)
        template = load_template(artifact_type)
    except (KeyError, GenerateError) as exc:
        print(f"error: {exc.args[0] if exc.args else exc}", file=sys.stderr)
        return 2

    from ke.attach import AttachError, attach, read_content, request

    if args.request:
        try:
            request(pack, obj, directory, template)
        except AttachError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"recorded: {obj.id} wants a {artifact_type}")
        print("  it will appear in `ke status` and the weekly digest")
        return 0

    if args.attach:
        try:
            content = read_content(args.attach)
            _, path = attach(
                pack, obj, directory, template, content,
                today=SystemClock().today(), model=args.model, force=args.force,
            )
        except (AttachError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"attached: {pack.relative(path)}")
        print(f"  generated from revision {obj.current_revision}; "
              f"prompt v{template.prompt_version}")
        print("  the file is yours — the engine will never rewrite it")
        return 0

    print(build_pack(pack, obj, directory, template))
    return 0


def _run_status(args: argparse.Namespace) -> int:
    from ke.artifacts import Coverage, refresh_pack, render_status

    _, packs = _packs_for(args)
    if packs is None:
        return 2

    for pack in packs:
        if args.refresh:
            changed = refresh_pack(pack)
            print(f"{pack.name}: marked {changed} artifact(s) stale")
        coverage = Coverage.of(pack)
        print(render_status(coverage, stale_only=args.stale,
                            requested_only=args.requested))
    return 0


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
    from ke.acquisition.confidence import summarise as confidence_summary
    from ke.acquisition.discover import discover_all, health_summary
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
