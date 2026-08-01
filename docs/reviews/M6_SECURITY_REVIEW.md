# M6 — Security & Vulnerability Review

**Milestone:** M6 — Weekly automation
**Date:** 2026-08-01
**Reviewer perspective:** Senior Software Architect
**Scope:** the whole engine as it stands, with M6's new capabilities in focus

---

## Why this review starts at M6

M6 is the milestone that changes the engine's security position, and it does so
in three ways at once:

1. **It runs unattended.** Nobody is reading the output at the moment something
   goes wrong.
2. **It holds credentials.** An SMTP password, an SMTP account, a recipient
   address, a GitHub token.
3. **It has write permission to the repository**, and two outbound channels
   whose purpose is to carry text out of the run.

Before M6 the engine was a program a person ran on their own machine against
public data. After M6 it is a scheduled process with secrets and a write token
that ingests attacker-influenceable input. That is a different thing, and it
deserves to be reviewed as one.

The organising question throughout is deliberately narrow and concrete:
**what can a hostile source do?**

Everything the engine ingests — titles, summaries, URLs, dates, tags — arrives
from the public internet. None of it is trustworthy merely because Microsoft
published it. A compromised CDN, a hijacked blog, a poisoned cache and a typo'd
URL all deliver the same thing: attacker-controlled text arriving in a process
that holds a write token.

---

## Threat model

### Assets, ranked by how badly losing them hurts

| Asset | Why it matters | Recoverable? |
|---|---|---|
| **User-owned fields** — `learning_status`, `notes`, relationships, `artifacts/` | Hand-written work that exists nowhere else | **No** |
| **Feature ID uniqueness** | IDs are permanent and never reused (ADR-0005); a duplicate cannot be repaired | **No** |
| **Revision history** | The Time Machine's entire value is that it was not rewritten | **No** |
| **SMTP credentials** | Account takeover of the notification mailbox | Yes, by rotation |
| **`GITHUB_TOKEN`** | Write access to the repository for the job's duration | Yes, expires per run |
| **Stored knowledge** | Rediscoverable from sources | Yes, at cost |
| **Derived state** (`seen.json`, indexes) | Regenerable | Yes, cheaply |

### Adversaries considered

| Adversary | Capability | Realistic? |
|---|---|---|
| **A hostile or compromised source** | Controls every string the engine ingests | Yes — the primary threat |
| **A malicious dependency** | Arbitrary code in the harvest process | Yes — two runtime deps, both widely used |
| **A compromised GitHub Action** | Arbitrary code with the job's token | Yes — the standard supply-chain path |
| **An opportunistic reader of published output** | Reads Issues, digests, run logs | Yes — the leak surface |
| **A repository collaborator** | Direct commit access | Out of scope: they already have write access by design |
| **A GitHub platform compromise** | Total | Out of scope: unmitigable at this layer |

### Explicitly out of scope

The repository is private and single-owner. Multi-tenancy, authorisation between
users, and rate limiting are not concerns this design carries. Nor is protecting
against the repository owner, who is the trust root.

---

## 1. Dependency review

**Runtime surface: two packages.**

| Package | Why | Alternative rejected |
|---|---|---|
| `PyYAML>=6.0` | `metadata.yaml` and `pack.yml` | Hand-rolled parsing — worse, and YAML is the storage contract |
| `feedparser>=6.0` | Real-world RSS/Atom is inconsistent in ways a hand-rolled parser discovers slowly | Hand-rolled — a false economy |

Everything else is standard library. HTTP is `urllib.request`; HTML is
`html.parser`; hashing is `hashlib`; email is `smtplib`. `requests` and
BeautifulSoup were considered in M1 and not added, which in hindsight is the
single largest contributor to how small this section is.

**`test_the_runtime_dependency_surface_stays_small`** asserts the declared
dependency set against `{PyYAML, feedparser}`. Adding a third runtime dependency
fails CI. That is deliberate friction: the check exists to force the question,
not to forbid the answer.

**Known weaknesses.**

* No lockfile and no hash pinning. `pip install -e .` resolves the latest
  compatible release at workflow time, so a compromised release of either
  package would execute in the harvest. **Carried as TD-6.**
