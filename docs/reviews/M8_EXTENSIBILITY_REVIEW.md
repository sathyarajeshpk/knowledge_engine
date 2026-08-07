# M8 — Extensibility Review

**Milestone:** M8 — the second Domain Pack
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** whether a **third** pack could now be added without modifying engine
code — and what would break first if nine were

---

## Why this is a different question from the Architecture Review

The Architecture Review answers *"did adding Azure require engine changes?"* —
a fact about the past, and the answer is no.

This asks *"would adding the next one?"* — a question about generalisation. A
system can pass the first question by accident: the second pack may simply have
resembled the first closely enough. The way to tell is to look at what the second
pack needed that the engine did not already have, and ask which of those needs
were **general** and which were **Azure's**.

The answer determines whether the roadmap's remaining seven packs (SQL, Python,
Databricks, Snowflake, AWS, Personal Knowledge, and Power BI as a category) are
data changes or a queue of engine work.

---

## The test: what would a third pack need?

Taking **Snowflake** as the concrete third pack — deliberately the most different
candidate on the roadmap, being neither Microsoft nor a cloud platform.

| Requirement | Mechanism | Engine change? |
|---|---|---|
| Its own sources | `sources:` in `pack.yml`, `http(s)` only | No |
| Its own ID prefix (`SNF`) | `id_prefix:`, 2–4 upper-case letters | No |
| Its own categories | `categories:` | No |
| Its own tier/difficulty vocabulary | `classification:` rules, `any:`/`none:` | No |
| Its own near-duplicate threshold | `dedupe.near_duplicate_jaccard` | No |
| Its own summary length limit | `limits.max_summary_words` | No |
| Its own notifiers | `notifiers:`, selected by name from a registry | No |
| To appear in the weekly run | `Pack.discover` globs `domain-packs/` | No |
| To participate in cross-pack detection | Automatic — `find_duplicates` takes the list | No |
| To be referenced from another pack | `known_feature_ids` spans the repository | No |

**Nothing on that list requires engine code.** A third pack is a directory
containing one YAML file.

### The honest caveats

Three things a third pack would need that are *not* purely configuration:

**1. A source adapter that does not exist yet.** The adapters are `rss`,
`sitemap` and `html`. A pack whose vendor publishes only, say, a paginated JSON
API would need a fourth adapter — engine code. This is not an abstraction leak:
the `Source` protocol is exactly the extension point for it, adapters are
registered by name, and `parser_version` is declared in `pack.yml` so the change
is visible in provenance. But it is engine work, and calling a pack "pure data"
without this footnote would be overselling it.

Snowflake publishes RSS, so this specific third pack would not hit it.

**2. Classification rules are string matching only.** Adequate for every
vocabulary seen so far, and deliberately so — rules are data, and data that can
evaluate is code. A domain needing genuinely different logic (numeric version
comparison, say) would need an engine change, and should get one rather than a
mini-language in YAML.

**3. Nothing validates that a pack's rules are *good*.** `ke validate` checks
that a pack is well formed, not that its taxonomy is sensible. The Azure GA rule
that classified previews as tier 1 was structurally valid YAML producing wrong
knowledge. Only reading the output caught it. This is the largest real risk in
onboarding pack three, and it is a process gap rather than a code gap.

---

## Extension points, ranked by how well they are proven

| Extension point | Mechanism | Evidence |
|---|---|---|
| **New pack** | Directory under `domain-packs/` | **Proven** — Azure, zero engine files |
| **New classification axis** | Key under `classification:` | **Proven** — 6 axes across two packs |
| **New review task kind** | One provider function + one enum member | **Proven** — `CROSS_PACK` added in M8 |
| **New notifier** | Registry entry implementing the protocol | Partial — two exist, both written in M6 |
| **New artifact type** | Enum member + one template file | Partial — seven exist, all written in M7 |
| **New source adapter** | `Source` protocol implementation | Partial — three exist, all written in M1 |
| **New index** | Renderer in `indexer.py` | Engine change by design |
| **New schema version** | `migrate.py` | **Unproven** — M9 work, no migration has run |

The pattern worth noting: every extension point exercised *after* it was designed
has held. The ones only exercised at design time are marked partial honestly —
three adapters written in one milestone by one author is not the same evidence as
a fourth added two milestones later.

`TaskKind.CROSS_PACK` is the strongest single data point in this table, because
it was added in M8 to a mechanism designed in M2 by following the documented
recipe. It also produced the one defect: the CLI's `--kind` choices were a
hard-coded tuple that did not follow the enum, so the engine produced a kind its
own CLI refused to name. The extension point worked; a hard-coded list next to it
did not. That has been changed to derive from the enum, which is the general fix
— **an extension point is only as good as the places that enumerate it.**

---

## What breaks first at nine packs

Not extensibility. Adding the ninth pack is the same work as adding the third.

What breaks is **performance**, and it is measured rather than feared: index
rebuild is O(packs²) in full-pack reads, because cross-pack duplicate detection
runs inside each pack's index rebuild and needs every pack. At two packs this is
four reads. At ten it is 3.6× the single-pack cost for identical knowledge, and
at the roadmap's full nine packs the weekly run would be dominated by how many
packs exist rather than by how much was learned.

See the M8 Performance Review. The recommendation is to hoist the scan out of
per-pack rebuild before the pack count reaches roughly five.

Second is **repository size**, at ~14 KB per object. Nine packs at 1,000 objects
each is ~126 MB and 18,000 files — fine. The same nine at 10,000 objects each is
1.26 GB, which is not.

---

## Assessment

**A third pack is a data change.** The mechanisms Azure exercised are general,
not Azure-shaped, and the one M8 extension that went through a *pre-existing*
extension point (`TaskKind`) worked.

Two things should land before the third pack, and neither is about
extensibility itself:

1. **`docs/ADDING-A-PACK.md`**, written from what Azure actually cost — including
   the `none:` guard lesson and the instruction to read the produced knowledge
   rather than the exit code. The engine makes adding a pack easy; nothing
   currently makes adding a *correct* pack easy.
2. **A decision on the O(packs²) index rebuild**, so the cost of the fifth pack
   is not discovered by the fifth pack.
