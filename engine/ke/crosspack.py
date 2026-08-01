"""What happens when two packs describe the same thing.

A pack is a **source boundary and an identifier namespace** (ADR-0016). Two
packs are meant to be independent: separate sources, separate `seen.json`,
separate ID registries, separate taxonomies. That independence is what makes
adding a pack a data change rather than a code change.

It also means nothing stops two packs minting two permanent Feature IDs for one
real-world feature. ADR-0016 named this and deferred it, because with one pack
it could not happen. M8 adds the second pack, so this is where it gets handled.

## Detect and report. Never merge, drop, or block.

The engine reports a cross-pack duplicate as a review item and does nothing
else. It does not pick a winner, does not rewrite either object, does not
suppress the second mint, and does not delete anything — every one of those
would either lose knowledge or make a permanent decision on a human's behalf,
and both are forbidden (CLAUDE.md, ADR-0014).

Two objects with the same canonical URL in two packs is often *correct*: an
Azure announcement that also appears in Fabric's what's-new is genuinely two
pieces of knowledge, filed under two taxonomies, useful to two different
questions. The engine has no way to tell that case from a true duplicate, so it
does not try.

## Order independence is the property that makes this trustworthy

Harvesting Azure then Fabric must produce exactly the same duplicate report as
Fabric then Azure. That is not automatic — a naive implementation that checked
"is this already in another pack?" during minting would depend entirely on which
pack ran first, and would suppress a mint in one order and not the other.

So detection is **not a pipeline stage**. It reads what is on disk *after* all
packs have harvested, groups by identity, and reports. It has no access to run
order because it never sees one. `find_duplicates` sorts its inputs and
canonicalises every pair, so the output is byte-identical whichever way round
you run things (ADR-0022, ADR-0044).

The consequence worth stating: **Feature IDs are unaffected by pack order.**
Each pack mints from its own registry, from its own counter, for its own items.
Nothing here changes that, which is precisely why nothing here can corrupt it.

## Where a resolution lives

A cross-pack duplicate belongs to neither pack, so acknowledging one cannot be
stored in either without the two copies drifting. `state/cross-pack.json` at the
repository root is the first genuinely repo-level state this project has needed.

Resolving from either side clears it for both, because the pair key is
canonical — sorted Feature IDs — rather than "the one I was looking at".
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ke.models import KnowledgeObject
from ke.pack import Pack

#: Repo-level state. Not inside any pack: a fact about two packs belongs to
#: neither, and storing it in both is how two copies come to disagree.
CROSS_PACK_STATE = Path("state") / "cross-pack.json"


@dataclass(frozen=True)
class DuplicateSide:
    """One object's half of a cross-pack pair, with the evidence for it."""

    pack_name: str
    feature_id: str
    title: str
    url: str
    source_name: str
    identity_basis: str
    published: str
    subpath: str


@dataclass(frozen=True)
class DuplicatePair:
    """Two objects in different packs that share an identity.

    Frozen and canonically ordered, so the same pair is the same value however
    it was discovered.
    """

    key: str
    basis: str
    sides: tuple[DuplicateSide, ...]

    @property
    def packs(self) -> tuple[str, ...]:
        return tuple(side.pack_name for side in self.sides)

    @property
    def feature_ids(self) -> tuple[str, ...]:
        return tuple(side.feature_id for side in self.sides)

    @property
    def short_key(self) -> str:
        """What a human types to resolve it."""
        return "+".join(self.feature_ids)

    def involves(self, pack_name: str) -> bool:
        return pack_name in self.packs

    def other_side(self, pack_name: str) -> DuplicateSide | None:
        for side in self.sides:
            if side.pack_name != pack_name:
                return side
        return None


def _sides_for(pack: Pack, obj: KnowledgeObject) -> DuplicateSide:
    return DuplicateSide(
        pack_name=pack.name,
        feature_id=str(obj.id),
        title=obj.title,
        url=obj.announcement_url or obj.source_url,
        source_name=obj.source_name,
        identity_basis=str(obj.provenance.identity_basis) if obj.provenance else "unknown",
        published=str(obj.published_date or obj.discovered_date),
        subpath=obj.knowledge_subpath,
    )


def find_duplicates(packs: list[Pack]) -> list[DuplicatePair]:
    """Every identity shared across two or more packs, deterministically ordered.

    Grouped on `url_hash` first and `content_hash` second — the same two layers
    `dedupe.py` uses within a pack, applied across packs. A URL match is a
    strong claim; a content match is a weaker one and is reported as such.

    Order independence comes from three places, all of them deliberate:

    1. Packs are sorted by name before anything is read.
    2. Objects within a pack are already returned in sorted path order.
    3. Each pair's sides are sorted by Feature ID.

    So the result depends on what is on disk, never on what ran first.
    """
    from ke.harvest import load_objects_with_dirs

    by_url: dict[str, list[DuplicateSide]] = defaultdict(list)
    by_content: dict[str, list[DuplicateSide]] = defaultdict(list)

    for pack in sorted(packs, key=lambda p: p.name):
        for obj, _ in load_objects_with_dirs(pack):
            side = _sides_for(pack, obj)
            if obj.url_hash:
                by_url[obj.url_hash].append(side)
            if obj.content_hash:
                by_content[obj.content_hash].append(side)

    pairs: list[DuplicatePair] = []
    seen_keys: set[str] = set()

    for basis, groups in (("canonical-url", by_url), ("content-fingerprint", by_content)):
        for key, sides in groups.items():
            if len({side.pack_name for side in sides}) < 2:
                continue
            ordered = tuple(sorted(sides, key=lambda s: (s.feature_id, s.pack_name)))
            identity = "+".join(side.feature_id for side in ordered)
            # A pair matching on URL also matches on content. Report the
            # stronger basis once rather than the same pair twice.
            if identity in seen_keys:
                continue
            seen_keys.add(identity)
            pairs.append(DuplicatePair(key=key, basis=basis, sides=ordered))

    pairs.sort(key=lambda p: (p.basis, p.short_key))
    return pairs


