"""Independent audit signals. **Not** validation warnings.

Nothing here emits a `Finding`, and nothing here is wired into `ke validate`.
That separation is the whole point of the module.

## Why this is not part of REV002

M9 needed to answer "how many objects are genuinely damaged?" without asking the
detector under test, which would have been circular. The answer came from
`run_id`: two revisions on one object bearing the same run identifier mean one
run wrote twice, and a run can observe a given object change **at most once**,
because it holds one view of the source and one view of storage.

That signal turned out to be exactly set-identical to the corrected REV002
detection over the historical data — 35 objects, both differences empty. Which
is precisely why it must stay separate: a validating oracle that has been folded
into the thing it validates has stopped being an oracle.

## The two mechanisms have different jobs, permanently

| | Responsibility |
|---|---|
| **REV002** (`validate.py`) | **Future correctness.** Detects the flip-flop *symptom* — a value oscillating between runs — even when later legitimate revisions follow. Runs on every validation. |
| **`duplicate_write_objects`** (here) | **Historical identification and independent validation.** Detects the duplicate-write *mechanism*. Used to derive the historical set, to validate REV002 without circularity, and to key the eventual grandfather baseline. |

**They are not expected to remain identical.** They agree on today's data
because one mechanism produced all of today's damage. They diverge on future
data by design:

* A genuine slow oscillation spread across separate runs, one revision each, is
  a REV002 symptom and is **invisible** to the duplicate-write signal.
* A duplicate write that happened to record different `changed_fields` each time
  is a duplicate write and is **invisible** to REV002.

Anyone who later finds these two disagreeing has found a real difference, not a
bug in one of them.

## What a non-empty result means now

The duplicate-write defect that produced the historical damage was fixed in M9-3
(`update_existing` now applies at most one update per stored object per run,
whichever identity layer matched). So on data written after that fix, this
function should return **nothing**.

If it ever returns something for a run that postdates M9-3, that is evidence of
a **new duplicate-write defect** — not an ordinary REV002 symptom, and not
something to grandfather.
"""

from __future__ import annotations

from dataclasses import dataclass

from ke.models import KnowledgeObject
from ke.pack import Pack


@dataclass(frozen=True)
class DuplicateWrite:
    """One run that appended more than one revision to a single object."""

    feature_id: str
    pack_name: str
    run_id: str
    revisions: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.revisions)


def duplicate_writes(obj: KnowledgeObject, pack_name: str = "") -> list[DuplicateWrite]:
    """Every run that wrote this object more than once, in revision order.

    Reads `run_id` and nothing else. In particular it never inspects
    `changed_fields`, which is what keeps it usable as an oracle for a detector
    built on `changed_fields`.
    """
    by_run: dict[str, list[int]] = {}
    for revision in obj.revisions:
        if revision.run_id is None:
            # A revision with no run identity cannot be attributed, so it is not
            # evidence either way. Silently skipped rather than guessed at.
            continue
        by_run.setdefault(revision.run_id, []).append(revision.revision)

    return [
        DuplicateWrite(
            feature_id=str(obj.id),
            pack_name=pack_name,
            run_id=run_id,
            revisions=tuple(sorted(revisions)),
        )
        for run_id, revisions in sorted(by_run.items())
        if len(revisions) > 1
    ]


def duplicate_write_objects(packs: list[Pack]) -> dict[str, list[DuplicateWrite]]:
    """Feature ID → duplicate writes, for every affected object in every pack.

    The keys of this mapping are the historical damage set. Keyed by Feature ID
    rather than counted, because a grandfather baseline built on a *number* is a
    baseline that silently accepts a different 35 (M9 Gate C, §5).
    """
    from ke.harvest import load_objects_with_dirs

    found: dict[str, list[DuplicateWrite]] = {}
    for pack in sorted(packs, key=lambda p: p.name):
        for obj, _ in load_objects_with_dirs(pack):
            writes = duplicate_writes(obj, pack.name)
            if writes:
                found[str(obj.id)] = writes
    return found
