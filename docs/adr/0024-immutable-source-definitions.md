# ADR-0024: Source definitions are immutable and versioned

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

Every knowledge object records the source that produced it and the parser version
that extracted it (ADR-0018). Those references are only meaningful while the
definition they point at still exists.

Source validation also showed how volatile this landscape is: URLs that were
official last year now return 403, and the primary source is a web page whose
structure Microsoft can change at any time. Sources *will* be retired and
replaced, and parsers *will* need to change.

Two failure modes follow. Deleting a retired source from `pack.yml` makes every
historical object it produced inexplicable. And silently changing a parser's
behaviour makes objects extracted before and after indistinguishable, so there is
no way to identify which need re-examining.

## Decision

**Source definitions are permanent.** A source is never removed from `pack.yml`.
Retirement is expressed by moving `status`:

| Status | Polled? | Meaning |
|---|---|---|
| `active` | yes | In rotation |
| `deprecated` | yes | Superseded; expect retirement |
| `disabled` | no | Out of rotation; definition retained for provenance |
| `replaced` | no | Superseded by the source named in `replaced_by` |

Same reasoning as Feature IDs: an identifier that something else points at cannot
be reclaimed.

**Parsers are versioned in configuration**, not hard-coded:

```yaml
adapter:
  name: html
  version: 1
```

When an extraction strategy changes, increment the version rather than changing
behaviour silently. The version is stamped onto every item the source produces,
so provenance answers: which parser created this object, which version
introduced this data, and which objects need revalidating after version 3.

## Consequences

### Positive
- **Historical provenance stays explicable** for the life of the repository.
- **Parser changes become visible and attributable.** "Which objects did the
  broken parser produce?" is a filter over `parser_version`, not an audit of the
  whole pack.
- **Declaring the version in `pack.yml` makes a behaviour change a reviewable
  config diff**, rather than something buried in a code change.
- Retirement is expressive: `deprecated` and `disabled` are genuinely different
  situations and the schema can now say which.
- Fits the existing rule that nothing is deleted, only marked.

### Negative
- **`pack.yml` grows monotonically.** Every source ever configured stays. Small,
  and the annotations make it readable.
- **`parser_version` is incremented by hand and can be forgotten**, which would
  silently attribute new-parser items to the old version. A reviewer check;
  deriving it from a source hash was rejected because it would change on every
  cosmetic edit and stop meaning "the logic changed".
- Four statuses is more than most projects need, and `deprecated` versus
  `disabled` will occasionally be a judgement call.

### Neutral
- Status describes the *definition*; `HealthState` describes whether it is
  currently working. Two axes, deliberately: "should we still ask?" and "did it
  answer?" are different questions.

## Alternatives considered

**Delete retired sources.** The obvious tidy-up. Rejected: it orphans the
provenance of every object the source ever produced.

**A separate `retired-sources.yml`.** Rejected: two files to keep consistent, and
provenance lookups would need to check both.

**Version parsers in code only.** Rejected: a behaviour change should be visible
in configuration where it is reviewed, not only in a diff of adapter internals.

**Auto-increment the version from a hash of the adapter source.** Rejected:
changes on every cosmetic edit, so it would stop meaning anything.
