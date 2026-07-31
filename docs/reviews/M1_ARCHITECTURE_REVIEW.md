# M1 Architecture Review — Discovery

**Reviewer:** Senior Software Architect
**Date:** 2026-07-31
**Scope:** everything M1 added — clock, identity, normalisation, three adapters,
orchestration, source health, fallback chains, provenance
**Verdict:** **Ready to merge, with one finding that must be resolved before M2
mints a single Feature ID.**

---

## 1. What was built

M1 makes the engine look at the internet and report what it found. It fetches
every configured source, parses each into normalised items, computes a stable
identity per item, records the full discovery chain, tracks source health, falls
back when a source fails, and prints the result.

**It writes nothing.** That is the milestone's most valuable property, not a gap.
Feature IDs are permanent (ADR-0005) and identity is what they are minted from
(ADR-0023), so discovery is the last point at which a mistake costs nothing.

| Area | Delivered |
|---|---|
| Injected clock | `Clock` Protocol, `SystemClock`, `FrozenClock`; enforced by a test that greps the whole package |
| Identity | Four-level hierarchy, `identity_basis` + `raw_value` recorded |
| Normalisation | Pure functions: canonical URL, HTML→text, dates with precision *and* confidence, truncation |
| Adapters | HTML (primary), Markdown (secondary), RSS/Atom |
| Orchestration | Fallback chains, per-source failure isolation, `ReviewItem` on total failure |
| Health | `healthy`/`degraded`/`failed`/`disabled`, median-based parser-break detection |
| Provenance | Full discovery chain, in chain order (ADR-0026) |
| Tests | 215, all offline, ~0.9s |
| ADRs | 0017–0026 |

---

## 2. Validation against production

Every number below was measured on a GitHub Actions runner against the live
sources, not against fixtures.

| | Fabric HTML | Fabric Markdown | Power BI HTML | Power BI Markdown |
|---|---|---|---|---|
| Items | 336 | 315 | 25 | 19 |
| Durable identity | 313 (93%) | 308 (97%) | 14 (56%) | 14 (73%) |
| Dated | — | 163 | — | **0** |
| **Identity agreement with primary** | — | **96%** | — | **73%** |

**The 96% figure is the one that matters.** It says that if the Fabric primary
goes down and the Markdown secondary takes over, 96% of items keep the identity
they already had — so failover produces revisions, not duplicate permanent
Feature IDs. That is the property that makes a fallback chain safe rather than
merely available, and it had never been measured before this milestone.

---

## 3. Strengths

**The architecture survived contact with reality, and changed shape when it
should have.** Source validation found that no purpose-built update feed is
reachable from a runner — every `blog.fabric.microsoft.com` and
`powerbi.microsoft.com` feed 403s, browser User-Agent included. The plan had RSS
as primary; the evidence made HTML primary. Inverting the milestone's central
assumption on measured evidence is the correct outcome of a validation step, and
it happened before adapters were written rather than after.

**The two injected seams are real, not decorative.** Clock and fetcher are
injected everywhere, and the clock rule is enforced by a test that walks every
module looking for `datetime.now(`. All 215 tests run offline in under a second.
An engine whose tests need the internet is an engine that becomes untestable on
the day a source goes down — which is exactly the day you need to change it.

**Failure handling is centralised and correct.** Adapters raise; `discover.py`
decides. `_attempt()` catches bare `Exception` — normally a smell, deliberate and
commented here, because an adapter *bug* must be survived rather than allowed to
end the run and suppress the run-log commit that keeps the weekly cron alive.

**`ReviewItem` instead of an empty list.** "No updates" and "we could not look"
are structurally distinguishable. Weeks of silent data loss is the failure mode
this engine exists to prevent, and this is where that is enforced.

**Two defects were found by measuring rather than reasoning.** The prose-date
defect (1 row in 361: a month scraped from a sentence, labelled `EXACT`, destined
to become a permanent Feature ID) was invisible in the fixture and obvious in
production. So was the identity finding in §5. The lesson is now written into the
playbook: measure the source.

**The Markdown secondary is a genuine fallback.** The previous fallback was a feed
of docs-repo merge commits — alive, but carrying commit messages rather than
knowledge. A fallback that cannot produce what the primary produces is not a
fallback; it is a monitoring endpoint wearing a fallback's name.

