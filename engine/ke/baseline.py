"""Historical REV002 findings accepted as a baseline.

## What a baseline entry means

> This finding is known, bounded and characterised, and is accepted as
> historical baseline.

It does **not** mean the incident that produced it has been explained. The 35
entries generated in M9 come from four harvest runs on 2026-08-01 whose
mechanism remains unidentified (`docs/CORRECTIONS.md` C-1). Accepting history is
not the same as understanding it, and the file says so in its own `_comment`.

## Downgrade, never delete

A matched finding becomes `INFO`. It is still printed by `ke validate`; it stops
failing `--strict`. Hiding it outright would be the same mistake as the
whole-chain detector this milestone replaced -- a silent suppression nobody can
audit.

## Matching is exact, and the revision range is why it is safe

The key is `(feature_id, first_revision, last_revision, changed_fields)`. All
four must match. There is no fuzzy matching, no prefix matching, and no
object-level matching.

The revision range is the component that makes future suppression impossible.
Revisions are **append-only**, so revision numbers only ever increase and a range
that has been used can never be re-issued. Every baselined range ends at 11; any
future flip-flop on those objects starts at 12 or later, produces a different
key, matches nothing, and stays a `WARNING`.

That guarantee only holds because the detector reports **every** qualifying run
rather than the longest (M9-6). Against the previous detector a new shorter run
produced no new finding at all, and no keying scheme could have been safe.

## Immutable historical state

Nothing in the engine writes this file. It is generated once by
`tools/generate_rev002_baseline.py`, reviewed as a diff, and committed. The
engine only ever reads it.

There is deliberately no mechanism to append new findings, drop stale ones,
rewrite keys, or "learn" from what it sees. A baseline that maintains itself is
a baseline that quietly accepts whatever arrives, which is the opposite of what
it is for.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

#: Repository-level state, beside `cross-pack.json`. A fact about the
#: repository's history rather than about any one pack.
BASELINE_PATH = Path("state") / "rev002-baseline.json"


@dataclass(frozen=True)
class BaselineKey:
    """The four fields that must all match. Nothing else participates.

    `pack`, run identifiers, the generating commit and the incident note are
    stored alongside as **provenance** -- readable evidence for a human deciding
    whether an entry still deserves to be there. They are deliberately not part
    of this key, so a pack rename or a re-run cannot silently change what an
    entry matches.
    """

    feature_id: str
    first_revision: int
    last_revision: int
    changed_fields: tuple[str, ...]

    @classmethod
    def from_entry(cls, entry: dict) -> BaselineKey:
        from ke.validate import canonical_fields

        return cls(
            feature_id=str(entry["feature_id"]),
            first_revision=int(entry["first_revision"]),
            last_revision=int(entry["last_revision"]),
            # Canonicalised on the way in as well as on the way out, so a
            # hand-edited file with mis-ordered fields still matches the finding
            # it describes rather than silently matching nothing.
            changed_fields=canonical_fields(entry.get("changed_fields") or ()),
        )

    def to_entry(self, **provenance) -> dict:
        return {
            "feature_id": self.feature_id,
            "first_revision": self.first_revision,
            "last_revision": self.last_revision,
            "changed_fields": list(self.changed_fields),
            **provenance,
        }

    def describe(self) -> str:
        return (
            f"{self.feature_id} revisions {self.first_revision}-"
            f"{self.last_revision} ({', '.join(self.changed_fields)})"
        )


@dataclass
class Baseline:
    """Accepted historical findings, and which of them were seen this run."""

    entries: dict[BaselineKey, dict] = field(default_factory=dict)
    #: Keys matched during this validation. Reset per `Baseline` instance, never
    #: persisted -- this is bookkeeping for stale detection, not state.
    seen: set[BaselineKey] = field(default_factory=set)

    @classmethod
    def load(cls, repo_root: Path) -> Baseline:
        """Read the baseline, or an empty one if there is none.

        A missing file means "nothing is grandfathered", which is the correct
        default and the state the repository is in until one is committed.

        A **corrupt** file is different and is not silently tolerated: returning
        an empty baseline there would look identical to "nothing grandfathered"
        while actually meaning "35 findings we cannot read", and the difference
        matters. It raises.
        """
        path = Path(repo_root) / BASELINE_PATH
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = {}
        for entry in raw.get("findings") or ():
            entries[BaselineKey.from_entry(entry)] = entry
        return cls(entries=entries)

    def match(self, key: BaselineKey) -> bool:
        """Whether this exact finding is grandfathered. Records the hit."""
        if key in self.entries:
            self.seen.add(key)
            return True
        return False

    def stale(self) -> list[BaselineKey]:
        """Entries that matched nothing during this validation.

        Reported, never ignored. An entry going stale means the object was
        deleted, its history was rewritten, or the entry was wrong -- the first
        two are forbidden by CLAUDE.md and the third is worth knowing. Silently
        dropping them would let the baseline decay into a set of matchers nobody
        can account for.
        """
        return sorted(
            (key for key in self.entries if key not in self.seen),
            key=lambda k: (k.feature_id, k.first_revision),
        )
