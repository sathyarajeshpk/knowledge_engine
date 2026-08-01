"""The review queue: knowledge held back from minting, and how it gets through.

The mint gate (ADR-0028) holds back roughly a fifth of everything discovered.
That is only an improvement if the queue is **drainable** -- a queue nobody works
through is a slower kind of data loss than the merging it replaced. This module
is the supported way to work it.

Two rules it exists to enforce:

* **Queuing never blocks.** Every high-confidence item in a run still mints. One
  ambiguous row cannot stall a weekly harvest.
* **A queued item keeps its first discovery date.** An item found in July and
  approved in September mints under **July**. Otherwise review latency silently
  shifts a permanent identifier's month, and nothing records that it happened.

The queue lives in `state/review-queue.json` rather than in the knowledge tree,
because a queued item is not knowledge yet -- it has no Feature ID and no object.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ke.models import IdentityConfidence, Lifecycle, RawItem, is_valid_transition


@dataclass
class QueuedItem:
    """One item awaiting a human decision.

    Deliberately a flat record rather than a serialised `RawItem`: the queue is
    read by a person deciding something, and only needs what supports that
    decision. Re-discovery supplies the rest.
    """

    identity_key: str
    title: str
    summary: str
    source_name: str
    source_url: str
    announcement_url: str | None
    first_discovered_date: date
    confidence: IdentityConfidence
    reason: str
    lifecycle: Lifecycle = Lifecycle.QUEUED

    @classmethod
    def from_item(cls, item: RawItem) -> QueuedItem:
        return cls(
            identity_key=item.identity.key,
            title=item.title,
            summary=item.summary,
            source_name=item.source_name,
            source_url=item.source_url,
            announcement_url=item.announcement_url,
            # The date the knowledge appeared, not the date it was queued.
            first_discovered_date=item.first_discovered_date or item.discovered_date,
            confidence=item.identity_confidence,
            reason=item.confidence_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key,
            "title": self.title,
            "summary": self.summary,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "announcement_url": self.announcement_url,
            "first_discovered_date": self.first_discovered_date.isoformat(),
            "confidence": str(self.confidence),
            "reason": self.reason,
            "lifecycle": str(self.lifecycle),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QueuedItem:
        return cls(
            identity_key=raw["identity_key"],
            title=raw["title"],
            summary=raw.get("summary", ""),
            source_name=raw.get("source_name", ""),
            source_url=raw.get("source_url", ""),
            announcement_url=raw.get("announcement_url"),
            first_discovered_date=date.fromisoformat(raw["first_discovered_date"]),
            confidence=IdentityConfidence(raw.get("confidence", IdentityConfidence.MEDIUM)),
            reason=raw.get("reason", ""),
            lifecycle=Lifecycle(raw.get("lifecycle", Lifecycle.QUEUED)),
        )


@dataclass
class ReviewQueue:
    """Everything currently held back, keyed by identity."""

    entries: dict[str, QueuedItem]

    @classmethod
    def load(cls, path: Path) -> ReviewQueue:
        if not path.exists():
            return cls(entries={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            # Unlike `seen.json`, losing the queue loses the human decisions
            # recorded in it and the original discovery dates. Fail loudly.
            raise ValueError(f"cannot read review queue at {path}: {exc}") from exc
        return cls(
            entries={
                key: QueuedItem.from_dict(value)
                for key, value in (raw.get("entries") or {}).items()
            }
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "entries": {
                key: self.entries[key].to_dict() for key in sorted(self.entries)
            }
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    # -- working the queue -------------------------------------------------

    def enqueue(self, item: RawItem) -> bool:
        """Add an item, preserving the date it was *first* seen.

        Returns whether this was new. Re-queuing an item already present is a
        no-op on everything except nothing -- crucially it does **not** refresh
        `first_discovered_date`, or every weekly run would push a queued item's
        Feature ID month forward by a week.
        """
        existing = self.entries.get(item.identity.key)
        if existing is not None:
            return False
        self.entries[item.identity.key] = QueuedItem.from_item(item)
        return True

    def approve(self, identity_key: str) -> QueuedItem:
        """Mark an item cleared for minting on the next harvest."""
        entry = self._require(identity_key)
        if not is_valid_transition(entry.lifecycle, Lifecycle.APPROVED):
            raise ValueError(
                f"cannot approve an item that is {entry.lifecycle}"
            )
        entry.lifecycle = Lifecycle.APPROVED
        return entry

    def archive(self, identity_key: str) -> QueuedItem:
        """Retire an item from the working set. Retained, never deleted."""
        entry = self._require(identity_key)
        if not is_valid_transition(entry.lifecycle, Lifecycle.ARCHIVED):
            raise ValueError(f"cannot archive an item that is {entry.lifecycle}")
        entry.lifecycle = Lifecycle.ARCHIVED
        return entry

    def take_approved(self) -> list[QueuedItem]:
        """Approved entries, in a deterministic order, for the harvester."""
        return [
            self.entries[key]
            for key in sorted(self.entries)
            if self.entries[key].lifecycle is Lifecycle.APPROVED
        ]

    def forget(self, identity_key: str) -> None:
        """Drop an entry once it has been minted and now exists as an object."""
        self.entries.pop(identity_key, None)

    def _require(self, identity_key: str) -> QueuedItem:
        """Find one entry by key, accepting the short form a human sees.

        Identity keys are `sha256:<hex>`, and the queue displays the hex only.
        Matching therefore compares against the digest with the algorithm prefix
        stripped -- otherwise the key printed in `review-queue.md` cannot be
        pasted back into `ke review approve`, which is the entire workflow.
        """
        needle = identity_key.split(":", 1)[-1].strip()
        matches = [
            key for key in self.entries if key.split(":", 1)[-1].startswith(needle)
        ]
        if not matches:
            raise KeyError(f"no queued item matching {identity_key!r}")
        if len(matches) > 1:
            raise KeyError(
                f"{identity_key!r} matches {len(matches)} queued items; use more characters"
            )
        return self.entries[matches[0]]

    @property
    def pending(self) -> list[QueuedItem]:
        return [
            self.entries[key]
            for key in sorted(self.entries)
            if self.entries[key].lifecycle is Lifecycle.QUEUED
        ]

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for entry in self.entries.values():
            key = str(entry.lifecycle)
            tally[key] = tally.get(key, 0) + 1
        return tally