---

## 4. Weaknesses

**W1 — `models.py` is 1,276 lines.** It now holds enums, identity plumbing,
knowledge objects, revisions, events, health, attempts and provenance. It is
still coherent, but it is the file everything imports and it is growing every
milestone. Splitting it before M5 (`models/`, `sources.py`, `events.py`) would be
cheap now and expensive later. Not urgent; do it when M2 adds to it.

**W2 — The Power BI secondary is materially weaker than the Fabric one.**
73% identity agreement against Fabric's 96%, and **zero** dated items. On failover,
roughly 5 of 19 items would mint new Feature IDs. The cause is structural: the
Power BI table has no date column and fewer resolvable links, so more items rest
on title hashes — and title hashes diverge between representations because the two
adapters title rows differently (HTML takes the first linked text, Markdown the
first content cell). This is documented and tested, but "documented" is not
"safe": if the Power BI primary fails during a weekly run, the pack acquires
duplicates. See recommendation R2.

**W3 — Source health is computed but not persisted.** `SourceHealth` is built
per run and discarded. Parser-break detection compares against a historical
median, so until M6 writes `state/source-health.json` the check can only fire
when a baseline is passed in by hand. The capability exists; the memory does not.
This is scheduled, not forgotten, but it means the single most valuable health
check is currently inert in production.

**W4 — `tools/` has grown to four probes with overlapping concerns.** They were
written one question at a time and it shows. They are deliberately outside
`engine/`, untested and throwaway-grade, so this is low-cost — but
`fallback_probe.py` has outgrown that category: it measures a safety property
(identity agreement) that should be checked continuously rather than when someone
remembers to look. See R3.

**W5 — No adapter has a recorded-fixture test from live bytes.** Fixtures are
hand-written from measured structure, which is far better than imagination but
still not the real document. A saved response would have caught the prose-date
defect at authoring time.

---

## 5. Findings

### F1 — Distinct features can share one identity (HIGH — blocks M2, not M1)

**This is the finding of the milestone.** It was surfaced by
`tools/fallback_probe.py` and confirmed against production data.

ADR-0023 makes the canonical URL the strongest identity basis. That rests on an
assumption which is true for feeds and **false for curated update tables**:

> one URL identifies one update

Measured against the live Fabric source:

- **237 of 315 items (75%) resolve to announcement blog posts**
  (`community.fabric.microsoft.com`, `blog.fabric.microsoft.com`) rather than
  documentation pages.
- One announcement post routinely announces **several** distinct features.
- 315 items collapse to **219 unique identities**. Most of that is correct — the
  same feature genuinely appears twice in the document — but **15 identity groups
  merge features that are not the same thing**, and 12 of those 15 are shared
  announcement-blog URLs.

Concretely, three distinct updates share one identity today:

```
https://community.fabric.microsoft.com/.../Simplify-data-movement-with-Copy-...
    Edit Copy job via JSON payloads (Generally Available)
    Switch between full and incremental copy mode in Copy job (Generally Available)
    Extended Auto partition support in Copy job for Oracle, Fabric Lakehouse, …
```

**Impact.** M1 is unaffected — it writes nothing. In M2, `dedupe.py` uses
`identity.key` as its strongest layer, so these would be stored as **one**
knowledge object under **one permanent Feature ID**, and the other features would
be silently absent. Not flagged, not queued for review — absent. That violates
"never delete existing knowledge" in spirit, and Feature IDs are never reused, so
it cannot be corrected afterwards without leaving a permanent scar.

**This is not caused by the Markdown adapter.** The HTML primary shares the
behaviour; the probe simply made it visible. It is a property of ADR-0023 meeting
a source shape ADR-0023 did not anticipate.

**Why it is not fixed in this PR.** Per the project's development workflow, a
design issue surfacing mid-implementation is stopped and explained rather than
resolved unilaterally — and the fix changes ADR-0023, which is the contract M2 is
built on. Three options, with my recommendation:

