# ADR-0026: Record the full discovery chain, and separate representation from adapter

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M1

## Context

M1 originally recorded provenance as a flat set of attributes: adapter type,
source name, timestamp, extraction method, parser version, selector. That is
enough to answer "which code produced this?" but not enough to answer "how did
this knowledge get here?" — and the second question is the one an investigation
actually starts from.

Two things forced the issue during source validation.

**First, a source acquired a second representation.** The Fabric "What's New"
updates exist twice: as rendered HTML on `learn.microsoft.com`, and as Markdown
in the public `MicrosoftDocs/fabric-docs` repository. Same authoritative content,
different hosts, different infrastructure, different failure modes — which is
exactly what makes one a genuine fallback for the other rather than a second
copy of the same risk. Once that was true, `adapter_type: html` no longer
answered "was this read from the rendered page or from the source file?", because
the answer had become a property of the *response*, not of the code.

**Second, the fields were stored in the order they were written**, which is not
the order the knowledge travelled. A reader had to reassemble the chain mentally
every time. For a record whose entire purpose is explainability, that is a real
cost.

The general principle underneath: **every link in the chain can break
independently, so every link must be nameable.** A parser break, a
representation change, an identity downgrade and a date-precision loss are four
different failures with four different remedies, and provenance is where they
are told apart.

## Decision

**1. Add `source_representation` to every discovered item**, with values `html`,
`markdown`, `rss`, `atom`, `api`. It records the format actually received, as
distinct from `adapter_type`, which records the code that read it. The two are
usually the same word and occasionally are not; storing both costs a few bytes
and removes an inference.

**2. Store provenance in discovery-chain order:**

```
Source → Representation → Adapter → Adapter version → Extraction method
       → Discovery time → Identity basis → Identity key
```

with `date_precision` and `date_confidence` as the tail of the same chain on the
knowledge object itself. The serialisation order in `to_dict` is fixed
deliberately, not incidentally, so a stored object reads as a narrative.

**3. Identity basis and key travel inside provenance**, rather than only on the
in-memory item. A duplicate investigation begins with "what were we matching
on?", and that question must be answerable from a file on disk years later,
without re-running discovery.

## Consequences

### Positive

- **An object is explainable from the file alone.** Every step from source to
  stored knowledge is named, in order, with no inference from adapter names.
- **Representation-level failures become visible.** "Every object read from
  Markdown between these two runs has a weaker identity basis" is now a query
  over stored data rather than an archaeology exercise.
- **The fallback chain is auditable.** `source_role` plus `source_representation`
  together say not just that a fallback fired, but what changed about the
  knowledge when it did.
- **`identity_basis` narrows re-verification.** When a source changes markup, the
  objects worth re-examining first are the ones resting on a title hash rather
  than a canonical URL, and that is now filterable.

### Negative

- **Two fields that are usually equal.** `source_representation: html` alongside
  `adapter_type: html` looks redundant on most objects and will invite a
  future "simplification". The Markdown fallback is the counter-example, and it
  is why the pair exists; this ADR is the record of that reasoning.
- **`Provenance` now has eleven fields**, which is a lot for a value object. It is
  still flat and still frozen, but it is approaching the size where a nested
  structure would read better. Left flat because nesting would complicate the
  YAML for no gain in answerability.
- **Field order is now load-bearing** for byte-identical output (ADR-0022).
  Reordering the dataclass changes stored files, so the order is documented in
  `to_dict` with an explanatory comment rather than left to look arbitrary.
- **Existing objects predate the field.** None exist yet — M1 writes nothing —
  so no migration is required. Had this landed after M2, it would have needed a
  `schema_version` bump.

## Alternatives considered

**Infer representation from the adapter.** Rejected: it is exactly the inference
that broke. `AdapterType.MARKDOWN` implies Markdown today, but `FeedSource`
already serves three adapter types and one adapter can grow a second
representation at any time.

**Put representation in `selector`.** Rejected: `selector` is free text for
humans; a field that gets queried must be an enum.

**A single `discovery_chain` string** such as
`learn/html/html-table-row/v1/canonical-url`. Rejected: compact and readable, but
unqueryable and unvalidatable — it converts nine typed fields into one string
that must be parsed to be useful.

**Defer to M6 with the time machine.** Rejected: provenance is written at
discovery time, so a field added later is absent from every object minted before
it, permanently. Fields whose value is historical must exist before the history
does.