* No automated CVE scanning. Dependabot is available and not yet enabled.
  **Carried as TD-7.**

Both are real. Neither is severe at this size, and both are cheap to fix in M7.

## 2. Secret scanning and leakage prevention

**Where secrets exist.** Four environment variables, injected by the workflow
from repository secrets: `KE_SMTP_HOST`, `KE_SMTP_USER`, `KE_SMTP_PASSWORD`,
`KE_SMTP_TO`, plus the job's `GITHUB_TOKEN`.

**Blast radius is one package.**
`test_no_credential_is_read_outside_the_notifiers` asserts that no module
outside `ke/notify/` reads `os.environ` or `getenv` at all. The pipeline, the
store, the validator and the classifier cannot see a credential even in
principle. This is what makes a redactor in one module a meaningful control
rather than a hopeful one.

**Redaction is pattern-based, not only value-based** (ADR-0038). Scrubbing known
values protects the secrets the engine holds; patterns also catch the ones it
never held — a connection string in somebody else's exception text, a bearer
token echoed by a server, a password pasted into a config by mistake. Those are
the ones nobody registered, which is why they leak.

Verified live:

```
redact("failed: ghp_abcdefghij0123456789 and https://u:sekrit123@host/x")
→ "failed: [redacted] and https://[redacted]host/x"
```

**Supporting controls.**

* A short secret value is never used for value-based redaction — a redactor that
  turns the whole message into `[redacted]` has destroyed the incident report it
  was protecting.
* The SMTP recipient is masked in success confirmations. Publishing an address
  into an audit trail leaks it for no benefit.
* `test_no_secret_reaches_a_stored_object` asserts no credential value appears
  anywhere under the pack after a harvest run with secrets set.
* Secrets are passed to the harvest step as `env:`, never interpolated into a
  shell command. `test_secrets_are_not_interpolated_into_shell_commands` asserts
  this on every workflow, because `${{ secrets.X }}` inside a `run:` body is how
  a secret ends up in a process listing — and how untrusted input becomes
  command injection.

**Residual risk.** A pattern list cannot be complete. Redaction is defence in
depth, not a guarantee. ADR-0038 says so explicitly rather than implying
otherwise.

## 3. File system safety

**The threat:** a title is used to build a directory name. A source that
controls the title influences a path.

**Control:** `slugify` reduces any string to `[a-z0-9-]` and cannot emit `/`,
`\`, `..`, a null byte or an absolute path.
`test_slugify_never_produces_a_path_separator` runs it over hostile inputs —
`../../etc/passwd`, `..\\..\\windows`, `/absolute/path`, `C:\\Windows`,
strings of dots, a null byte, a 500-character title, and non-Latin scripts —
and asserts the result is confined.

`test_a_hostile_title_cannot_escape_the_pack` then runs a full harvest with each
of those as the item title and asserts every written path resolves inside the
pack.

**Additional controls.**

* `test_a_harvest_writes_nothing_outside_the_pack` snapshots the filesystem
  outside the pack directory before and after a run and asserts it is unchanged.
* Objects are written by rendering **both** documents before writing **either**
  — the fix for the 222-orphan bug in M2 — so an interrupted write leaves
  nothing rather than half an object.
  `test_an_interrupted_object_write_leaves_nothing` proves it by injecting a
  failure between the two.
* The 2026-08 addition to `models.py`: a title is forced onto a single line. A
  title carrying a newline followed by `# ` could otherwise introduce a second
  heading into `feature.md`, letting a source forge structure in a stored
  document and make an object appear to be a different feature than its
  `metadata.yaml` says. Found by this suite. `ke validate` compares the pair and
  would eventually have caught the disagreement, but detection after the fact is
  the wrong layer for input the engine controls entirely.

## 4. Input validation