| Option | Mechanism | Assessment |
|---|---|---|
| **A. Composite identity** | `hash(canonical_url + normalised_title)` for table-row extraction | Deterministic and run-independent. Fixes merging completely. Cost: a reworded row gets a new identity, weakening the exact protection ADR-0023 was written for. |
| **B. Rank announcement URLs below titles** | Treat `blog.*`/`community.*` hosts as non-identifying; fall back to title hash | Targets the actual root cause — doc pages document one feature, announcement posts announce many. Deterministic, configurable per pack. **Recommended.** Cost: 75% of items drop to a non-durable basis. |
| **C. De-duplicate within a run only** | Merge only when titles also match | Rejected: run-dependent, so identity would change when a row stops appearing. Breaks permanence. |

I recommend **B**, expressed as a per-pack list of non-identifying hosts in
`pack.yml`, because it fixes the cause rather than the symptom and keeps durable
identity where it is actually durable. It should be decided before M2 begins.

### F2 — Power BI items carry no publication dates at all (MEDIUM)

Zero of 19 Markdown rows and no date column in the HTML. Every Power BI Feature ID
would therefore be minted from the **discovery** month with
`date_confidence: inferred` — meaning an item announced in March but first
harvested in August is filed under August, permanently.

This is a source property, not a parser bug, and ADR-0005's fallback is working
as designed. But it is worth knowing that a meaningful slice of the pack will
carry approximate dates forever, and worth deciding in M2 whether backfill should
seed Power BI history before the first live run rather than after.

---

## 6. Risks going into M2

| Risk | Severity | Mitigation |
|---|---|---|
| Distinct features merged under one permanent ID (F1) | **High** | Resolve before M2 mints anything. M1 writes nothing, so the window is open and free. |
| Power BI failover mints duplicates (W2) | Medium | R2 — gate the weak secondary, or accept and monitor |
| Parser break undetected until health persists (W3) | Medium | M6; `fallback_probe` in CI narrows the gap meanwhile |
| Learn changes markup silently | Medium | `parser_version` + `selector` make affected objects findable rather than requiring a full re-verify |
| Learn begins blocking runner IPs | Medium | The Markdown secondary is now a genuine fallback — this risk is materially lower than it was |
| `models.py` growth (W1) | Low | Split when M2 adds to it |

---

## 7. Recommendations

### Before M2

**R1 — Decide F1.** Blocking. My recommendation is option B. Whatever is chosen
becomes an ADR amending ADR-0023, and M2's dedupe design follows from it.

**R2 — Decide the Power BI secondary's status.** Either mark it
`status: disabled` until identity agreement improves (safe, loses the fallback),
or keep it and accept ~5 duplicate IDs on failover (fast, permanent cost). I lean
towards disabling it: a fallback whose activation damages the pack is worse than
no fallback, because it fires automatically and without a human present.

**R3 — Promote the identity-agreement check from probe to assertion.** It is a
safety property, so it should fail rather than print. Adding a threshold to
`fallback_probe.py` and running it on a schedule converts "someone looked once" into
"the engine notices". Cheap, and it is what would have caught F1 automatically.

### After M2, before v1

**R4 — Split `models.py`** (W1).

**R5 — Record one real response per adapter as a test fixture** (W5).

**R6 — Persist source health** — already scheduled for M6, noted so it is not
lost.

---

## 8. What I deliberately did not change

- **The identity hierarchy**, despite F1. Changing the contract M2 depends on,
  mid-milestone, without approval, is exactly what the development workflow
  forbids.
- **Power BI's source status**, for the same reason — R2 is a judgement about
  acceptable permanent cost, and that is the maintainer's call.
- **Scope.** No storage, no minting, no classification. M1 stays a milestone that
  can be thrown away and re-run.

---

## 9. Assessment

M1 does what it set out to do and is honest about where it does not. The
architecture bent where evidence required it (RSS → HTML primary), the safety
properties are enforced by tests rather than by convention, and the two defects
found were both found by measuring production rather than by reasoning about
fixtures — which is the habit worth keeping.

The identity finding (F1) is genuinely valuable *because* it arrived now. M1
writes nothing, so it costs a conversation. Had the same behaviour been found
three weeks into M2, it would have cost a pack full of permanent, un-correctable
Feature IDs.

**Recommend merge**, with F1 resolved before M2 begins.
