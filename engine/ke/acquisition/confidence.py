"""Deciding whether an identity is trustworthy enough to mint from.

Recommendation C, from `docs/design/IDENTITY_MODEL.md`. Two jobs:

1. **Assess** each item's `IdentityConfidence` from deterministic evidence.
2. **Detect collisions** -- one identity resolving to several distinct features --
   and surface them for review instead of merging them silently.

The rule this module exists to enforce:

    A collision is never a merge.

Measured against the live Fabric source, one announcement URL is cited by 18
rows covering 11 genuinely different features. Under the old behaviour those
collapsed into one identity, and M2 would have minted **one** permanent Feature
ID with the other ten features silently absent -- not flagged, not queued,
absent. Feature IDs are never reused, so that damage could never be undone.

Everything here is pure: same items in, same assessment out. No clock, no
network, no model (ADR-0004). Confidence is recomputed every run and never
enters a Feature ID, which is exactly why it may use run-scoped evidence that
identity may not -- see `IdentityConfidence` and IDENTITY_MODEL.md §6.1.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ke.acquisition.identity import IdentityBasis, normalise_title
from ke.models import IdentityConfidence, Lifecycle, RawItem

#: Bases durable enough to anchor a permanent identifier (ADR-0023 ranks 1-2).
DURABLE_BASES = (IdentityBasis.CANONICAL_URL, IdentityBasis.SOURCE_IDENTIFIER)


@dataclass(frozen=True)
class Collision:
    """One identity claimed by several distinct features.

    Not an error and not a duplicate: it means the source reported several
    features in one announcement and our anchor cannot tell them apart. A human
    decides. Until then nothing is minted and nothing is lost.
    """

    identity_key: str
    announcement_url: str | None
    #: The features' titles **as published**, not normalised. Normalised titles
    #: decide *whether* this is a collision; a human triaging it needs to read
    #: the real ones.
    titles: tuple[str, ...]

    @property
    def feature_count(self) -> int:
        return len(self.titles)


def census(items: list[RawItem]) -> dict[str, set[str]]:
    """Map each identity key to the distinct feature titles claiming it.

    Computed across the **whole run** rather than per source: the same
    announcement can legitimately be cited by two different sources, and whether
    two rows describe one feature is a property of the knowledge, not of which
    adapter happened to find it.
    """
    seen: dict[str, set[str]] = {}
    for item in items:
        seen.setdefault(item.identity.key, set()).add(normalise_title(item.title))
    return seen


def assess(item: RawItem, seen: dict[str, set[str]]) -> tuple[IdentityConfidence, str]:
    """Grade one item's identity. Returns the level and the reason for it.

    The reason is not decoration. A person triaging the review queue should not
    have to re-derive the rule that put an item there.
    """
    basis = item.identity.basis
    titles = seen.get(item.identity.key, {normalise_title(item.title)})

    if basis is IdentityBasis.CONTENT_FINGERPRINT:
        return (
            IdentityConfidence.LOW,
            "identity rests on a content fingerprint, which changes whenever the "
            "text does; nothing durable to mint from",
        )

    if not normalise_title(item.title) and basis not in DURABLE_BASES:
        return (
            IdentityConfidence.LOW,
            "no durable anchor and no usable title",
        )

    if basis not in DURABLE_BASES:
        return (
            IdentityConfidence.MEDIUM,
            f"identity rests on {basis}, which does not survive rewording; "
            "no announcement URL could be resolved",
        )

    if len(titles) > 1:
        return (
            IdentityConfidence.MEDIUM,
            f"this announcement is cited by {len(titles)} distinct features, so "
            "the URL identifies the announcement rather than this feature",
        )

    return (
        IdentityConfidence.HIGH,
        f"durable anchor ({basis}) and this announcement reports one feature",
    )


def apply(items: list[RawItem]) -> list[RawItem]:
    """Grade every item, and advance it out of `DISCOVERED`.

    Adapters cannot do this themselves: exclusivity is only knowable once the
    whole run is visible, and an adapter sees one source at a time.

    Grading is the moment acquisition branches. An item either clears the gate
    and becomes `APPROVED`, or it becomes `QUEUED` — never dropped, and never
    left ambiguous. `discover_all` calls this on every run.
    """
    seen = census(items)
    graded = []
    for item in items:
        level, reason = assess(item, seen)
        assessed = replace(
            item, identity_confidence=level, confidence_reason=reason
        )
        graded.append(
            replace(
                assessed,
                lifecycle=(
                    Lifecycle.APPROVED
                    if assessed.mints_automatically
                    else Lifecycle.QUEUED
                ),
            )
        )
    return graded


def collisions(items: list[RawItem]) -> list[Collision]:
    """Every identity claimed by more than one distinct feature, sorted."""
    seen = census(items)
    announcement_of: dict[str, str | None] = {}
    # One published title per distinct normalised title, so the report shows a
    # human-readable name for each feature rather than repeating near-duplicates.
    published: dict[str, dict[str, str]] = {}
    for item in items:
        announcement_of.setdefault(item.identity.key, item.announcement_url)
        published.setdefault(item.identity.key, {}).setdefault(
            normalise_title(item.title), item.title
        )

    found = [
        Collision(
            identity_key=key,
            announcement_url=announcement_of.get(key),
            titles=tuple(sorted(published[key][t] for t in titles)),
        )
        for key, titles in seen.items()
        if len(titles) > 1
    ]
    # Deterministic order, worst first: a human triaging should meet the
    # announcement hiding the most knowledge before the one hiding two rows.
    return sorted(found, key=lambda c: (-c.feature_count, c.identity_key))


def summarise(items: list[RawItem]) -> dict[IdentityConfidence, int]:
    """Counts per level, for `ke discover` and the weekly digest."""
    tally = {level: 0 for level in IdentityConfidence}
    for item in items:
        tally[item.identity_confidence] += 1
    return tally