| Input | Threat | Control |
|---|---|---|
| Title | Path traversal, Markdown forgery | `slugify`; single-line invariant on the type |
| Summary | Repository exhaustion; copyright | `truncate_summary` to the pack's word limit; asserted at ≤130 words after a 200,000-word input |
| URL | `javascript:`, `file://`, `data:`, CRLF header injection, 4 KB URLs | `canonical_url` normalises without crashing; CR/LF assertions |
| Date | Malformed or absurd values | Parsed with explicit precision and confidence; unparseable → `inferred`, never a guess |
| `metadata.yaml` | Deserialisation to arbitrary objects | `yaml.safe_load` everywhere, enforced by scanning |
| `pack.yml` | Same | Same |
| `seen.json`, `id-registry.json` | Corrupt or hostile JSON | See §6 |

**YAML deserialisation** deserves naming explicitly because it is the classic
Python remote-code-execution path. `test_yaml_from_a_source_is_never_executed`
writes `!!python/object/apply:os.system ['echo pwned']` into a stored
`metadata.yaml` and asserts the object is **refused**, not executed.
`test_every_yaml_load_in_the_engine_is_safe` scans every module for any
`yaml.*` call outside `{safe_load, safe_dump, dump, SafeDumper, YAMLError}`,
because one `yaml.load` anywhere is enough.

## 5. CLI safety

* **No shell execution anywhere in the engine.**
  `test_no_shell_execution_anywhere_in_the_engine` scans for `subprocess.`,
  `os.system`, `os.popen`, `eval(` and `exec(`. Command injection has no entry
  point because there is no command.
* **One network egress point.**
  `test_the_engine_makes_no_network_call_outside_the_fetcher` allows `urlopen`
  in exactly three files — the discovery fetcher and the two notifiers — and
  nowhere else. That is what makes the whole engine testable offline, and it
  means the egress surface can be reviewed by reading three files.
* **A command finding no packs is an error.** Fixed in M6: `ke harvest
  --repo-root <wrong path>` used to print nothing and exit 0. A mistyped path
  would have produced a green weekly run, every week, with the engine harvesting
  nothing at all — invisible by construction, and exactly the failure the 60-day
  cron auto-disable rule punishes.
* **`--notify` is off by default.** A local `ke harvest` does not email anyone.

## 6. Corrupted state recovery

The engine's failure policy is deliberately **not uniform**, because the states
have different consequences (ADR-0032):

| State file | If corrupt | Why |
|---|---|---|
| `id-registry.json` | **Stop the run** | The counter is the only thing preventing a duplicate permanent ID. Guessing is unrecoverable. |
| `seen.json` | **Degrade and continue** | Worst case is re-processing an item already known — wasteful, not harmful. |
| A single `metadata.yaml` | **Skip that object, continue** | One damaged object must not cost the other 221. |
| `review-queue.json` | **Degrade and continue** | Queued items are re-discoverable next run. |

Each is asserted: `test_a_corrupt_registry_stops_the_harvest_rather_than_reusing_ids`,
`test_a_corrupt_dedup_cache_does_not_stop_the_harvest`,
`test_an_unreadable_object_does_not_stop_the_harvest`.

`test_a_crash_mid_harvest_leaves_an_id_gap_not_a_dangling_id` covers the
subtler case: a harvest killed after minting leaves a **gap** in the sequence,
never an ID pointing at an object that does not exist. A gap is cosmetic; a
dangling ID is a broken registry.

## 7. Concurrency

Covered in full by ADR-0039. In summary:

* `O_CREAT | O_EXCL` — one atomic syscall, no check-then-create window.
* Released in `finally`, so a crash does not hold it.
* Stale locks reclaimed after an hour; a lock that can wedge the pack forever
  is worse than no lock.
* An unreadable lock is reclaimed, not obeyed — treating "I cannot read this" as
  "somebody is working" is how a system deadlocks on its own garbage.
* Two independent lines of defence: GitHub's `concurrency` group for scheduled
  runs, the lock for everything GitHub cannot see.

Five tests: mutual exclusion, release on exception, stale reclamation,
unreadable-lock reclamation, and that a *fresh* lock is genuinely respected —
the last one matters because without it the other four pass trivially if the
lock never blocks anything.

## 8. GitHub Actions security

