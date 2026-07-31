"""Time, injected rather than taken.

Nothing in the engine may call `datetime.now()` or `date.today()` directly. Every
component that needs the time receives a `Clock`.

Three things follow from that, and the third is the one that matters:

* **Tests become deterministic.** A frozen clock makes "what does a run in July
  produce?" a plain assertion rather than a mock.
* **Runs become reproducible.** Two invocations with the same clock and the same
  inputs produce identical output, which is what makes deterministic ordering
  meaningful.
* **Replay becomes possible later.** Reprocessing an archived source snapshot
  *as it would have been processed at the time* requires the pipeline to accept
  the historical instant. A component that reads the system clock internally can
  never be replayed, and retrofitting that is invasive once several modules do
  it. See ADR-0025.

`Clock` is a `Protocol` rather than a base class: adapters and stages depend on
the shape, not on an inheritance chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Anything that can tell the engine what time it is."""

    def now(self) -> datetime:
        """Current instant, always timezone-aware UTC."""
        ...

    def today(self) -> date:
        """Current UTC date."""
        ...

    def run_id(self) -> str:
        """Stable identifier for the run starting at this instant.

        Appears on provenance, revisions, events and the run log, so any run can
        be reconstructed completely from what it touched.
        """
        ...


def _run_id_for(moment: datetime) -> str:
    """`run-2026-07-31T20-15-00Z`.

    Second precision is ample for a weekly job, and colons are avoided so the
    identifier is safe in a filename or a URL fragment.
    """
    return "run-" + moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


class SystemClock:
    """Reads the real clock. The only place in the engine that does."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> date:
        return self.now().date()

    def run_id(self) -> str:
        return _run_id_for(self.now())


@dataclass(frozen=True)
class FrozenClock:
    """A clock stuck at one instant.

    Used by tests, and by any future replay that reprocesses an archived
    snapshot as of the moment it was captured.
    """

    instant: datetime

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware instant")

    def now(self) -> datetime:
        return self.instant.astimezone(timezone.utc)

    def today(self) -> date:
        return self.now().date()

    def run_id(self) -> str:
        return _run_id_for(self.instant)
