# M7 — Security & Vulnerability Review

**Milestone:** M7 — Retrieval and on-demand generation
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Baseline:** [`M6_SECURITY_REVIEW.md`](M6_SECURITY_REVIEW.md)

---

## What changed in the threat position

M6 was the milestone that gave the engine credentials and a write token. M7 adds
much less attack surface than it looks like it should, for one structural reason:

> **`ke generate` makes no network call, and the engine holds no AI credential.**

There is no API client, no key, no endpoint, no request signing, and no response
parsing. `ke generate` prints text; `--attach` reads a file or stdin. The entire
AI integration is a human with a clipboard (ADR-0040).

That removes, before it exists, the class of vulnerability this milestone would
otherwise have introduced: leaked model API keys, prompt content sent to a third
party by an automated path, and untrusted model output written to disk without a
person seeing it.

What M7 *does* add:

| New | Security relevance |
|---|---|
| `ke search` / `ke get` | Read-only. No new writes, no new inputs. |
| `ke generate` | Reads templates and objects; writes nothing without `--attach`. |
| `--attach` | **The one new write path**, taking arbitrary user-supplied content. |
| `requirements.lock` | Closes M6's largest finding. |
| `.github/dependabot.yml` | Closes M6's S-4. |
| `test_packaging.py` | Installs a package in CI — worth reviewing as a capability. |

---

## Status of the M6 findings

| M6 | Finding | M7 status |
|---|---|---|
| **S-1** | No dependency pinning or hash verification | **Closed** |
| **S-2** | Actions pinned by tag, not SHA | **Open** — blocked, see below |
| **S-3** | Redaction is best-effort | Accepted, unchanged |
| **S-4** | No CVE scanning | **Closed** |
| **S-5** | Stale-lock reclamation trade-off | Accepted, unchanged |
| **S-6** | Notification content not authenticated | Re-examined below |

### S-1 closed — dependencies are hash-pinned

`requirements.lock` pins every package in the resolved graph with every sha256
PyPI publishes for that version, and the weekly workflow installs with
`--require-hashes`. A tampered artifact now fails the install rather than
executing in a job that holds a write token.

Two details worth recording, because both were mistakes worth not repeating:

**The lockfile is generated, not written.** The hand-written first version pinned
`sgmllib3k` — which feedparser used to depend on and no longer does; the real
transitive dependency is `feedparser-sgmllib`. pip rejected the file outright,
which was the good outcome. The bad one would have been a file that installed
cleanly while pinning the wrong graph. `tools/lock_dependencies.py` now
*discovers* the closure with pip's own resolver rather than asserting it.

**Every published distribution is hashed, not just this runner's wheel.** pip
accepts any one matching hash. Pinning only the `cp311-manylinux` wheel would
produce a lockfile that works in CI and fails on every other machine, including
the maintainer's laptop — which is how a security control gets removed for being
annoying.

Verified by installing into a clean virtualenv with `--require-hashes`.

### S-4 closed — Dependabot watches both ecosystems

`.github/dependabot.yml` covers `pip` and `github-actions`, weekly, opening pull
requests only. Nothing updates automatically: an unattended weekly job that could
also update its own dependencies would be a strictly worse version of the problem
being solved.

### S-2 remains open, and the reason is a constraint rather than a judgement

Pinning `actions/checkout@v4` to a commit SHA requires reading the
`actions/checkout` repository. **This environment cannot reach it** — GitHub
access is scoped to `sathyarajeshpk/knowledge_engine`, and cross-owner access is
not available in this session.

I could have written SHAs from memory. I did not, and would not: an unverified
SHA replaces a weak guarantee with a broken build and a false sense of a strong
one. A workflow pinned to a hash that does not exist fails on the next scheduled
run, at 06:00 on a Sunday, for a reason that looks nothing like its cause.

**Recommended action for whoever has access**, in one step:

```bash
gh api repos/actions/checkout/commits/v4 --jq .sha
gh api repos/actions/setup-python/commits/v5 --jq .sha
gh api repos/actions/upload-artifact/commits/v4 --jq .sha
# then: uses: actions/checkout@<sha>  # v4
```

Dependabot is configured for `github-actions` in the meantime, so a moved tag or
a new release surfaces as a pull request. Tracked as **TD-8**, carried.

### S-6 re-examined — and it grew

M6 accepted this as informational, with the note: *worth revisiting when M7 puts
source text into generated artifacts.* M7 does exactly that, so here is the
revisit.

**The concern.** A context pack contains source-derived text — titles, summaries,
tags — assembled into an instruction that a human pastes into a model. A hostile
source could place text in a summary that reads as an instruction rather than as
content.

**Why it is still Low rather than Medium.**

* **A human is between the source and the model, by design.** The person pastes
  the pack and reads what comes back. This is the same property that makes
  generated artifacts trustworthy, applied to a different risk.
