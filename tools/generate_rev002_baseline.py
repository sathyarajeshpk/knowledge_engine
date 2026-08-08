"""Generate `state/rev002-baseline.json` once, for human review.

Run from the repository root::

    python tools/generate_rev002_baseline.py --write

## Why this is a tool and not engine code

The baseline is **immutable historical state**. Nothing in the engine writes it;
the engine only reads it. Creating or changing it is an explicit act that lands
as a reviewable diff.

There is deliberately no way to append new findings, drop stale ones or "learn"
from what validation sees. A baseline that maintains itself accepts whatever
arrives, which is the opposite of what a baseline is for — and it would recreate
the silent suppression M9 spent the milestone removing.

Running without `--write` prints the file and changes nothing, so the content
can be inspected before it exists.

## What it records, and what it does not claim

Each entry is one REV002 finding, keyed on
`(feature_id, first_revision, last_revision, changed_fields)`. Everything else —
pack, incident, run identifiers, commit — is provenance for a human and never
participates in matching.

Accepting these findings does **not** mean the 2026-08-01 incident has been
explained. It has not (`docs/CORRECTIONS.md` C-1).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "engine"))

from ke.audit import duplicate_writes  # noqa: E402
from ke.baseline import BASELINE_PATH, BaselineKey  # noqa: E402
from ke.harvest import load_objects_with_dirs  # noqa: E402
from ke.pack import Pack  # noqa: E402
from ke.validate import canonical_fields, identical_runs  # noqa: E402

NOTE = (
    "Historical REV002 findings accepted as baseline. These are known, bounded "
    "and characterised. This does NOT mean the 2026-08-01 incident has been "
    "explained - see docs/CORRECTIONS.md C-1. Matching is exact on "
    "(feature_id, first_revision, last_revision, changed_fields); every other "
    "field here is provenance for a human and never participates in matching."
)


def collect() -> tuple[list[dict], list[str]]:
    """Every current REV002 finding, plus the runs the audit oracle attributes.

    The findings come from the production detector and the run attribution from
    the independent oracle. They are recorded together because a human reading
    this file in a year needs both, but only the finding is ever matched on.
    """
    packs = Pack.discover(REPO_ROOT)
    entries: list[dict] = []
    runs: set[str] = set()

    for pack in sorted(packs, key=lambda p: p.name):
        for obj, _ in load_objects_with_dirs(pack):
            changes = [canonical_fields(r.changed_fields) for r in obj.revisions[1:]]
            for fields, length, start in identical_runs(changes):
                key = BaselineKey(
                    feature_id=str(obj.id),
                    first_revision=start,
                    last_revision=start + length - 1,
                    changed_fields=fields,
                )
                entries.append(key.to_entry(pack=pack.name))
            for write in duplicate_writes(obj, pack.name):
                runs.add(write.run_id)

    entries.sort(key=lambda e: (e["feature_id"], e["first_revision"]))
    return entries, sorted(runs)


def render(entries: list[dict], runs: list[str]) -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    ).stdout.strip()

    payload = {
        "_comment": NOTE,
        "generated_at": str(date.today()),
        "generated_from_commit": commit,
        "incident": {
            "runs": runs,
            "note": (
                "All findings below are attributable to these runs, identified "
                "by the independent run_id audit oracle (ke.audit). The "
                "mechanism that produced them remains unidentified."
            ),
        },
        "findings": entries,
    }
    return json.dumps(payload, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="write the file; without this, print and change nothing")
    args = parser.parse_args()

    entries, runs = collect()
    text = render(entries, runs)

    print(f"{len(entries)} finding(s) across {len({e['feature_id'] for e in entries})} object(s)")
    print(f"attributed to {len(runs)} run(s): {', '.join(runs)}")

    if not args.write:
        print("\n(dry run — nothing written; pass --write)\n")
        print(text)
        return 0

    path = REPO_ROOT / BASELINE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"\nWrote {path.relative_to(REPO_ROOT)}")
    print("Review it as a diff before committing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
