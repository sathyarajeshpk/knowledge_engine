# ADR-0007: Separate `feature.md` and `metadata.yaml`

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0

## Context

A knowledge object has two kinds of content: prose a human reads (the summary and
why it matters) and ~30 structured fields a program reads (IDs, dates, hashes,
classification, learning state, relationships, generation tracking).

The near-universal convention in static-site and note-taking tools is **YAML front
matter**: metadata in a `---` block at the top of the Markdown file. One file per
object, one thing to keep in sync.

Our metadata is unusually large — around 50 lines including the revision history
and seven generation entries. Front matter of that size would push the actual
knowledge below the fold in every viewer.

## Decision

Keep them in **two files**:

- `feature.md` — the canonical knowledge article. Starts with an `# ` heading and
  contains only prose.
- `metadata.yaml` — all structured fields.

`ke validate` enforces that the pair agrees: the `# ` heading must match the
`title` field (`CONS002`), and the article body must stay within the pack's word
limit (`COPY001`).

## Consequences

### Positive
- **The knowledge stays readable.** Opening `feature.md` in the GitHub UI shows a
  short article, not 50 lines of YAML followed by three paragraphs.
- **Machine access needs no Markdown parsing.** Any tool in any language can read
  `metadata.yaml` directly. Front matter requires splitting on `---` first, which
  is a small but real parsing dependency.
- **Diffs are cleaner and more meaningful.** A weekly run that updates
  `content_hash` touches only `metadata.yaml`; the knowledge file shows as
  unchanged. With front matter, every metadata change would appear as a change to
  the knowledge article.
- **Ownership maps onto files.** `metadata.yaml` is engine-managed with
  user-owned fields inside it; `feature.md`, `artifacts/`, `images/` and
  `references/` are increasingly user territory. The boundary is visible in the
  filesystem.
- **Editing metadata by hand is pleasant** — a YAML file with comments, not a
  block wedged above prose.

### Negative
- **The two files can drift.** This is the real cost, and it is why `CONS001` and
  `CONS002` exist. A title changed in one file and not the other is caught by CI
  rather than by hope.
- **Two files to write atomically.** M2's `store.py` must handle the case where
  one write succeeds and the other fails.
- **Two files to open** when inspecting an object manually.
- **Slightly unconventional.** Contributors familiar with Jekyll or Obsidian will
  expect front matter, so the reasoning is written down here.

### Neutral
- Doubles the file count per object, which is irrelevant given ADR-0006 already
  makes each object a directory.
- `KnowledgeObject.to_metadata_dict()` writes fields in explicit human-readable
  order rather than declaration order, which matters more when the file stands
  alone.

## Alternatives considered

**YAML front matter in a single file.** The conventional choice, and genuinely
tempting for its familiarity and atomicity. Rejected on the volume argument: ~50
lines of front matter buries a ~120-word article, and every metadata-only update
would appear as a diff against the knowledge file. The drift risk we accepted in
exchange is mechanically checkable; the readability loss would not have been
recoverable.

**JSON instead of YAML for metadata.** Stricter, universally parseable. Rejected:
no comments, no readable multi-line strings, and hostile to hand-editing —
which matters because learning state is maintained by a human. JSON is used for
`state/` files, which are machine-only.

**TOML instead of YAML.** Better typing, no whitespace significance. Rejected:
nested structures (the revision list, the generation map) are awkward in TOML,
and the project already uses YAML for `pack.yml`.

**Metadata in a per-pack database or a single index file.** Rejected: breaks the
"everything about one object is in one place" property from ADR-0006, and
reintroduces a central file that every write must contend over.