# ---------------------------------------------------------------------------
# Resolutions
# ---------------------------------------------------------------------------


@dataclass
class Resolutions:
    """Cross-pack duplicates a human has already looked at.

    Append-only in spirit: an acknowledgement is never removed by the engine,
    because re-surfacing a decision somebody already made every Sunday is how a
    review queue teaches people to ignore it.
    """

    acknowledged: dict[str, dict]

    @classmethod
    def load(cls, repo_root: Path) -> Resolutions:
        path = Path(repo_root) / CROSS_PACK_STATE
        if not path.is_file():
            return cls(acknowledged={})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # Degrade rather than stop: the worst case is re-showing a decision
            # somebody already made, which is annoying rather than harmful.
            # Contrast the ID registry, where guessing is unrecoverable.
            return cls(acknowledged={})
        return cls(acknowledged=dict(raw.get("acknowledged") or {}))

    def save(self, repo_root: Path) -> Path:
        path = Path(repo_root) / CROSS_PACK_STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Cross-pack duplicates a human has reviewed. Acknowledging one "
                "records a decision; it does not modify either knowledge object."
            ),
            "acknowledged": {k: self.acknowledged[k] for k in sorted(self.acknowledged)},
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        return path

    def is_acknowledged(self, pair: DuplicatePair) -> bool:
        return pair.short_key in self.acknowledged

    def acknowledge(self, pair: DuplicatePair, *, today: date, note: str = "") -> None:
        """Record that a human has seen this pair and made a decision.

        Stores the evidence alongside the acknowledgement, so the record still
        means something if one of the objects is later superseded or retitled.
        """
        self.acknowledged[pair.short_key] = {
            "basis": pair.basis,
            "acknowledged_on": str(today),
            "note": note,
            "objects": [
                {
                    "pack": side.pack_name,
                    "id": side.feature_id,
                    "title": side.title,
                    "url": side.url,
                }
                for side in pair.sides
            ],
        }


def outstanding(packs: list[Pack], repo_root: Path) -> list[DuplicatePair]:
    """Cross-pack duplicates nobody has acknowledged yet."""
    resolutions = Resolutions.load(repo_root)
    return [p for p in find_duplicates(packs) if not resolutions.is_acknowledged(p)]


# ---------------------------------------------------------------------------
# Cross-pack references
# ---------------------------------------------------------------------------


def known_feature_ids(packs: list[Pack]) -> set[str]:
    """Every Feature ID in every pack, for referential integrity.

    M8 permits relationships to point across packs — an Azure object may
    legitimately be a prerequisite for a Fabric one. Validation therefore has to
    resolve a reference against the whole repository rather than one pack, or
    every cross-pack link would be reported as dangling.
    """
    from ke.harvest import load_objects_with_dirs

    return {
        str(obj.id)
        for pack in packs
        for obj, _ in load_objects_with_dirs(pack)
    }


def dangling_references(packs: list[Pack]) -> list[tuple[str, str, str]]:
    """`(feature_id, field, missing_id)` for every reference that resolves nowhere.

    Checked across all packs at once, so a cross-pack reference is valid and a
    typo is still caught.
    """
    from ke.harvest import load_objects_with_dirs

    known = known_feature_ids(packs)
    problems: list[tuple[str, str, str]] = []
    for pack in sorted(packs, key=lambda p: p.name):
        for obj, _ in load_objects_with_dirs(pack):
            for field in ("prerequisites", "builds_on", "related_topics"):
                for reference in getattr(obj, field, ()) or ():
                    if reference not in known:
                        problems.append((str(obj.id), field, reference))
            for field in ("replaced_by", "replaces"):
                reference = getattr(obj, field, None)
                if reference and reference not in known:
                    problems.append((str(obj.id), field, reference))
    return sorted(set(problems))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_evidence(pair: DuplicatePair) -> str:
    """Everything a human needs to decide, without opening either file.

    A review item that says "these two might be duplicates" and nothing else
    makes the reader do the research the engine already did.
    """
    lines = [
        f"Cross-pack duplicate · matched on {pair.basis}",
        f"  shared identity: {pair.key}",
        "",
    ]
    for side in pair.sides:
        lines += [
            f"  [{side.pack_name}] {side.feature_id}",
            f"      title:     {side.title}",
            f"      url:       {side.url}",
            f"      source:    {side.source_name} · identity: {side.identity_basis}",
            f"      published: {side.published}",
            f"      path:      {side.subpath}",
            "",
        ]
    lines += [
        "  Both objects are kept. The engine does not choose between them.",
        "",
        "  Same feature reported by two sources → acknowledge and move on:",
        f"    ke review resolve {pair.short_key}",
        "  Genuinely the same knowledge, one should win → link them explicitly:",
        f"    ke supersede {pair.feature_ids[0]} --by {pair.feature_ids[1]}",
        "",
    ]
    return "\n".join(lines)
