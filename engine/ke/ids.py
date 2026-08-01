"""Minting permanent Feature IDs, and the registry that keeps them unique.

`MSF-2026-04-001`. Once minted, a Feature ID **never changes and is never
reused** -- including for objects later marked `replaced` (ADR-0005). Everything
in this module exists to make that survivable.

The registry (`state/id-registry.json`) holds two things:

* **per-month counters**, so backfilling an old month mints correctly dated IDs
  without disturbing the current month;
* an **id -> path map**, which is what makes "has this already been minted?"
  answerable without walking the whole pack.

The month segment comes from `RawItem.id_basis_date`: the publication month when
we trust it, else the month the item was *first* seen. Never the month a human
got round to approving it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ke.models import SEQUENCE_WIDTH, FeatureId, RawItem

#: A month holding more than this widens the sequence. Existing IDs are never
#: rewritten -- `MSF-2026-04-001` and `MSF-2026-04-1000` coexist happily.
MAX_STANDARD_SEQUENCE = 10**SEQUENCE_WIDTH - 1


class IdError(Exception):
    """The registry is inconsistent, or an ID would be reused."""


@dataclass
class IdRegistry:
    """Per-month counters plus the id -> path map, persisted as JSON.

    Loaded at the start of a harvest and written once at the end. Holding it in
    memory for the run is what makes minting a pure counter increment rather
    than a filesystem scan per item.
    """

    prefix: str
    counters: dict[str, int] = field(default_factory=dict)
    paths: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path, prefix: str) -> IdRegistry:
        """Read the registry, tolerating absence but never corruption.

        A missing file is a first run. A malformed one is a hard error: minting
        against a half-read registry is how IDs get reused, and a reused ID
        cannot be undone.
        """
        if not path.exists():
            return cls(prefix=prefix)
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IdError(f"cannot read ID registry at {path}: {exc}") from exc

        registry = cls(
            prefix=raw.get("prefix", prefix),
            counters={str(k): int(v) for k, v in (raw.get("counters") or {}).items()},
            paths={str(k): str(v) for k, v in (raw.get("paths") or {}).items()},
        )
        registry._assert_consistent(path)
        return registry

    def _assert_consistent(self, path: Path) -> None:
        """Every recorded ID must be parseable and within its month's counter.

        Catches a hand-edited or partially-written registry *before* it can mint
        a duplicate, rather than after.
        """
        for raw_id in self.paths:
            try:
                feature_id = FeatureId.parse(raw_id)
            except ValueError as exc:
                raise IdError(f"registry {path} holds a malformed ID {raw_id!r}") from exc
            counter = self.counters.get(feature_id.month_key, 0)
            if feature_id.sequence > counter:
                raise IdError(
                    f"registry {path} is inconsistent: {raw_id} exceeds the "
                    f"counter for {feature_id.month_key} ({counter}). Refusing to "
                    "mint against it -- a reused Feature ID cannot be undone."
                )

    def save(self, path: Path) -> None:
        """Write deterministically: sorted keys, trailing newline (ADR-0022)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "prefix": self.prefix,
            "counters": dict(sorted(self.counters.items())),
            "paths": dict(sorted(self.paths.items())),
        }
        text = json.dumps(payload, indent=2, sort_keys=False, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")

    # -- minting ----------------------------------------------------------

    def mint(self, item: RawItem) -> FeatureId:
        """Allocate the next ID for this item's month.

        Deliberately takes a `RawItem` rather than a date: the month must come
        from `id_basis_date`, and routing every caller through that property is
        what stops one of them quietly using `discovered_date` instead.
        """
        basis = item.id_basis_date
        month_key = f"{basis.year:04d}-{basis.month:02d}"
        sequence = self.counters.get(month_key, 0) + 1

        feature_id = FeatureId(
            prefix=self.prefix,
            year=basis.year,
            month=basis.month,
            sequence=sequence,
        )
        if str(feature_id) in self.paths:
            # Only reachable if the registry was inconsistent in a way the load
            # check missed. Fail rather than overwrite.
            raise IdError(f"refusing to reuse Feature ID {feature_id}")

        self.counters[month_key] = sequence
        return feature_id

    def record(self, feature_id: FeatureId, object_path: str) -> None:
        """Bind a minted ID to the object directory that now owns it."""
        self.paths[str(feature_id)] = object_path

    def path_for(self, feature_id: FeatureId | str) -> str | None:
        return self.paths.get(str(feature_id))

    @property
    def total_minted(self) -> int:
        return len(self.paths)

    def counter_for(self, month_key: str) -> int:
        return self.counters.get(month_key, 0)
