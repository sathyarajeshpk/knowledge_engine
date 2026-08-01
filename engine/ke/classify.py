"""Proposing tier, priority, category, difficulty and workload — deterministically.

Rules live in `pack.yml` as data, never in code, so the vocabulary can be tuned
without a release and a second Domain Pack needs no engine change (ADR-0010).

Three properties this module must have, in order of how expensive they are to
lose:

1. **Deterministic.** Same object and same rules produce the same classification,
   every run, forever. Anything else and a weekly harvest rewrites objects at
   random, which destroys the one signal the git diff carries.
2. **Explainable.** Every proposal records which rule produced it. "Why is this
   tier 1?" must be answerable from the stored object, not by re-running the
   engine and reading code.
3. **Never a silent guess.** An object matching no rule gets `tier: 3` and
   `needs_review: true` rather than a plausible-looking default. A wrong
   confident answer is worse than an admitted gap.

There is no AI here and there never will be (ADR-0004). Classification is
keyword and pattern matching over text the engine already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ke.models import (
    Difficulty,
    KnowledgeObject,
    LearningPriority,
    Tier,
    Workload,
)

#: Release-wave phrasing Microsoft uses, e.g. "2026 Release Wave 1".
RELEASE_WAVE = re.compile(r"\b(20\d{2})\s+release\s+wave\s+([12])\b", re.IGNORECASE)


@dataclass(frozen=True)
class Proposal:
    """What classification decided, and which rule decided it.

    `matched_by` is not decoration. Without it, disagreeing with a
    classification means reading the rules *and* the engine to work out which
    one fired -- and the whole point of rules-as-data is that a human can adjust
    them without doing that.
    """

    field: str
    value: Any
    matched_by: str

    def __str__(self) -> str:
        return f"{self.field}={self.value} ({self.matched_by})"


def _haystack(obj: KnowledgeObject) -> str:
    """The text rules match against: title, summary-ish fields and source tags.

    Lower-cased once here so every rule is case-insensitive without each having
    to remember.

    Deliberately excludes **`category` and `tags`** even though they are text on
    the object: they are classification's own output, and feeding them back in
    makes the result depend on whether classification has run before. Measured:
    with them included, a second harvest reclassified four more objects than the
    first. Classification must be a pure function of the knowledge.

    Also excludes the full article text -- we do not store it (ADR-0003), and
    rules depending on it would behave differently for items whose summaries were
    truncated at different lengths.
    """
    parts = [obj.title, obj.slug]
    latest = obj.revisions[-1] if obj.revisions else None
    if latest is not None and latest.summary_snapshot:
        parts.append(latest.summary_snapshot)
    return " ".join(parts).lower()


def _match_rule(haystack: str, rule: dict[str, Any]) -> bool:
    """Whether one rule fires.

    A rule matches when **any** of its `any` terms appear and **none** of its
    `none` terms do. Both lists are plain substrings rather than regexes: a
    pack author should not need to know regex escaping, and a malformed pattern
    should not be able to break a harvest.
    """
    terms = [str(t).lower() for t in (rule.get("any") or [])]
    excluded = [str(t).lower() for t in (rule.get("none") or [])]
    if excluded and any(term in haystack for term in excluded):
        return False
    if not terms:
        return False
    return any(term in haystack for term in terms)


def _first_match(
    haystack: str, rules: list[dict[str, Any]], field: str
) -> Proposal | None:
    """First matching rule wins, in the order the pack author wrote them.

    Order is the pack author's priority statement. Scoring or "best match" would
    be cleverer and would make the outcome depend on the whole rule set, so
    adding an unrelated rule could silently reclassify existing objects.
    """
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            continue
        if _match_rule(haystack, rule):
            value = rule.get("value")
            if value is None:
                continue
            name = rule.get("name") or f"{field}[{index}]"
            return Proposal(field=field, value=value, matched_by=str(name))
    return None


def extract_version(obj: KnowledgeObject) -> str | None:
    """Pull a release wave out of the text, if the source names one.

    Normalised to Microsoft's own phrasing so two spellings of the same wave do
    not produce two different values -- which would otherwise churn the diff.
    """
    match = RELEASE_WAVE.search(_haystack(obj))
    if match is None:
        return None
    return f"{match.group(1)} Release Wave {match.group(2)}"


def reading_time(obj: KnowledgeObject) -> int:
    """Minutes, rounded up, minimum 1."""
    latest = obj.revisions[-1] if obj.revisions else None
    text = f"{obj.title} {latest.summary_snapshot if latest else ''}"
    return max(1, -(-len(text.split()) // 200))


def propose(obj: KnowledgeObject, rules: dict[str, Any]) -> list[Proposal]:
    """Everything classification would like to set on this object.

    Returns proposals rather than applying them. Whether each one is *allowed*
    to land is a separate question -- the object may have locked the field via
    `overrides`, or already carry a value -- and keeping that decision out of
    here means the rules cannot accidentally acquire the power to overwrite.
    """
    haystack = _haystack(obj)
    proposals: list[Proposal] = []

    for field, options in (
        ("category", rules.get("category") or []),
        ("tier", rules.get("tier") or []),
        ("learning_priority", rules.get("learning_priority") or []),
        ("difficulty", rules.get("difficulty") or []),
        ("workload", rules.get("workload") or []),
    ):
        proposal = _first_match(haystack, options, field)
        if proposal is not None:
            proposals.append(_coerce(proposal))

    tags = _propose_tags(haystack, rules.get("tags") or [])
    if tags:
        proposals.append(Proposal("tags", tags, "tags rules"))

    version = extract_version(obj)
    if version:
        proposals.append(Proposal("version", version, "release-wave pattern"))

    proposals.append(Proposal("reading_time", reading_time(obj), "word count"))
    return proposals


def _propose_tags(haystack: str, rules: list[dict[str, Any]]) -> tuple[str, ...]:
    """Every matching tag rule contributes. Sorted, so order is never a diff."""
    found = {
        str(rule["value"])
        for rule in rules
        if isinstance(rule, dict) and rule.get("value") and _match_rule(haystack, rule)
    }
    return tuple(sorted(found))


def _coerce(proposal: Proposal) -> Proposal:
    """Turn a YAML scalar into the enum the model expects.

    A pack author writes `value: 1` or `value: high`; the model wants `Tier.ACT_NOW`
    or `LearningPriority.HIGH`. An unconvertible value is dropped rather than
    stored raw, because a bad rule should degrade one field rather than write a
    value that fails validation for every object it touches.
    """
    converters = {
        "tier": lambda v: Tier(int(v)),
        "learning_priority": LearningPriority,
        "difficulty": Difficulty,
        "workload": Workload,
    }
    convert = converters.get(proposal.field)
    if convert is None:
        return proposal
    try:
        return Proposal(proposal.field, convert(proposal.value), proposal.matched_by)
    except (ValueError, TypeError):
        return Proposal(proposal.field, None, f"{proposal.matched_by} (invalid value)")


def applicable(obj: KnowledgeObject, proposals: list[Proposal]) -> dict[str, Any]:
    """Filter proposals down to what the engine is actually allowed to write.

    Two reasons a proposal is dropped, and both matter:

    * **The user locked the field** by naming it in `overrides`. Their judgement
      outranks the rules, permanently.
    * **The field already has a value.** Engine-proposed fields are written only
      when absent (ADR-0008), so re-running with tweaked rules never rewrites a
      classification that already landed. This is what stops a rule change
      churning every object in the pack.

    `reading_time` is exempt from the second rule: it is engine-*owned*, derived
    purely from the text, and must track the text.
    """
    locked = set(obj.overrides)
    updates: dict[str, Any] = {}

    for proposal in proposals:
        if proposal.value is None or proposal.field in locked:
            continue
        if proposal.field == "reading_time":
            if obj.reading_time != proposal.value:
                updates[proposal.field] = proposal.value
            continue
        if _is_unset(obj, proposal.field):
            updates[proposal.field] = proposal.value
    return updates


def _is_unset(obj: KnowledgeObject, field_name: str) -> bool:
    """Whether a field still holds the value it was created with.

    Not a falsiness check. `tier` defaults to `AWARENESS` (3) and `difficulty`
    to `INTERMEDIATE` -- both truthy -- so testing for emptiness made every
    enum-valued field look already-set and classification silently wrote
    nothing. Measured: all 222 objects came back `tier: 3`, which was the
    default rather than a decision.

    Comparing against the dataclass default is the honest question: has anything
    ever set this? A user who deliberately wants the default value locks it with
    `overrides`, which is checked before this is reached.
    """
    import dataclasses

    current = getattr(obj, field_name, None)
    for field_def in dataclasses.fields(obj):
        if field_def.name != field_name:
            continue
        if field_def.default is not dataclasses.MISSING:
            return current == field_def.default
        if field_def.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            return current == field_def.default_factory()  # type: ignore[misc]
    return current in (None, "", ())


def unmatched_fields(proposals: list[Proposal]) -> list[str]:
    """Which classification axes produced nothing.

    An object with no `tier` or no `category` is flagged for review rather than
    given a confident default -- ADR-0010's "never a silent guess".
    """
    proposed = {p.field for p in proposals if p.value is not None}
    return sorted({"tier", "category"} - proposed)
