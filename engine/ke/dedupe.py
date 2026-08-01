"""Deciding whether an item is already known.

Three layers, strongest first. The rule underneath all of them (ADR-0014):

    A near-duplicate is flagged, never dropped.

Layers 1 and 2 are exact -- when they match, it *is* the same item, and treating
it as such is correct. Layer 3 is a judgement, so it never removes anything: it
marks `needs_review` and lets a human decide. Silently dropping knowledge because
two titles looked similar would violate "never delete existing knowledge" in
spirit, and unlike a duplicate, a wrong drop leaves no trace.

`seen.json` persists layers 1 and 2 between runs, which is what makes the second
harvest of an unchanged source produce zero new objects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ke.acquisition.identity import normalise_title
from ke.models import RawItem
from ke.normalize import content_hash


class Verdict:
    """Why an item was or was not considered new."""

    NEW = "new"
    KNOWN_IDENTITY = "known-identity"
    KNOWN_CONTENT = "known-content"
    NEAR_DUPLICATE = "near-duplicate"


@dataclass(frozen=True)
class Decision:
    """One deduplication outcome, with the reason attached."""

    item: RawItem
    verdict: str
    #: The Feature ID or identity key this matched, when it matched something.
    matched: str | None = None
    #: Set for near-duplicates: how similar, and to what.
    similarity: float = 0.0

    @property
    def is_new(self) -> bool:
        return self.verdict in (Verdict.NEW, Verdict.NEAR_DUPLICATE)

    @property
    def needs_review(self) -> bool:
        return self.verdict is Verdict.NEAR_DUPLICATE


def jaccard(left: str, right: str) -> float:
    """Token-set similarity of two normalised titles.

    Deliberately crude and deliberately deterministic. A cleverer measure would
    need tuning, and a tuned threshold on permanent identifiers is a dial
    somebody eventually turns.
    """
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class SeenIndex:
    """What previous runs already stored, keyed for each exact layer.

    Persisted so a weekly run does not have to re-read every knowledge object to
    answer "have I seen this?".
    """

    identities: dict[str, str] = field(default_factory=dict)   # identity key -> Feature ID
    contents: dict[str, str] = field(default_factory=dict)     # content hash -> Feature ID
    titles: dict[str, str] = field(default_factory=dict)       # normalised title -> Feature ID

    @classmethod
    def load(cls, path: Path) -> SeenIndex:
        if not path.exists():
            return cls()
        try:
            raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Unlike the ID registry, a damaged seen-index is recoverable: the
            # worst case is re-examining items we already have, and layer 1 will
            # rebuild as objects are re-encountered. Losing it is not losing
            # knowledge, so this degrades rather than fails the run.
            return cls()
        return cls(
            identities=dict(raw.get("identities") or {}),
            contents=dict(raw.get("contents") or {}),
            titles=dict(raw.get("titles") or {}),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "identities": dict(sorted(self.identities.items())),
            "contents": dict(sorted(self.contents.items())),
            "titles": dict(sorted(self.titles.items())),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def remember(self, item: RawItem, feature_id: str) -> None:
        self.identities[item.identity.key] = feature_id
        self.contents[content_hash(item.title, item.summary)] = feature_id
        self.titles[normalise_title(item.title)] = feature_id


def classify(
    items: list[RawItem],
    seen: SeenIndex,
    *,
    near_duplicate_threshold: float = 0.85,
) -> list[Decision]:
    """Sort items into new, already-known and possibly-duplicate.

    Also de-duplicates *within* the run: the same feature legitimately appears
    twice on one page (measured: 49 such pairs on the live Fabric source), and
    both rows would otherwise mint separate IDs in a single harvest.
    """
    decisions: list[Decision] = []
    # Layers 1 and 2 for this run, seeded from previous runs.
    identities = dict(seen.identities)
    contents = dict(seen.contents)
    titles = dict(seen.titles)

    for item in items:
        fingerprint = content_hash(item.title, item.summary)
        normalised = normalise_title(item.title)

        # Layer 1 -- identity. The strongest signal, and the one M1 spent the
        # milestone making trustworthy.
        if item.identity.key in identities:
            decisions.append(
                Decision(item, Verdict.KNOWN_IDENTITY, identities[item.identity.key])
            )
            continue

        # Layer 2 -- content fingerprint. Catches republication at a new URL,
        # where the identity changed but the knowledge did not.
        if fingerprint in contents:
            decisions.append(Decision(item, Verdict.KNOWN_CONTENT, contents[fingerprint]))
            continue

        # Layer 3 -- near-duplicate. A judgement, so it flags and never drops.
        best_match, best_score = None, 0.0
        for known_title, owner in titles.items():
            score = jaccard(normalised, known_title)
            if score > best_score:
                best_match, best_score = owner, score

        verdict = (
            Verdict.NEAR_DUPLICATE
            if best_score >= near_duplicate_threshold
            else Verdict.NEW
        )
        decisions.append(
            Decision(
                item,
                verdict,
                best_match if verdict is Verdict.NEAR_DUPLICATE else None,
                best_score if verdict is Verdict.NEAR_DUPLICATE else 0.0,
            )
        )

        # Provisionally claim this item's keys so a later item in the same run
        # matches against it. The Feature ID is not known yet, so the placeholder
        # is replaced by `remember()` once minting happens.
        identities[item.identity.key] = "pending"
        contents[fingerprint] = "pending"
        if normalised:
            titles[normalised] = "pending"

    return decisions


def summarise(decisions: list[Decision]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for decision in decisions:
        tally[decision.verdict] = tally.get(decision.verdict, 0) + 1
    return tally
