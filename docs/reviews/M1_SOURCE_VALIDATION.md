# M1 Source Validation Report

**Date:** 2026-07-31
**Method:** Two probe rounds on a GitHub Actions runner — 22 candidates, real HTTP
**Runs:** [#1](https://github.com/sathyarajeshpk/knowledge_engine/actions/runs/30661044908) · [#2](https://github.com/sathyarajeshpk/knowledge_engine/actions/runs/30661189485) · [#3 HTML structure](https://github.com/sathyarajeshpk/knowledge_engine/actions/runs/30661717204)
**Status:** Awaiting approval. No source is pinned to `pack.yml` yet.

---

## Headline

**The two feed URLs that M0 planning identified as "the official RSS feeds" are
both dead from a runner.** Every `blog.fabric.microsoft.com` and
`powerbi.microsoft.com` feed returns **403 Forbidden**.

Had those been pinned in `pack.yml` during M0 — which was the tempting,
tidy-looking option — M1 would have shipped a source list that fetched nothing.
Leaving `sources: []` empty was correct.

**9 of 22 candidates are mechanically usable.** But mechanically usable is not
the same as *good*, and the honest conclusion is harder than the number suggests:

> **No purpose-built "what's new in Fabric / Power BI" feed is reachable from a
> GitHub Actions runner.** Everything that works is either the wrong content, the
> wrong granularity, or stale.

---

## What passed

Usable = reachable, parses, and **every entry carries a publication date**. That
last condition is not cosmetic: ADR-0005 mints Feature IDs from the publication
month, so an undated feed silently degrades every ID to `inferred`.

| Source | URL | Entries | Dated | Newest | Body size |
|---|---|---|---|---|---|
| `azure-updates-rss` | `microsoft.com/releasecommunications/api/v2/azure/rss` | 200 | 200/200 | 2026-07-31 | 255 ch |
| `learn-search-rss-fabric` | `learn.microsoft.com/api/search/rss?search=fabric` | 100 | 100/100 | 2026-07-30 | 350 ch |
| `learn-search-rss-powerbi` | `learn.microsoft.com/api/search/rss?search=power+bi` | 100 | 100/100 | 2026-07-30 | 532 ch |
| `learn-search-rss-fabric-whatsnew` | same API, different query | 100 | 100/100 | 2026-07-30 | 350 ch |
| `fabric-docs-commits-atom` | `github.com/MicrosoftDocs/fabric-docs/commits/main.atom` | 20 | 20/20 | 2026-07-31 | 1,028 ch |
| `powerbi-docs-commits-atom` | `github.com/MicrosoftDocs/powerbi-docs/commits/main.atom` | 20 | 20/20 | 2026-07-31 | 203 ch |
| `fabric-docs-api` | `api.github.com/.../fabric-docs/commits` | 5 | 5/5 | 2026-07-31 | — |
| `powerbi-docs-api` | `api.github.com/.../powerbi-docs/commits` | 5 | 5/5 | 2026-07-31 | — |
| `fabric-microsoft-com-feed` | `microsoft.com/en-us/microsoft-fabric/blog/feed/` | 10 | 10/10 | **2026-06-29** | **29,239 ch** |

## What failed

| Source | Result |
|---|---|
| `blog.fabric.microsoft.com/en-US/blog/feed/` | **403** |
| `blog.fabric.microsoft.com/en-us/blog/feed/` | **403** |
| `blog.fabric.microsoft.com/feed/` | **403** |
| `powerbi.microsoft.com/en-us/blog/feed/` | **403** |
| `powerbi.microsoft.com/blog/feed` | **403** |
| `powerbi.microsoft.com/blog/feed/` | **403** |
| `microsoft.com/en-us/power-platform/blog/power-bi/feed/` | 404 |
| `microsoft.com/en-us/power-bi/blog/feed/` | 404 |
| `community.fabric.microsoft.com/…/rss/board` | 404 |
| `roadmap.fabric.microsoft.com/feed` | 404 |

**The 403s are not a User-Agent problem.** Round 2 retried every failure with a
browser User-Agent and they still returned 403. That hypothesis is disproven.
The most likely remaining explanation is datacenter IP-range blocking — but the
cause does not change the conclusion, because **the weekly job runs on exactly
this infrastructure.** If a feed cannot be fetched from a GitHub runner, it
cannot be a source, regardless of whether it works from a laptop.

## Reachable but not feeds

| Source | Notes |
|---|---|
| `learn.microsoft.com/en-us/fabric/fundamentals/whats-new` | 200, 308 KB HTML. **The canonical curated update list.** Needs an HTML adapter. |
| `learn.microsoft.com/en-us/fabric/release-plan/` | 200, redirects to `roadmap.fabric.microsoft.com` |
| `roadmap.fabric.microsoft.com/` | 200, 196 KB HTML — a JavaScript app, so likely not server-rendered |

---

## Quality assessment — the part that matters

Nine sources pass the mechanical test. Judged on whether they actually deliver
*new knowledge worth storing*, the picture is worse:

### The Learn search RSS is search results, not news

`learn-search-rss-fabric` looks ideal — 100 entries, fully dated, clean summary
lengths. Then you read the titles it returned:

> Microsoft Fabric documentation · Ingest Data with Microsoft Fabric - Training ·
> Roles in workspaces in Microsoft Fabric · **Azure Architecture Center** ·
> **Microsoft SQL Documentation**

These are documentation landing pages ranked by search relevance, not
announcements. The date is the page's last-edited date, not a publication date
for anything new. Ingesting this would fill the pack with "Microsoft Fabric
documentation exists" rather than "Direct Lake reached GA".

**Mechanically usable, semantically wrong.**

### The docs-commit feeds are Git plumbing

`fabric-docs-commits-atom` is structurally excellent: reliable, free, always
dated, updated within hours. Its content:

> Merge pull request #3215 from MicrosoftDocs/main639210926963698039sync_temp ·
> Merging changes synced from … (branch live) · Merge pull request #15705

Merge commits carry no knowledge. Extracting meaning requires fetching each
commit's diff and mapping changed file paths back to article titles — real work,
and high volume (20 commits in ~1 day).

**Excellent plumbing, no signal without significant processing.**

### The one blog feed that works is the wrong blog, and it is stale

`fabric-microsoft-com-feed` returns 10 entries whose newest is **2026-06-29 — a
month old**, with titles like "Why data teams are emerging as leaders in AI agent
adoption" and "FabCon Europe 2026: the sessions we're most excited about".

That is the *corporate marketing blog*, not the *feature updates blog*. It also
ships **29,239-character full articles**, so ADR-0003 requires truncating at
ingest rather than storing what it delivers.

### `azure-updates-rss` is the best feed found — for a different product

200 entries, all dated, current to today, clean summaries, and genuinely
structured titles: `[Launched] Generally Available: …`, `[In preview] Public
Preview: …`. That prefix maps directly onto our Tier 1/2 rules.

It is the Azure release-communications feed. Whether it carries Fabric items is
untested — worth one query before assuming.

---

## Recommendation

**Do not pin the Learn search RSS feeds.** They pass the mechanical test and
would quietly poison the pack with documentation index pages. This is exactly the
failure the "verify before pinning" rule exists to catch — and a probe that only
checked HTTP 200 would have waved all three through.

**Proposed source set for M1:**

| Priority | Source | Adapter | Rationale |
|---|---|---|---|
| 1 | `learn.microsoft.com/en-us/fabric/fundamentals/whats-new` | **HTML** | The authoritative curated list of Fabric updates. Reachable. Highest signal available. |
| 2 | `fabric-docs-commits-atom` + `powerbi-docs-commits-atom` | Atom + filtering | Reliable and current; needs path→article mapping to be useful |
| 3 | `azure-updates-rss` | RSS | Best-structured feed found; confirm Fabric coverage first |
| 4 | `fabric-microsoft-com-feed` | RSS + truncation | Low value and stale, but it is a real blog feed. Truncation mandatory. |
| — | Learn search RSS (×3) | **rejected** | Search results, not news |

**This changes M1's shape.** The roadmap assumed RSS would be the primary adapter
and HTML a fallback. The evidence inverts that: **the HTML adapter is now the
primary one**, because the only high-signal source is a web page.

### Before adapters are written

~~One more probe round should confirm the "What's New" page is actually
parseable.~~ **Done — see round 3 below. It is parseable, and the HTML adapter is
confirmed as M1's primary path.**

---

## Round 3: HTML structure — the deciding result

The recommendation rested on an untested assumption: that the Learn "What's New"
page is server-rendered. Round 3 tested it, with the roadmap page included as a
**control** — a page already believed to be a JavaScript app. If the probe could
not tell them apart, its verdicts would be worthless.

| Page | Bytes | Visible text | Ratio | Scripts | H2 | Tables | Dates | Verdict |
|---|---|---|---|---|---|---|---|---|
| `learn-fabric-whats-new` | 308,301 | **131,730** | **0.427** | 6 | 21 | 23 | 177 | **Server-rendered, dated, structured** |
| `learn-powerbi-whats-new` | 60,245 | 10,269 | 0.171 | 6 | 11 | 6 | 8 | **Server-rendered, dated, structured** |
| `learn-fabric-known-issues` | 47,850 | 2,226 | 0.047 | 6 | 5 | 0 | 1 | Marginal — see below |
| `fabric-roadmap-CONTROL` | 196,106 | 2,185 | **0.011** | **35** | 1 | 0 | **0** | **JS app shell — not parseable** |

**The control separated cleanly.** The roadmap page ships 196 KB and yields
2,185 characters of text, 35 script tags and zero dates. The What's New page
ships 308 KB and yields 131,730 characters — a **38× difference in text ratio**.
The probe distinguishes a real page from an app shell, so its verdicts can be
relied on.

### `learn-fabric-whats-new` is confirmed usable

- `ms.date` metadata: **2026-07-30** — current, unlike the corporate blog feed.
- **177 date matches** in `Month YYYY` form: July 2026, June 2026, May 2026,
  March 2026, February 2026, December 2025, November 2025 — a real backlog to
  backfill from.
- **23 tables** and 730 links: the updates are laid out as tables, not prose.
- H2 sections are **feature areas**, not months: `Features currently in preview`,
  `Generally available features`, `Microsoft Fabric platform features`,
  `Continuous Integration/Continuous Delivery (CI/CD)`, `Community`, and —
  notably — **`Power BI`**.

That last heading is independent confirmation of [ADR-0016](../adr/0016-single-fabric-pack-with-power-bi-as-category.md):
**Microsoft puts Power BI inside the Fabric "What's New" page.** The decision to
keep one pack mirrors how the source itself is organised.

### Proposed parsing strategy

```
for each <h2>            → feature area  (becomes a `category` signal)
  for each <table>       → the update rows for that area
    for each <tr>        → one candidate RawItem
       cell with Month YYYY → published_date  (ADR-0005 basis)
       cell with <a>        → title + source_url
```

**One schema nuance to settle before implementation.** The page dates updates to
a *month*, not a day. That is exactly what ADR-0005 needs for the Feature ID, so
identifiers are unaffected. But `published_date` is typed as a full date.
Recording `2026-07-01` for "July 2026" would be quietly false. Options: store the
first of the month and treat `date_confidence: exact` as month-granular, or add
an explicit granularity field. This should be decided in M1 rather than
improvised inside the adapter.

### `learn-fabric-known-issues` should not be trusted yet

The probe labelled it parseable, but only just: 2,226 visible characters against
a 2,000-character shell threshold, ratio 0.047, **zero tables**, and an `ms.date`
of **2025-07-21 — a year stale**. That profile is closer to the roadmap control
than to the What's New page, and suggests the issue list is loaded dynamically.
It is excluded from the proposed source set. The verdict thresholds are a little
too generous here and are worth tightening if this page is revisited.

---

## Open decision: Fabric / Power BI ownership

The probe adds hard evidence to a question that was previously theoretical.

**There is no working Power BI blog feed at all.** The only Power BI–specific
source that passes is `powerbi-docs-commits-atom` — Git plumbing. A Power BI pack
built today would be fed almost entirely by commit noise.

Two further facts bear on this:

- `seen.json` is **per-pack** (`engine/ke/pack.py:138`), so nothing currently
  prevents the same URL being stored in two packs with two permanent IDs.
- `pack.yml` already declares `power-bi-integration` as a category, so a Power BI
  *view* does not require a Power BI *pack* — indexes handle topics, packs handle
  sources and identity.

**Resolved.** One Fabric pack covering Power BI, with `power-bi` as a first-class
category and the `PBI` prefix reserved but unused. Recorded in
[ADR-0016](../adr/0016-single-fabric-pack-with-power-bi-as-category.md), and
independently corroborated by round 3: the Fabric What's New page contains a
`Power BI` section, so the source is organised the same way.


---

# Part 2 — Access diagnostic and the search for a better source

**Date:** 2026-07-31 · [run](https://github.com/sathyarajeshpk/knowledge_engine/actions/runs/30664653569)

## The adapter works against production markup

`ke discover` on a GitHub runner, against the live pages:

```
fabric-whats-new:   336 items
powerbi-whats-new:   25 items
Source health: healthy: fabric-blog, fabric-whats-new, powerbi-whats-new
```

Real extraction with month precision, e.g.
`[2026-05-01 · month/exact] OneLake security and OneLake data access roles are generally available`.
No fallback was triggered and no review item was raised.

**Microsoft Learn is not blocked from GitHub Actions.** The earlier suspicion
was mine, not the evidence's — I misread a job log.

## Access characteristics

| Question | Answer |
|---|---|
| Reachable from a runner? | **Yes.** `ke discover` uses the plain bot User-Agent and fetched 336 items. |
| Header-dependent? | **No.** The production configuration sends only `User-Agent` and `Accept` and works. |
| CDN challenge? | **None detected.** No challenge markers in any response. |
| robots.txt | 765 bytes, 19 `Disallow` rules for `*`. **None matches `/en-us/fabric/fundamentals/whats-new`.** Path permitted. |
| Redirects | The Power BI URL **redirects**: `/power-bi/fundamentals/desktop-latest-update` → `/power-bi/fundamentals/whats-new`. Now pinned to the final URL. |
| IP-range blocking | No evidence. Learn answers from both a GitHub runner and a restricted container. |

## Structured alternatives

| Source | Result |
|---|---|
| `fabric-docs` raw Markdown | **200** · 189,900 bytes · 57 ms |
| `fabric-docs` contents API | **200** · carries `sha: ae3df076…` for change detection |
| `fabric-docs` commit history for that one file | **200** · a ready-made change feed |
| `powerbi-docs` raw Markdown | 200 at `main/powerbi-docs/fundamentals/whats-new.md` (the probe's guessed path 404'd) |
| Learn sitemap | **404** — not published |
| Learn `toc.json` | **404** — not published |

### The Markdown source is real and good

`learn.microsoft.com/fabric` is generated from the public
`MicrosoftDocs/fabric-docs` repository, and the source file is fetchable:

- 189,836 characters, YAML front matter with an authoritative `ms.date: 07/30/2026`
- 18 `##` feature-area sections — the same structure as the rendered page
- 361 table rows, 163 with a dedicated date cell

It has genuine advantages: no CDN, no JavaScript, no markup churn, and Git
history supplies change detection and the Time Machine for free.

### But it is not strictly better

Markdown links are **relative document paths**:

```markdown
[Use Iceberg tables with OneLake](../onelake/onelake-iceberg-tables.md#virtualize-delta-lake-tables-as-iceberg)
```

Identity's strongest signal is the canonical URL (ADR-0023). The rendered HTML
supplies those already resolved and absolute. Markdown would require
reconstructing every URL from a relative path — a deterministic transform, but a
new failure mode sitting directly on top of the mechanism that prevents
duplicate permanent Feature IDs.

Also measured: the **Power BI Markdown has 0 of 31 rows with a date cell**, the
same as its rendered page. The undated Power BI items are a property of the
source, not of the parser.

## Recommendation

**Keep HTML as primary.** It is proven, permitted, unchallenged, and supplies
resolved URLs for identity.

**Promote the raw Markdown to secondary fallback**, replacing the docs-commit
Atom feed. Today's fallback is merge-commit noise carrying no knowledge; the
Markdown carries *the same content as the primary*, from a different host with a
different failure mode. That is what a fallback should be.

## Defect found by this investigation

The adapter searched the whole table row for a date. Row:

```
|**Data Factory gateway manual update (Preview)** | The [Gateway December 2025 release](...) adds ...|
```

That yielded `December 2025`, labelled `EXACT`. ADR-0005 mints a **permanent**
Feature ID from the publication month, so the item would have been filed under
2025-12 forever on the strength of a date scraped out of a sentence.

Measured on the production page: **163 rows have a dedicated date cell, 197 have
no month at all, 1 has a month only in prose.** One row in 361 — rare, silent,
and unfixable afterwards.

Fixed: only a cell containing *nothing but* a date is trusted. Everything else
falls back to the discovery month, which is honest. Both real table shapes are
now pinned by tests.