| Control | Implementation | Test |
|---|---|---|
| Least privilege | `contents: write`, `issues: write`, nothing else | `test_every_workflow_declares_least_privilege` |
| Only the harvest writes | CI workflow is read-only | `test_only_the_harvest_can_write` |
| No concurrent runs | `concurrency: weekly-harvest`, `cancel-in-progress: false` | `test_the_harvest_cannot_be_run_concurrently_with_itself` |
| Commit containment | `git add domain-packs/` only | `test_the_harvest_only_commits_pack_data` |
| No secret interpolation | `env:` blocks, never `${{ }}` in `run:` | `test_secrets_are_not_interpolated_into_shell_commands` |
| Actions pinned | `@v4` / `@v5`, no floating refs | `test_actions_are_pinned_to_a_major_version` |
| No AI in the schedule | `ke generate` never invoked | `test_the_scheduled_pipeline_never_invokes_a_model` |

**`git add domain-packs/` is a containment boundary, not a convenience.** A
harvest that could commit outside the pack could modify the engine that runs it
next week, or the workflow's own permissions — a self-modifying scheduled job
with a write token. This is the single most important line in the workflow file.

**The push script is tested by running it**, not by reading it
(`test_workflow_push.py`). Every other workflow assertion here is textual, and
textual assertions cannot catch a shell script that is wrong. The push step is
the only shell in M6, runs unattended, and fails by half-publishing a harvest.
The script is extracted from the YAML rather than copied, because a copy drifts
and a test of a copy proves nothing about what runs on Sunday.

Cases covered: an ordinary harvest, a run that wrote nothing (must fail loudly),
a concurrent hand-edit landing between checkout and push, a rejected push, an
unreachable remote, and an assertion that **no path force-pushes**. A
force-push from an unattended weekly job is the one operation in this design
that could actually destroy knowledge.

Each of these was verified by mutation, not by passing:

| Mutation | Test that caught it |
|---|---|
| `git add -A` instead of `git add domain-packs/` | containment |
| `exit 0` on final push failure | rejected-push, rollback |
| All rebasing removed | concurrent hand-edit |

**Actions are pinned to a major version, not a commit SHA.** `@v4` still trusts
the tag owner to not move it maliciously. SHA pinning is stricter and adds
maintenance cost. **Carried as TD-8** with a recommendation to adopt it in M7,
since the same change pairs naturally with dependency pinning.

## 9. Architecture boundary review

Boundaries only hold if something enforces them. Four are enforced by scanning:

1. **The acquisition subsystem** does not import from the knowledge layer
   (ADR-0030), and the mirror-image check ensures the reverse holds too — added
   after a real import cycle in M5 where `normalize.py` reached into
   `acquisition.identity`.
2. **Credentials live only in `ke/notify/`** (§2).
3. **Network egress is confined to three files** (§5).
4. **No shell execution anywhere** (§5).

**All four have been demonstrated to fire, not assumed to.** A scan that greps
for something absent passes whether or not it works, so each was mutation-tested
by introducing the violation it exists to catch:

| Boundary | Mutation | Result |
|---|---|---|
| Credentials outside `notify/` | `os.environ.get(...)` added to `clock.py` | caught |
| No shell execution | `subprocess.run([...])` added to `clock.py` | caught |
| Network egress | *(caught in situ)* the allowlist matched bare filenames and flagged `acquisition/sources/base.py` | caught |
| YAML safety | *(caught in situ)* flagged two legitimate `yaml.YAMLError` references | caught |
| Acquisition subsystem | violation introduced deliberately in M5 | caught |

One limit worth stating: the shell scan matches `subprocess.` — a *call* — not a
bare `import subprocess`. An unused import passes. That is the intended
granularity, since the risk is execution rather than the name being in scope.

Also enforced at import time, in the models themselves: `UPDATABLE_FIELDS` must
be a strict subset of `ENGINE_OWNED_FIELDS`, and `FROZEN_AFTER_MINT` must not
intersect it. Those are assertions that run when the module loads, so the field
ownership model cannot be violated by a later edit that looks reasonable in
isolation.

## 10. Security regression tests

**56 collected in `test_security.py`, 9 in `test_workflow_push.py`.** 465 across
the whole suite, up from 366 at the end of M5.

