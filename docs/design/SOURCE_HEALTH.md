# Source Health and Fallback

**Status:** Model layer implemented (M1). Behaviours land across M1, M2 and M6 — see §8.

## The rule this exists to enforce

> The engine must never silently stop collecting knowledge. Every failure must be
> visible, actionable, recoverable and auditable.

Silence is the enemy. A pipeline that returns zero items looks identical whether
the week was quiet or the parser broke three months ago — and the second case
costs months of missing knowledge that nobody notices until they go looking for
something that should be there.

Everything below exists to make those two cases distinguishable.

---

## 1. Health states

| State | Meaning | How it is entered |
|---|---|---|
| `healthy` | Fetched, parsed, item count normal | A successful primary-source run |
| `degraded` | Working, but not properly | Fell back to a secondary, **or** returned far fewer items than baseline |
| `failed` | Could not fetch or parse | Any unsuccessful attempt |
| `disabled` | Out of rotation | Set by a human; the engine never clears it |

**`degraded` is the state that earns its keep.** HTTP 200 with zero items is not
health. It is what a broken parser looks like from the outside.

A `disabled` source is never state-changed, never alerted on, and never removed.
Sources are kept forever so that objects whose provenance points at them stay
explicable years later.

---

## 2. What is recorded

Per **attempt** (`SourceAttempt`) — the raw observation:

`source_name` · `run_id` · `attempted_at` · `ok` · `role` · `http_status` ·
`response_ms` · `items_discovered` · `failure_reason`

Per **source** (`SourceHealth`) — the running state folded from those attempts:

`state` · `last_success_at` · `last_attempt_at` · `consecutive_failures` ·
`last_http_status` · `last_failure_reason` · `last_items_discovered` ·
`recent_item_counts` · `disabled_reason` · `open_alert_issue`

`SourceHealth.record()` returns a **new** object rather than mutating, matching
`KnowledgeObject.with_engine_fields()`. A partially-applied health update is
impossible.

**Storage:** `state/source-health.json`, per pack. History is bounded to 26
successful runs — about six months of weekly runs — because this file is
committed on every run and must not grow without limit.

---

## 3. A failed source never fails the run

`RunReport.succeeded_overall` depends only on whether the run *completed*, never
on whether every source worked.

This is not politeness. Failing the run would:

- discard successful harvesting from every healthy source, and
- skip the run-log commit — which is what stops GitHub disabling the weekly cron
  after 60 days of inactivity.

One broken feed would turn into a dead pipeline. Failures are recorded in the run
log, the health file, the weekly digest and — after enough of them — a GitHub
Issue.

---

## 4. Parser-break detection

The check that stops silent death.

```
baseline = median(recent successful item counts)     # median, not mean
suspicious = items_discovered < baseline × 0.34
```

- **Median, not mean** — one anomalous week must not move the baseline enough to
  mask a genuine break the following week.
- **At least 3 observations** before the baseline means anything. A new source is
  never accused of breaking before it has a history.
- **Threshold 0.34** is deliberately generous. A false "possible parser break"
  costs a glance at the digest; a missed one costs weeks of lost knowledge. The
  asymmetry is the whole point.

A suspicious run marks the source `degraded` with:

> Possible parser break detected. Source returned significantly fewer items than
> its historical baseline.

---

## 5. Fallback chains

Each source declares a chain in `pack.yml`:

```yaml
sources:
  - name: fabric-whats-new
    role: primary
    adapter: html
    url: https://learn.microsoft.com/en-us/fabric/fundamentals/whats-new
    fallback:
      - name: fabric-docs-commits
        role: secondary
        adapter: github-commits
        url: https://github.com/MicrosoftDocs/fabric-docs/commits/main.atom
      - role: manual-review        # terminal link: raise, never lose
```

1. Try **primary**. Success → `healthy`.
2. On failure, try **secondary**. Success → `degraded` (working, but not
   properly — the operator should know).
3. If both fail → a **review item**, never a silent zero.

`Provenance.source_role` records which link produced each item, so knowledge
gathered from a fallback is identifiable afterwards rather than indistinguishable
from primary data.

---

## 6. Escalation to a GitHub Issue

After **3 consecutive failures** (`FAILURE_ALERT_THRESHOLD`), the engine opens:

```
Source Health Alert - <Source Name>
```

containing the failing URL, failure reason, last successful run, consecutive
failure count, and suggested investigation.

**No duplicates.** `open_alert_issue` holds the issue number while one is open,
and `needs_alert` returns `False` whenever it is set. A source failing for six
months produces one issue, not twenty-six.

This uses the notifier interface from ADR-0013, so it costs nothing extra and
needs no secret — the workflow's built-in token suffices.

---

## 7. Reporting

**Weekly digest** gains a Source Health section: healthy / degraded / failed /
newly disabled, with reasons for anything not healthy.

**`ke health`** reports current state, failure history, baselines, open alerts,
recommendations, and readiness before the next scheduled run. It reads
`source-health.json` and makes no network calls, so it is instant and safe to run
anywhere.

**Historical metrics** come from `recent_item_counts` plus the per-run attempt
records in the run log, so reliability trends are analysable without a database.

---

## 8. Delivery across milestones

The data model is built now because it is expensive to retrofit. The behaviours
land with the code that needs them — building issue creation before there is a
pipeline to monitor would produce untested code guarding nothing.

| Piece | Milestone | Why then |
|---|---|---|
| States, attempts, health record, baselines, parser-break detection | **M1** ✅ | Model layer; retrofit-expensive |
| `discover()` contract and `Provenance` | **M1** ✅ | Every adapter must be born with it |
| Attempts actually recorded; fallback chain executed | **M1** | Needs adapters |
| `source-health.json` persisted across runs | **M2** | Needs a run that writes state |
| `ke health` | **M2** | Needs persisted state to report |
| Digest Source Health section | **M6** | Needs the digest |
| GitHub Issue escalation | **M6** | Needs the notifier |
