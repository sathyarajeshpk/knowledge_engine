"""A per-pack lock, so two harvests cannot mint over each other.

The failure this prevents: two runs load the ID registry at the same moment,
both see counter 41, and both mint `...-042`. The registry's consistency check
(ADR-0032) would notice the damage afterwards, but a **permanent duplicate
Feature ID cannot be undone** -- detection is not enough.

The workflow's `concurrency` group is the first line of defence and covers the
scheduled case. This covers the rest: a manual `ke harvest` while the cron is
running, two terminals, a retry started before the first finished.

Deliberately simple. `O_CREAT | O_EXCL` is atomic on every filesystem this will
run on, needs no dependency, and leaves a file a human can read and delete.
"""

from __future__ import annotations

import errno
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


class LockError(Exception):
    """Another harvest holds the lock."""


#: A lock older than this is treated as abandoned. Longer than any real harvest
#: (the live pack takes seconds) and short enough that a crashed run does not
#: block the next week's schedule.
STALE_AFTER_SECONDS = 3600


@contextmanager
def pack_lock(state_dir: Path, *, holder: str = "", now: float | None = None):
    """Hold an exclusive lock for one pack, releasing it even on failure.

    A stale lock is reclaimed rather than respected: a crashed run must not
    silently disable the weekly harvest for good. The reclaim is logged into the
    lock file so the next reader can see it happened.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / ".harvest.lock"
    now = now if now is not None else time.time()

    reclaimed = _reclaim_if_stale(path, now)

    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
        raise LockError(
            f"another harvest is running (lock: {path}). "
            "If you are sure it is not, delete that file."
        ) from exc

    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(
                {"holder": holder, "acquired_at": now, "pid": os.getpid(),
                 "reclaimed_stale": reclaimed},
                stream,
            )
        yield path
    finally:
        # Released in a finally so a crash inside the harvest does not leave the
        # pack locked until the staleness timeout.
        path.unlink(missing_ok=True)


def _reclaim_if_stale(path: Path, now: float) -> bool:
    if not path.exists():
        return False
    try:
        age = now - json.loads(path.read_text(encoding="utf-8")).get("acquired_at", 0)
    except (OSError, ValueError):
        # An unreadable lock is worse than no lock: nothing can prove it is
        # live, and respecting it forever would disable the pack.
        path.unlink(missing_ok=True)
        return True
    if age > STALE_AFTER_SECONDS:
        path.unlink(missing_ok=True)
        return True
    return False