Counts below are collected tests, so a parametrized case counts once per
parameter — that is what actually runs.

| Section | File | Collected |
|---|---|---|
| Secret leakage prevention | `test_security.py` | 6 |
| File system safety | `test_security.py` | 16 |
| Input validation | `test_security.py` | 9 |
| Concurrent harvest protection | `test_security.py` | 5 |
| Interrupted writes, rollback, corrupted state recovery | `test_security.py` | 5 |
| Notification safety | `test_security.py` | 3 |
| GitHub Actions and supply chain | `test_security.py` | 10 |
| Architecture boundary | `test_security.py` | 2 |
| Git push, rollback and containment | `test_workflow_push.py` | 9 |

Every area the maintainer named for M6 is covered. Git push failures and
rollback live in `test_workflow_push.py` rather than `test_security.py`, because
they are exercised by running the workflow's shell rather than by calling into
the engine.

---

## Findings

### S-1 · Medium · No dependency pinning or hash verification

`pip install -e .` resolves the newest compatible release at workflow time. A
compromised release of PyYAML or feedparser would execute inside the harvest,
with the write token available.

*Mitigating:* two dependencies, both extremely widely used, both with large
downstream blast radii that make a quiet compromise unlikely to stay quiet.

*Recommendation:* add a lockfile with hashes and install with
`--require-hashes` in M7. Tracked as **TD-6**.

### S-2 · Medium · Actions pinned by tag, not SHA

`actions/checkout@v4` trusts the tag to not be moved. This is the standard
supply-chain path into a workflow with a write token.

*Recommendation:* pin to full commit SHAs with a comment naming the version.
Tracked as **TD-8**.

### S-3 · Low · Redaction is best-effort

A pattern list cannot enumerate every credential format. A novel token shape in
an exception message could reach a GitHub Issue.

*Mitigating:* the credential blast radius is one package, asserted; the
repository is private; the GitHub token is per-run.

*Accepted.* Documented in ADR-0038 rather than implied.

### S-4 · Low · No CVE scanning

No automated alerting on advisories for the two dependencies.

*Recommendation:* enable Dependabot alerts and security updates — repository
configuration, no code. Tracked as **TD-7**.

### S-5 · Informational · Stale-lock reclamation is a deliberate trade-off

An hour-old lock is assumed dead. A harvest genuinely running longer than an
hour could be joined by a second one, and both could mint.

*Mitigating:* the workflow's 20-minute timeout makes this impossible on the
scheduled path; a harvest of 222 objects completes in seconds.

*Accepted.* The alternative — never reclaiming — converts one crashed run into a
permanently unusable pack.

### S-6 · Informational · Notification content is not authenticated

A GitHub Issue posted by the workflow is attributable to the token, but the
digest body is assembled from source-derived text. A hostile source could place
misleading text in a notification.

*Mitigating:* the digest is generated from counts and Feature IDs, not from
free-form source text; titles are truncated to 70 characters in tables; there is
no HTML rendering path and therefore no XSS surface. Markdown links from a
hostile title are the residual case, and the single-line invariant limits their
shape.

*Accepted for M6.* Worth revisiting when M7 puts source text into generated
artifacts.

---

## No high-severity findings

Nothing in this review is exploitable to arbitrary code execution, credential
exfiltration, or destruction of user-owned data as the system currently stands.
The two Medium findings are supply-chain hardening — both real, both cheap, both
scheduled for M7 — rather than defects in what M6 built.

Two genuine defects were found *by* this work and fixed within it: the Markdown
forgery vector in titles, and the silent success on a missing pack directory.
Both are in the branch, both have regression tests, and both are stated plainly
in the release notes rather than absorbed quietly.

## What this review does not cover

* **The correctness of the knowledge itself.** Whether a stored object faithfully
  represents its source is a data-quality question, addressed by `ke validate`
  and the review queue.
* **Denial of service against the sources.** The engine polls weekly and reads a
  handful of pages; it is not a scraper worth rate-limiting.
* **The security of GitHub itself**, of the runner image, or of the reader's
  mailbox.
* **Anything after the push.** Once a commit lands, the repository's own access
  controls apply and this engine has no further say.
