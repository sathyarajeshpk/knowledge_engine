# Vision

## The short version

Knowledge Engine is a **model-independent Knowledge Operating System**.

It is not a Microsoft Fabric project. Microsoft Fabric is the first Domain Pack
— the proving ground. The engine underneath is a general system for turning a
firehose of scattered, perishable information into a structured, durable,
personally-owned body of knowledge that any AI model can read and that no AI
vendor controls.

---

## Why this exists

### The problem with how technical knowledge works today

If you work in a fast-moving platform ecosystem — Fabric, Azure, Databricks,
Snowflake, whatever comes next — you face the same four problems:

**1. The information arrives faster than you can absorb it.**
Microsoft ships Fabric updates monthly. Power BI ships monthly. Azure ships
continuously. Nobody reads all of it, so everyone reads none of it and finds out
about breaking changes from a broken pipeline.

**2. What you learn evaporates.**
You read a good article, you understand it, and eleven months later you cannot
remember whether Direct Lake supported that thing, or which release it landed
in, or where you read it. Bookmarks rot. Notes apps become write-only.

**3. Your knowledge is trapped inside someone's chat history.**
You had an excellent conversation with an AI model that explained a concept
perfectly. That conversation is in one vendor's product, in one account, in a
format you do not control, subject to a retention policy you did not write. Move
vendors and it is gone.

**4. Learning has no state.**
There is no answer to "what have I actually learned, what am I mid-way through,
and what should I study next given what I already know?" Every learning tool
either tracks progress but owns your content, or holds content but tracks
nothing.

### What we are building instead

A system where:

- **Knowledge is discovered automatically**, on a schedule, from sources you
  trust, without you remembering to check.
- **Knowledge is stored as plain files** in a Git repository you own —
  greppable, diffable, portable, readable in twenty years.
- **Every unit of knowledge has a permanent identity** so it can be referenced,
  linked, and reasoned about over time.
- **Learning state lives beside the knowledge** — what you have learned, what is
  in progress, what depends on what — and the automation can never overwrite it.
- **AI is invited in on demand**, given a self-contained context pack, and asked
  to produce a tutorial, a quiz, a LinkedIn post, an interview drill. Then it
  leaves. It is a consumer of your knowledge base, never its owner.

---

## The core insight

> **Separate the knowledge from the intelligence.**

Most AI-era knowledge tools fuse the two. Your notes live inside the assistant;
the assistant's embeddings *are* the index; changing vendors means starting over.
That fusion is convenient for six months and a liability afterwards.

Knowledge Engine keeps them apart:

| Layer | What it is | Who owns it | Lifespan |
|---|---|---|---|
| **Knowledge** | Markdown + YAML in Git | You | Decades |
| **Structure** | IDs, indexes, relationships, learning state | Deterministic code | Decades |
| **Intelligence** | Whatever model you feel like using today | Rented, replaceable | Months |

The knowledge layer does not know or care which model you use. The engine that
maintains it contains no AI calls at all. That is not a limitation we are working
around — it is the point.

**The test of whether we got this right:** if every AI vendor alive today
disappeared tomorrow, the repository would still be a complete, structured,
useful body of knowledge, and the engine would keep maintaining it.

---

## Goals

### 1. Durability over convenience

Files in Git, not a database. Plain Markdown, not a proprietary format. The
knowledge must outlive the engine, the vendor, the format fashion, and the
author's interest in maintaining any of them.

### 2. Zero marginal cost

The target is ₹20/month; the actual cost is ₹0. This is not penny-pinching — it
is a design constraint that forces good decisions. A system with no running cost
does not get switched off during a quiet quarter, does not need a business case,
and does not create pressure to monetise your own notes back to you.

Concretely: no database, no server, no paid API, no vendor account. GitHub's free
tier and a weekly cron.

### 3. The automation must never damage your work

A weekly job rewrites these files. You maintain learning state in the same files.
Without a hard rule, the automation eventually destroys something you wrote.

So field ownership is enforced in code: the engine raises `PermissionError`
rather than overwriting anything you own. This is treated as the system's most
important safety property, not a nice-to-have.

### 4. Model independence, structurally

Not "we support multiple providers behind an adapter". No AI call exists in the
scheduled pipeline at all. Generation happens by handing you a self-contained
Markdown context pack that you paste into whatever model you like.

