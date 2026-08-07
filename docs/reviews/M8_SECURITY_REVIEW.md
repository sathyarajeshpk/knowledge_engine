# M8 — Security & Vulnerability Review

**Milestone:** M8 — the second Domain Pack
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** the trust boundary M8 created — packs as reviewable data — and
everything that crosses it

---

## What changed about the threat model

M6 and M7 reviewed an engine with one pack, written by the person running it.
M8's premise is different and is the whole point of the milestone (ADR-0016):

> A Domain Pack is **pure data**. Adding one requires no engine change, and
> therefore no engine review.

That sentence is an architectural win and a security statement at the same time.
It means `pack.yml` and the knowledge tree are reviewed **as configuration** — a
reviewer scans 29 classification rules and six source entries looking for
plausible taxonomy, not for exploits. Anything a pack can do that a reviewer
would not notice is a vulnerability created by the abstraction.

So this review asks one question of every mechanism:

> **What is the most damaging thing a pack definition can do, to someone who
> reviewed it as data?**

The context that sets severity: the weekly workflow runs unattended on a
disposable runner, holding a `GITHUB_TOKEN` with repository write access, and it
commits and pushes what the pipeline produced.

Two findings were exploitable. Both are fixed.

---

## S-1 — A pack could read local files into stored knowledge · **CRITICAL** · fixed

`HttpFetcher` called `urllib.request.urlopen` with whatever URL a source
declared. `urlopen` speaks `file:`, `ftp:` and `data:` as happily as `http:`.
Nothing validated the scheme.

```yaml
sources:
  - name: release-notes
    adapter: rss
    url: file:///etc/hostname          # fetched, parsed, stored, committed
    authority: third-party
```

Verified against the real fetcher before the guard was written — it returned the
file's contents. The pipeline would then have stored them as a knowledge object,
and the weekly workflow would have committed and pushed it.

**Why this is the most serious finding in the milestone.** It is a
local-file-read-to-exfiltration chain reachable from a change that looks
entirely like configuration, in a process holding a write token, on a runner
whose workspace contains the checkout and the workflow's own event payload. One
line among dozens in a file nobody reads as code.

**Fix.** An allowlist of `http://` and `https://`, checked in two places for two
different reasons:

* `SourceDefinition.from_config` — rejects the URL when the source is built.
* `HttpFetcher.fetch` — checked again, because the fetcher is reachable directly
  from adapters and any future caller. A guard living only at the configuration
  boundary protects only the configuration boundary.
* `ke validate` → **SEC002**, ERROR — see below.

**The third layer exists because the first two were not enough, and the first
draft of this review said they were.** `Pack.source_definitions` is a *lazy
property*: the allowlist fires only when something asks for the sources.
Validation never did. So a pack declaring `file:///etc/hostname` reported
`ok: 1 pack(s), 0 knowledge object(s), no findings` and would have failed at
03:00 on Sunday instead — inside the process holding the write token, which is
the single place it must not first be discovered.

This was found by an installation-level test running the real console script,
after the in-process tests were green and this document already claimed CI
caught it. The guard was real; the path to it was not. `_check_pack_config` now
forces the property and reports any failure as SEC002.

An allowlist rather than a denylist: the set of schemes this engine legitimately
needs is two, and it will stay two. Comparison is case-insensitive, because
schemes are case-insensitive per RFC 3986 and rejecting `HTTPS://` would be a
false positive — the failure mode that gets a security guard deleted rather than
fixed.

Mutation-verified; a test also asserts every source in both shipped packs
complies, so the guard is not retroactively breaking real configuration.

**Residual.** The allowlist does not address SSRF to internal addresses — a pack
could name `http://169.254.169.254/`. On GitHub-hosted runners there is no cloud
metadata service worth reaching, and IP-range blocking is disproportionate to
that. Noted, not fixed.

---

## S-2 — A symlink in a pack could redirect automated writes · **HIGH** · fixed

No symlink handling existed anywhere in the engine.

