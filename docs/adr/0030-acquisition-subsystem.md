# ADR-0030: Acquisition is a subsystem with an enforced boundary

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M1
**Relates to:** ADR-0018 (uniform adapter interface), ADR-0019 (fallback and failure isolation), ADR-0027, ADR-0028

## Context

M1 grew four capabilities that were designed one at a time: discovery, identity,
confidence and (in M2) review. They turned out to be one thing — **acquisition**:
everything between "a source exists somewhere" and "an identified, graded item
ready to become knowledge".

The roadmap makes reuse concrete rather than hypothetical. Eight more Domain
Packs are planned, and knowledge will eventually arrive from APIs, PDFs, videos
and other documentation systems. Each of those should need **a new adapter and
nothing else**.

Left as loose modules under `ke/`, that would not survive contact with M2. The
moment `discover.py` imports `store.py` for something convenient, the pipeline
stops being reusable — and nothing would have noticed.

## Decision

**Acquisition is a package with a stated contract and an enforced boundary.**

```
engine/ke/acquisition/
├── __init__.py        the public surface (the port)
├── sources/           adapters: html, markdown, feed
├── identity.py        the four-level hierarchy
├── confidence.py      grading and collision detection
└── discover.py        orchestration, fallback chains, health
```

### The two ports

**In — the adapter contract.** `discover() -> list[RawItem]`. Adding a source
type is one module, one entry in `ADAPTERS`, one `adapter:` block in `pack.yml`.
A PDF adapter and an HTML adapter are indistinguishable to everything past this
line.

**Out — `DiscoveryResult`**, with items already graded and separated into
`mintable`, `needs_review` and `collisions`.

### The rules

1. **Acquisition imports downward only** — `models`, `normalize`, `clock`. Never
   storage, classification, indexing, digest or validation.
2. **Adapters fetch and parse; they never decide what happens on failure.** They
   raise `SourceError`; `discover` decides (ADR-0019).
3. **No source-specific logic outside an adapter.** Vendor-specific markup,
   hosts or date formats belong in that adapter or in `pack.yml` as data.
4. **Everything except fetching is pure.** Clock and fetcher are injected.

### Enforcement

`engine/tests/test_architecture.py` checks these by scanning imports rather than
trusting them — including a test that grading never imports an adapter, because
source-specific grading rules would silently end reusability. The tests were
verified to fail when a violation is introduced; a guard that cannot trip is
decoration.

### Supporting change

`ItemIdentity` and `IdentityBasis` moved from `identity.py` into `models.py`.
`models` previously imported from `identity`, which would have made core types
depend on the subsystem. Types belong below the code that computes them.

## Consequences

### Positive

- **A new source type is additive.** The contract is one method, and the tests
  prove every registered adapter satisfies it.
- **The boundary is checked, not aspirational.** The rule that would otherwise
  erode first — "just import store, it's only one function" — now fails CI.
- **Extraction is cheap if it is ever wanted.** `ke.acquisition` has no upward
  dependencies, so it could become its own package without untangling anything.
- **The `DOWNSTREAM` list names modules that do not exist yet**, which is when
  the rule is cheapest to enforce: before the code that would break it is
  written.

### Negative

- **A late structural move.** Files moved after the milestone's substance was
  written, so `git log --follow` is needed to trace history through the rename,
  and the M1 diff is larger than the behaviour change it contains.
- **The facade duplicates names.** `ke.acquisition.__init__` re-exports 18
  symbols; adding a public name means touching two files, and a stale `__all__`
  is a silent papercut. Mitigated by a test that every exported name resolves.
- **The boundary is currently one-sided.** Nothing downstream exists yet to
  respect it. The real proof is M2 — if `store.py` can be written without
  reaching back into acquisition's internals, the contract held.
- **`identity.TITLE_NOISE` still violates rule 3.** It is Microsoft-flavoured
  vocabulary living in code rather than `pack.yml`. ADR-0023 judged a shared list
  safer than a per-pack one while there is one vendor; a second vendor, or a
  video or PDF source with different title conventions, is the trigger to move
  it. Recorded here so it is a known debt rather than a discovery.

## Alternatives considered

**Leave the modules flat and document the boundary.** Rejected: documentation is
not enforcement, and this is precisely the kind of rule that erodes one
convenient import at a time.

**Enforce with tests but no package move.** Tempting — it is the cheaper half and
delivers most of the safety. Rejected because the directory is what a future
contributor sees first; a boundary visible only in a test file is one they will
cross before they find it.

**Wait until M2, when dedupe and review join the subsystem.** Rejected: moving
four modules now and three more later is churn twice, and M2 is exactly when the
boundary is most likely to be breached.