* **The instruction comes first and the knowledge is fenced beneath a `#
  Knowledge` heading**, with an explicit closing line telling the model to produce
  only the described artifact. That is not a security boundary — nothing about
  prompt structure is — but it is not nothing either.
* **The blast radius is one artifact**, which a person reads before attaching.
  There is no automated path from a hostile summary to stored content.
* **Summaries are truncated to the pack's word limit** (ADR-0003), which caps how
  much an attacker can say.

**What I am not claiming.** Prompt injection through a poisoned source is not
*prevented*. It is bounded by there being no automated path, and detected by a
human reading the output. If a future milestone ever automates generation, this
finding immediately becomes the most serious one in the file.

Recorded as **S-6 (revised), Low** with that condition attached.

---

## New findings

### S-7 · Low · `--attach` writes arbitrary content to a path the template chooses

`--attach` takes a file or stdin and writes it into the object's directory at the
path declared in the template's `output` field.

**What is controlled.** The path comes from the template, not from user input,
and templates ship inside the package. `test_generate.py` asserts every `output`
begins with `artifacts/` or `images/`. The content itself is Markdown written to
a `.md` file — never executed, never parsed as configuration, never rendered as
HTML by anything in this system.

**What was not, and now is.** A malicious *template* could have declared
`output: ../../../../etc/whatever`. Templates are part of the engine and arrive
through the same review as any code change, so this was a code-integrity concern
rather than an input-validation one — but the check is one line, so it was
written rather than deferred.

`load_template` now refuses any `output` that is absolute, contains `..`, or
falls outside `artifacts/` and `images/`. `references/` is refused along with the
traversals: it holds the user's own supporting notes, and nothing generated
belongs there.

**Closed in this milestone.** Six parametrised cases in
`test_a_template_cannot_write_outside_the_object_directory`.

### S-8 · Low · The packaging test installs from the network in CI

`test_packaging.py` runs `pip install .` into a throwaway virtualenv, which
resolves and downloads dependencies from PyPI **without** `--require-hashes`.

**Why it is Low.** It runs in the CI job, which holds no secrets beyond a
read-scoped token, not in the weekly harvest. The installed package is discarded.
The value it provides is high: it is the only test that catches a defect in what
actually ships, and it caught one immediately.

**Recommendation:** point it at `requirements.lock` so even the test path is
hash-verified, once that does not slow CI unacceptably. Tracked as **TD-14**.

### S-9 · Informational · Prompt templates are executable-adjacent content

The templates are instructions that a model will follow. A change to one changes
what every future artifact is asked to be, and a subtly bad instruction is much
harder to notice in review than a subtly bad function.

**Existing controls.** Templates live in the package and change through pull
requests. Two tests scan them: one asserts no vendor-specific syntax (ADR-0004),
one asserts every template forbids inventing facts — the single most damaging
thing any of them could omit.

**Not a vulnerability**, recorded because "text that steers an AI" is a category
this codebase now contains and did not before.

---

## Unchanged controls, re-verified

| Control | Test |
|---|---|
| No shell execution anywhere in the engine | `test_no_shell_execution_anywhere_in_the_engine` |
| Network egress confined to three files | `test_the_engine_makes_no_network_call_outside_the_fetcher` |
| Credentials read only inside `notify/` | `test_no_credential_is_read_outside_the_notifiers` |
| `yaml.safe_load` everywhere | `test_every_yaml_load_in_the_engine_is_safe` |
| Least-privilege workflow permissions | `test_every_workflow_declares_least_privilege` |
| The harvest commits only `domain-packs/` | `test_the_harvest_only_commits_pack_data` |
| No AI in the scheduled pipeline | `test_the_scheduled_pipeline_never_invokes_a_model` |
| **New:** the pipeline cannot import the generation code | `test_the_pipeline_cannot_reach_the_generation_code` |

The last one is M7's addition and closes a gap the workflow scan could not see.
Verified by mutation: adding `from ke.generate import build_pack` to
`pipeline.py` fails it.

`generate.py` and `retrieve.py` read `yaml` through `safe_load` and make no
network calls, so they inherit the existing boundaries rather than widening them
— confirmed by the unchanged scans above, which cover every module.

---

## Findings summary

| | Severity | Status |
|---|---|---|
| S-2 Actions pinned by tag, not SHA | Medium | **Open — blocked by session repo scope**, TD-8 |
| S-3 Redaction is best-effort | Low | Accepted (ADR-0038) |
| S-5 Stale-lock window | Info | Accepted (ADR-0039) |
| S-6 Prompt injection via a poisoned source | Low | Accepted, **conditional** — see above |
| S-7 `--attach` output path unvalidated at load time | Low | **Closed in M7** |
| S-8 Packaging test installs unpinned in CI | Low | TD-14 |
| S-9 Templates are AI-steering content | Info | Recorded |

**No high-severity findings. One Medium, carried, blocked for a stated reason.**

The net security position improved this milestone: the largest M6 finding is
closed, and the largest thing M7 could have introduced — an AI API integration
with a credential and an automated write path — was designed out rather than
secured.