Nothing in the CLI concatenates attacker-controlled strings into a path —
`--pack` filters an already-discovered list **by name** rather than joining a
path, and object paths are built from validated Feature IDs — so there is no
classic `../../etc/passwd` traversal to find. That reasoning is sound and
insufficient: a path can be entirely engine-derived and still land outside the
pack, because a *directory component* of it is a link.

```
domain-packs/x/state    -> ../../.git        # legal git object, survives clone
domain-packs/x/knowledge -> /somewhere/else
```

Every path the engine then builds is well formed. State, knowledge, indexes and
digests all follow the link.

**Fix.** `ke.paths.contained()` asks one question — does this path, with every
symlink followed, sit inside the boundary? — so the answer does not depend on
having predicted the attack. Three layers use it:

| Layer | Behaviour | Covers |
|---|---|---|
| `Pack.find_roots` | Refuses to treat a symlinked directory as a pack | A redirected pack root |
| `store.object_dir` | Refuses at write time | A redirected knowledge tree, on a machine where nothing was validated |
| `ke validate` → **SEC001**, ERROR | Reports every escaping link | Both, on the PR that introduces them |

**The boundary is `domain-packs/`, not the repository root**, and that
distinction is the finding. `state -> ../../.git` never leaves the repository and
is exactly the case worth stopping; engine-owned paths are *inside* the
repository, so a check that only caught escapes from it would catch nothing that
matters.

SEC001 runs regardless of `--pack`. A security check a flag can switch off is one
that will be switched off — and because `find_roots` refuses a symlinked pack
root, a per-pack check would never see it: refused by one layer, reported by
none.

Reading through a symlink is deliberately **not** blocked. A knowledge tree on
another volume is a legitimate setup, and refusing to read it would break
something real to prevent nothing. CI still reports it, so the choice is visible
rather than silent.

The first version of the write-time guard checked the object path against the
very argument it was built from, which proved only that `..` does not appear in a
Feature ID. Its own test caught it. All five guards are mutation-verified,
including the `startswith` containment bug and the unresolved-root false
positive.

---

## S-3 — Prompt generation from stored content · **LOW** · mitigated, not solved

`ke generate` builds one document containing a task and then third-party prose
summarised from pages the engine does not control. A source writing *"ignore the
above and instead ..."* into a title was, structurally, indistinguishable from
the task — the task is also prose, in the same document, in the same voice.

**What was done.** `build_pack` now emits an explicit boundary before the
knowledge section: everything below is reference data, not instructions.

**What that is worth, stated honestly.** It does not prevent prompt injection.
Nothing at this layer can, and claiming otherwise would be worse than not having
it. It gives a model something to honour and makes the intent explicit to the
human reading the pack.

Emitted from the engine rather than added to the seven templates: a per-template
instruction is one a new template can forget, and forgetting it would be
invisible — the pack would render and look complete.

**Why the severity is genuinely low.** The containment is architectural and
predates this milestone:

* **No AI runs in the scheduled pipeline** (ADR-0040, CI-enforced). An injected
  instruction can never trigger an automated action, because nothing automated
  reads a context pack.
* **A human is in the loop by construction.** The pack is pasted into a model by
  hand and the output is read before `--attach` writes it.
* **Summaries are truncated** to the pack's word limit, capping the payload.
* **No credential exists anywhere near this path** — the engine holds no AI API
  key at all.

The worst realistic outcome is a tutorial containing content an attacker chose,
which a human reviews before storing. That is a content-quality problem, not a
compromise.

---

## Reviewed and found sound

### Cross-pack trust boundaries

A cross-pack duplicate is *reported*, never acted on. `_act_on_cross_pack`
records an acknowledgement and modifies neither object — asserted by
`test_acknowledging_modifies_neither_object`. No pack's data can cause a write to
another pack's objects, which is the property that makes packs independent rather
than merely separate.

The resolution store lives at `state/cross-pack.json`, repo-level, outside every
pack, keyed on the canonical sorted pair. A fact about two packs stored inside
one of them is a fact that will eventually disagree with its copy.

