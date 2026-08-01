# The Knowledge Time Machine

**Status:** Model layer implemented (M1). Query commands land in M5–M7 — see §6.

## The shift

A knowledge base answers **"what is true now?"**. A time machine also answers
**"what happened, and when?"**.

The target queries:

1. What changed in Microsoft Fabric during July 2026?
2. Show everything that changed since I last studied.
3. How has Direct Lake evolved over the past two years?
4. Compare this month's updates with last month's.
5. Show all updates that happened before a specific feature was released.

None of these require an AI model. All of them require **history that was
captured at the time**, because history cannot be reconstructed later. That is
why this is being designed now rather than in M7: the queries are cheap to add
later, the *data they need* is impossible to add later.

---

## 1. What was missing

ADR-0009 records *that* a source changed — revision number, changed field names,
a one-line summary. It deliberately did not keep old values, on the grounds that
Git already has them.

That is true and insufficient. Git has the bytes, but:

- answering query 3 means running `git log -p` per object and parsing diffs,
- which is slow, non-deterministic in output format, and unavailable to anyone
  reading the repository through the GitHub UI,
- and it dies the moment the repository is ever squashed, re-imported, or
  exported to another host.

**Git is a transport for history, not a queryable model of it.**

---

## 2. Two structures, two questions

### Revision snapshots — "how did *this* evolve?"

Each `Revision` now carries `content_hash`, `title_snapshot`, `summary_snapshot`
and `run_id`. The object becomes **self-describing over time**: its whole history
is readable from the one file, in order, by a human or a script.

```yaml
revisions:
  - revision: 1
    date: 2026-03-12
    title_snapshot: Direct Lake mode enters preview
    content_hash: sha256:aaa...
  - revision: 2
    date: 2026-07-04
    changed_fields: [title, content_hash]
    title_snapshot: Direct Lake mode reaches general availability
    content_hash: sha256:bbb...
```

Query 3 is now: open the file, read the list. No Git, no diffing, no model.

This is affordable precisely because ADR-0003 already caps stored summaries at a
short original paragraph — we are keeping a bounded amount of text we were
already permitted to store.

### The event log — "what happened *across the pack*?"

`state/events.jsonl`, append-only, one JSON object per line, time-ordered:

```jsonl
{"occurred_at":"2026-07-04T06:00:00+00:00","event_type":"discovered","feature_id":"MSF-2026-07-001","run_id":"run-...","revision":1}
{"occurred_at":"2026-08-02T06:00:00+00:00","event_type":"revised","feature_id":"MSF-2026-07-001","run_id":"run-...","revision":2,"changed_fields":["title"]}
```

Event types: `discovered` · `revised` · `reclassified` · `replaced` ·
`deprecated` · `artifact-generated` · `artifact-stale`.

**Why a separate log rather than walking the objects.** Queries 1, 2, 4 and 5 are
all *time-range filters over the whole pack*. Answering them by walking every
object and replaying its revisions works, but costs time proportional to the size
of the pack rather than to the size of the answer. One time-ordered file makes
each of them a single sequential scan.

JSON Lines specifically: append-only writes never rewrite earlier bytes, so
diffs stay minimal and two runs can never conflict over the same line. It stays
greppable and readable one line at a time, honouring ADR-0003's "no database".

---

## 3. How each query is answered

| Query | Mechanism |
|---|---|
| 1. What changed in July 2026? | Filter `events.jsonl` by `occurred_at` month |
| 2. Since I last studied | Filter by `occurred_at > learning_status last-changed` |
| 3. How has Direct Lake evolved? | Read that object's `revisions` list |
| 4. This month vs last month | Two range filters, compared |
| 5. Before feature X shipped | Find X's `discovered` event, filter `occurred_at <` it |

All deterministic. All plain-file. No AI, no index, no database.

---

## 4. What makes this honest

**`published_date` vs `occurred_at` are different clocks.** The first is when
Microsoft shipped something; the second is when *we* learned about it. Query 1
("what changed in Fabric during July") wants publication dates; query 2 ("since I
last studied") wants discovery timestamps. Conflating them would silently answer
the wrong question, so both are kept and each query names which it uses.

**`run_id` correlates everything.** The same identifier appears on the knowledge
object's provenance, on each revision, on every event, and in the run log. Any
run can be reconstructed completely: what it saw, what it created, what it
changed, and which sources were healthy at the time.

**Timestamps are UTC ISO-8601, always.** `_coerce_datetime` treats a naive
timestamp as UTC rather than local time. A history whose entries mean different
instants depending on which machine wrote them is worse than no history.

---

## 5. What is deliberately *not* stored

- **Full previous article text** — ADR-0003 still applies. Snapshots are of *our*
  summary, not the source's article.
- **Raw source responses** — tempting for parser-break forensics, rejected on
  repository size and copyright. The health baseline stores counts, not bodies.
- **Derived aggregates** — no precomputed monthly rollups. They are cheap to
  compute from the log and expensive to keep correct.

---

## 6. Delivery across milestones

| Piece | Milestone | Status |
|---|---|---|
| `Revision` snapshots + `content_hash` + `run_id` | M1 | ✅ implemented |
| `KnowledgeEvent`, `EventType`, UTC normalisation | M1 | ✅ implemented |
| Events actually appended during harvest | M2 | |
| Snapshots actually written on revision | M5 | |
| `ke history <id>` — evolution of one object | M5 | |
| `ke changes --since <date>` / `--month` | M7 | |
| Digest "compared with last month" section | M6 | |

The M1 work is the part that cannot be added later. Everything below it is a
query over data that will, by then, already exist.