This is enforced by CI, from M0, before the scheduled workflow it constrains even
exists.

### 5. Learning as a first-class concept

Most knowledge bases store documents. This one stores documents *plus* your
relationship to them: difficulty, workload, what you have learned, what a topic
requires you to understand first. The prerequisite graph produces a learning path
automatically. That is knowledge management crossed with a curriculum.

### 6. Simplicity that survives contact with time

Prefer boring, obvious mechanisms. A regex for IDs. A JSON file for counters.
Directory names that encode their own meaning. The measure is whether someone
opening this repository in five years, with no context, can understand it from
the files alone.

---

## Design philosophy

### Rules must be mechanisms, not documentation

A rule that lives only in a document is a rule that will be broken. Every
important rule here is enforced by something that fails loudly:

| Rule | Mechanism |
|---|---|
| Knowledge is never deleted | `ObjectStatus` has no `deleted` member — the state is unrepresentable |
| The engine never writes your fields | `with_engine_fields()` raises `PermissionError` |
| Ownership classes never overlap | Three `assert`s that break the import |
| No duplicate Feature IDs | `ke validate` fails CI |
| No AI in the scheduled pipeline | A CI step greps every scheduled workflow |

If you find yourself writing "we should remember to…", you have found a missing
mechanism.

### Determinism is a feature

The pipeline is a pure function: same inputs, same outputs. This makes it
testable, debuggable, reproducible, free, and vendor-independent — five benefits
from one constraint.

### Data over code

Categories, limits, thresholds, sources and classification rules live in
`pack.yml`. Tuning behaviour should be a text edit, not a release. The engine
contains no mention of Microsoft Fabric anywhere — verifiable with one `grep`.

### Nothing is ever lost, only marked

Corrections append revisions. Superseded objects are marked `replaced`. Stale
artifacts are marked `stale`. Near-duplicates are flagged for review rather than
dropped. Git gives us history; the schema gives us *legible* history.

### Build the guardrails before the thing they guard

M0 shipped no pipeline — only the schema, the ID rules, the ownership model, the
validator and CI. Those are the things that are nearly impossible to retrofit
once data exists.

---

## The long view

### Phase 1 — One pack, proven (M0–M7)

Microsoft Fabric, end to end: discovery, storage, classification, relationships,
revisions, weekly automation, retrieval, on-demand generation. Prove the model
works for one domain.

### Phase 2 — Many packs, same engine (M8–M9)

Power BI proves the abstraction: adding a Domain Pack must require zero engine
changes. Then SQL, Python, Azure, Databricks, Snowflake, AWS, and Personal
Knowledge. Cross-pack relationships let a Fabric concept declare a SQL
prerequisite.

At this point the thing stops being "a Fabric knowledge base" and becomes a
**personal knowledge operating system** with a Fabric pack installed.

### Phase 3 — The engine as a product

`engine/` is already a standalone installable package with no knowledge of any
specific pack. Extracting it into its own open-source repository is a folder
move, planned from M0. Anyone could then run their own Domain Packs — legal
research, medicine, a company's internal platform docs — against the same engine.

The knowledge stays private. The engine can be public. That split is deliberate.

### Phase 4 — Knowledge as durable infrastructure

The long-term ambition is unglamorous and specific: a body of structured
knowledge that is **still useful in twenty years** because it is plain text in
Git with stable identifiers, and that can be handed to whatever intelligence
exists at that point — a model nobody has built yet, or a person.

Models will keep getting better and keep getting replaced. Formats will churn.
Vendors will consolidate and disappear. Markdown in Git with permanent IDs is
one of the few bets in this space that ages well.

---

## What this is deliberately not

**Not a note-taking app.** It maintains structure around knowledge; it does not
try to be where you think.

**Not a RAG system.** No embeddings, no vector database. Retrieval is `grep` and
regenerated Markdown indexes. When that stops being enough, the files are still
the source of truth and indexing them is somebody's afternoon.

**Not an AI wrapper.** The engine contains zero AI calls. That is the defining
constraint, not a limitation.

**Not a SaaS product.** No server, no account, no subscription, no telemetry.
It is a repository and a cron job.

**Not automatic content generation.** Tutorials and posts are generated when you
ask, from knowledge you have curated. The system will never quietly fill your
repository with machine-written text you did not request.