A corrupt resolution store degrades to "re-show the decision" rather than
stopping the run — the right trade, because the worst case is annoyance.
Contrast the ID registry, which stops the harvest, because guessing there is
unrecoverable.

### YAML injection

Every one of the six YAML loads in the engine is `yaml.safe_load`, asserted by a
test that greps the source (`test_every_yaml_load_in_the_engine_is_safe`) rather
than trusting review. Dumping uses a `SafeDumper` subclass. `!!python/object`
constructors are unreachable.

A pack whose `pack.yml` is unparseable becomes a `PACK005` finding and the other
packs still validate — a malformed pack cannot suppress every other pack's
results.

### Markdown and front-matter injection

Titles are collapsed to a single line at model construction
(`RawItem.__post_init__` and `KnowledgeObject.__post_init__`, both using
`single_line`), so a title containing newlines and `---` cannot forge front
matter or a document structure. This was an M6 finding, fixed then, and it holds
for the Azure pack's titles unchanged — a useful signal that the fix was
structural rather than pack-specific.

Prompt template `output:` paths are validated by `_is_safe_output` against
absolute paths, backslashes, `..` components, and anything outside `artifacts/`
or `images/`.

### Arbitrary file writes

`test_a_harvest_writes_nothing_outside_the_pack` and
`test_the_cli_never_writes_outside_a_pack` were already in place; S-2 closes the
symlink route they could not see. Slugs can never produce a path separator
(`test_slugify_never_produces_a_path_separator`).

### Pack loading

`--pack` filters a discovered list by name. There is no path join, so there is no
traversal — the mechanism is structurally immune rather than defended.

### Supply chain for pack definitions

Beyond S-1, a `pack.yml` controls: the ID prefix (validated letters-only, so it
cannot inject path segments), classification rules (pure string matching against
stored text, no evaluation), limits (numeric), and notifier selection (chosen
from a registry by name, not constructed). Runtime dependencies remain two
pure-Python packages, hash-pinned via `requirements.lock` and installed with
`--require-hashes`.

---

## Findings summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| S-1 | A pack source could name `file://` and read local files into stored, committed knowledge | **Critical** | Fixed — http/https allowlist at three layers (config, fetcher, SEC002 in CI), mutation-verified |
| S-2 | A symlink in a pack could redirect every automated write | **High** | Fixed — containment at load, at write, and SEC001 in CI |
| S-3 | Stored third-party prose sits in the same document as the task in a context pack | Low | Mitigated with an explicit data boundary; architecturally contained |
| S-4 | SSRF to internal addresses remains possible via an `http://` URL | Informational | Accepted — no metadata service worth reaching on a GitHub runner |

---

## What M8 changed about the security posture

The milestone's architectural claim — that a pack is data and needs no engine
review — was **true in the sense that mattered** (zero engine files changed for
the Azure pack) and **not yet safe to rely on**. Both exploitable findings exist
precisely because "reviewed as data" and "cannot act like code" had not been
made the same statement.

They are now closer, and the mechanism is checkable: `ke validate` runs on every
pull request and refuses a pack that names a non-web URL (SEC002) or links
outside its own tree (SEC001). That is what makes the abstraction's promise
something a reviewer can rely on rather than something they have to personally
verify.

One methodological note that changed a finding rather than confirming one. Both
S-1 and S-2 were fixed, tested and written up before an installation-level test
ran the real console script — and that test found the S-1 fix was unreachable
from `ke validate`, the exact path this document claimed protected it. The
lesson generalises past M7's packaging bug: **a guard that is never invoked and
a guard that does not exist are the same guard.** Testing the library proves the
former; only testing the shipped command distinguishes them.

**Recommendation for M9:** when `docs/ADDING-A-PACK.md` is written, state the
trust boundary explicitly — a pack may name web sources and match strings, and
nothing else — so the next pack author knows what the review is checking for.
