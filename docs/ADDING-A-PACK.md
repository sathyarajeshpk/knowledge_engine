# Adding a Domain Pack

A Domain Pack is a directory of data. Adding one requires **no engine change** —
proven in M8, where the Azure pack added 200 knowledge objects, 10 categories and
29 classification rules with zero files changed under `engine/`.

This document is written from what that actually cost, including the parts that
went wrong.

---

## Before you start: is it a pack, or a category?

**A pack is a source boundary and an identifier namespace** (ADR-0016). The test
is not "is this a distinct topic" — it is:

> **Does it have its own sources?**

If the announcements you want would arrive through a feed an existing pack
already harvests, it is a **category within that pack**, not a pack. Making it a
pack means every announcement is discovered twice and minted under two permanent
Feature IDs, and the cross-pack duplicate queue fills with pairs that are not
mistakes.

This is why Power BI is a category in the Fabric pack rather than a `PBI` pack,
despite the original plan naming one (ADR-0043).

---

## The trust boundary

A pack is reviewed **as data**. To make that safe, what a pack can do is a closed
set (ADR-0045):

| A pack may | A pack may not |
|---|---|
| Name `http://` and `https://` sources | Name any other scheme — `file:`, `ftp:`, `data:` are refused (SEC002) |
| Match literal strings in classification rules | Evaluate anything |
| Declare a 2–4 upper-case-letter ID prefix | Put path separators in it |
| Set numeric limits and thresholds | — |
| Select notifiers by name | Construct one |
| Contain knowledge, artifacts and images | Contain a symlink leaving its own tree (SEC001) |

`ke validate` enforces all of these in CI. You do not need to remember them; you
need to know that a red build on `SEC001` or `SEC002` means the pack is trying to
reach outside itself.

---

## Steps

### 1. Create the directory and `pack.yml`

```
domain-packs/<name>/
├── pack.yml
└── state/
    └── id-registry.json      # {"prefix": "XYZ"}
```

Minimum viable `pack.yml`:

```yaml
name: snowflake
id_prefix: SNF                 # 2-4 upper-case letters, permanent, never reused
schema_version: 1

limits:
  max_summary_words: 120       # copyright guard: summaries and links, never full text

sources:
  - name: snowflake-releases
    adapter: rss               # rss | sitemap | html
    url: https://example.invalid/releases.rss
    authority: third-party     # official-microsoft | microsoft-community | third-party

categories:
  - data-loading
  - governance

classification:
  tier:
    - name: ga
      any: [generally available]
      none: [preview]          # read the warning below before omitting this
      value: 1
  category:
    - name: loading
      any: [copy into, snowpipe]
      value: data-loading
```

Pick the ID prefix carefully. **It is permanent and never reused**, including for
replaced objects.

### 2. Harvest

```bash
ke harvest --pack snowflake
```

The weekly workflow picks the pack up automatically — it globs `domain-packs/`
and needs no edit.

### 3. **Read the knowledge it produced.** Do not skip this.

This is the step that matters, and it is the one the engine cannot do for you.

> **A successful pipeline execution does not guarantee correct output.**

`ke harvest` will exit 0 having produced 200 objects that are all wrong. Check,
at minimum:

```bash
ke search --pack snowflake --tier 1 | head -30   # are these really "act now"?
cat domain-packs/snowflake/indexes/review-queue.md
grep -c "category: null" domain-packs/snowflake/knowledge/**/metadata.yaml
```

Open five objects at random and read them against their source URLs.

### 4. Validate

```bash
ke validate
```

Runs in CI on every pull request. `--pack` narrows the per-object output but
never switches off the security checks.

---

## What went wrong when Azure was added

Every one of these produced a clean, successful run.

### The `none:` guard, learned twice

Azure phrases previews as *"this feature is now available in public preview"*.
The GA rule matched `generally available`… and so did *"now generally available
in public preview"*. **Every preview classified as tier 1 — act now.**

The Fabric pack already carried `none: [preview]` on its GA rule. The lesson had
to be learned a second time because vocabulary is pack knowledge and does not
transfer.

> **Rule of thumb:** for any rule that means "finished", add a `none:` listing the
> words your vendor uses for "not finished". Substring matching will happily fire
> inside a phrase that negates it.

### Invented vocabulary

The first `pack.yml` used `contains:` for match lists. The real keys are `any:`
and `none:`. Nothing rejected the unknown key, so 200 objects were minted with no
classification at all — a clean run producing uniformly empty metadata.

Check the review queue and the `unclassified` count after a first harvest. A
large unclassified count usually means the rules are not matching at all rather
than that the source is unusual.

### Retirement announcements from years back

Azure's feed carries retirements dated well in the past. This worked correctly —
`AZ-2025-09-001` landed in a 2025 directory — but it is worth knowing that a new
pack's first harvest may create knowledge objects across many past months, and
that this is right rather than a bug.

---

## Cross-pack behaviour, once you have two

The engine will notice when two packs hold what may be the same feature and will
**report it, never act on it** (ADR-0044). Both objects are kept; neither is
modified.

```bash
ke review list --kind cross-pack
ke review resolve AZ-2026-07-038+MSF-2026-07-012
```

An acknowledgement is stored once, at the repository root
(`state/cross-pack.json`), keyed on the canonical sorted pair — so resolving from
either side clears it for both, and it is not re-surfaced next week.

Relationships may point across packs. `prerequisites: [AZ-2026-04-001]` in a
Fabric object is valid and validated; a typo is still caught as `REF001`.

---

## What is *not* a data change

Be honest with yourself about these before promising a pack is "just
configuration":

* **A source that needs a new adapter.** Adapters are `rss`, `sitemap`, `html`.
  A vendor publishing only a paginated JSON API needs a fourth — engine code,
  through the `Source` protocol.
* **Classification that needs logic.** Rules are string matching. A domain
  needing numeric version comparison needs an engine change, and should get one
  rather than a mini-language in YAML.
* **A new index.** Index renderers live in `engine/ke/indexer.py`.

---

## Cost, measured

From the M8 Performance Review, so you can size the commitment:

| | |
|---|---|
| First harvest, 1,000 objects | ~32 s |
| Weekly harvest thereafter (~20 new) | ~1 s |
| Index rebuild, 1,000 objects | ~12 s |
| Repository growth | ~14 KB and 2 files per object |

One caveat that grows with pack count rather than knowledge: index rebuild is
currently O(packs²), so each additional pack costs a little more than the last.
At two packs it is immeasurable; the M8 Performance Review recommends addressing
it before the fifth.
