"""Deciding what makes two discovered items *the same item*.

This module exists to prevent one specific, unrecoverable failure.

The primary source is a web page whose updates are table rows. Rows carry no
identifier. If Microsoft reorders the table, rewords a heading, or changes its
markup, a naive matcher sees new rows, and M2 mints **new permanent Feature
IDs** for knowledge that was already stored. Feature IDs are never reused and
never renumbered (ADR-0005), so duplicates created this way can never be cleaned
up.

The rule: **a presentation-layer change must never create a new identity.**

So identity is computed from the most durable signal available, in a fixed
order, and the engine records *which* signal it used so a future investigation
does not have to guess.
"""

from __future__ import annotations

import hashlib
import re

from ke.models import IdentityBasis, ItemIdentity

#: Tracking and session parameters that change per visit and never identify an
#: article. Stripping them is what makes the canonical URL stable.
TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "wt.mc_id", "ocid", "cid", "culture", "country", "fbclid", "gclid",
        "msclkid", "src", "ref", "referrer", "s_cid", "epi", "irgwc", "irclickid",
    }
)

#: Words dropped before hashing a title. Microsoft rewords announcements
#: constantly ("Announcing X" -> "X is now generally available"), and a title
#: hash that moves on rewording defeats the purpose of having one.
#:
#: Lifecycle words -- `preview`, `ga`, `generally`, `availability` -- are dropped
#: too, so "X in preview" and "X generally available" collapse to one identity.
#: That is correct under ADR-0009: one Feature ID per concept, with the GA
#: announcement as a revision rather than a second object.
#:
#: The list stops at nouns and adjectives. Verbs are **not** removed, so
#: "X enters preview" and "X reaches GA" still differ. Chasing every lifecycle
#: verb would mean an ever-growing list that steadily raises the risk of two
#: genuinely different features colliding -- a far worse failure than missing a
#: match. This limitation is exactly why the title hash sits third in the
#: hierarchy rather than first: it is a fallback, not a guarantee.
TITLE_NOISE = frozenset(
    {
        "announcing", "announcement", "announces", "introducing", "new", "now",
        "is", "are", "the", "a", "an", "of", "for", "to", "in", "and", "with",
        "available", "availability", "general", "generally", "ga", "preview",
        "public", "update", "updates", "support", "supports",
    }
)

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalise_title(title: str) -> str:
    """Reduce a title to its stable words, sorted.

    Lower-cased, punctuation removed, marketing noise dropped, then **sorted** so
    that "Direct Lake is now generally available" and "Announcing general
    availability of Direct Lake" reduce to the same key.

    Sorting is what makes this robust to rewording. It also makes the key
    meaningless to read, which is why `raw_value` is retained.
    """
    words = _NON_WORD.sub(" ", title.lower()).split()
    meaningful = sorted(word for word in words if word not in TITLE_NOISE)
    return " ".join(meaningful)


def compute_identity(
    *,
    canonical_url: str | None = None,
    source_identifier: str | None = None,
    title: str = "",
    summary: str = "",
) -> ItemIdentity:
    """Establish an item's identity using the strongest signal available.

    The order is fixed and deliberate:

    1. **Canonical URL** -- survives everything except the article moving.
    2. **Source identifier** -- a stable id published by the source.
    3. **Normalised title hash** -- survives markup change, not rewording.
    4. **Content fingerprint** -- last resort.

    Raises `ValueError` if nothing usable is supplied: an item with no
    identifiable identity must be rejected loudly, never given a fabricated one.
    """
    if canonical_url:
        return ItemIdentity(IdentityBasis.CANONICAL_URL, _digest(canonical_url), canonical_url)

    if source_identifier:
        return ItemIdentity(
            IdentityBasis.SOURCE_IDENTIFIER, _digest(source_identifier), source_identifier
        )

    normalised = normalise_title(title)
    if normalised:
        return ItemIdentity(IdentityBasis.TITLE_HASH, _digest(normalised), normalised)

    fingerprint = " ".join(f"{title} {summary}".split()).strip()
    if fingerprint:
        return ItemIdentity(
            IdentityBasis.CONTENT_FINGERPRINT, _digest(fingerprint), fingerprint
        )

    raise ValueError("cannot establish identity: no URL, identifier, title or summary")
