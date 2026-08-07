# ADR-0045 — A pack may name web sources and match strings, and nothing else

**Status:** Accepted
**Date:** 2026-08-01
**Relates to:** ADR-0016 (a pack is data), ADR-0040 (no AI in the pipeline)

## Context

ADR-0016's claim is that a Domain Pack is **pure data**, so adding one requires no
engine change and therefore no engine review. M8 proved the first half: the Azure
pack added 200 knowledge objects, 10 categories and 29 classification rules with
`git diff engine/` showing zero files.

The second half was not true yet. A pack is reviewed **as configuration** — a
reviewer scans `pack.yml` looking for plausible taxonomy, not for exploits. The
M8 security review found two things a pack could do that such a reviewer would
not notice, both reachable from a change that looks entirely like data, both
landing in a weekly unattended process holding a repository write token:

* A source URL of `file:///etc/hostname` was fetched, stored as a knowledge
  object, committed and pushed. `urlopen` speaks `file:`, `ftp:` and `data:`.
* A symlink — `domain-packs/x/state -> ../../.git` — redirected every automated
  write out of the pack, while every path the engine built remained well formed.

"Reviewed as data" and "cannot act like code" had not been made the same
statement.

## Decision

**The capability surface of a Domain Pack is closed and enumerated.** A pack may:

| It may | It may not |
|---|---|
| Name `http://` and `https://` sources | Name any other scheme |
| Match literal strings in classification rules | Evaluate anything |
| Declare a 2–4 upper-case-letter ID prefix | Inject path segments through it |
| Set numeric limits and thresholds | — |
| Select notifiers by name from a registry | Construct one |
| Contain knowledge objects, artifacts and images | Contain a symlink leaving its own tree |

Enforced, not documented:

* `SEC001` (ERROR) — any symlink under `domain-packs/` resolving outside it.
  The boundary is `domain-packs/`, not the repository root: `-> ../../.git`
  never leaves the repository and is precisely the case worth stopping.
* `SEC002` (ERROR) — any source the allowlist rejects, reported by forcing the
  lazy `source_definitions` property during validation.
* `PACK003` — the ID prefix grammar.
* Defence in depth at the write path (`store.object_dir`) and the fetch path
  (`HttpFetcher.fetch`), so a machine where nothing was validated is still
  protected.

`ke validate` runs in CI on every pull request, which is where a pack definition
is first seen.

## Consequences

**A pack review has a checklist.** "Are the sources https, are the rules string
matches, are there any symlinks" — and CI answers all three, so a human reviewer
is checking taxonomy quality rather than acting as a security boundary.

**Adding a capability to packs is now an explicit decision.** Anything a future
pack needs to do beyond this table requires amending this ADR. That is the point:
the surface is small because it was chosen, not because nobody has extended it
yet.

**A guard that is never invoked and a guard that does not exist are the same
guard.** SEC002 exists only because an installation-level test ran the real
console script and found the allowlist unreachable from `ke validate`, after
in-process tests were green and the security review already claimed CI caught it.
Every capability restriction here is asserted through the shipped CLI, not only
the library.

**Reading through a symlink is still permitted.** A knowledge tree on another
volume is a legitimate local setup; refusing to read it would break something
real to prevent nothing. CI reports it, so the choice is visible rather than
silent.

## Alternatives rejected

**Denylist dangerous schemes.** The set of schemes the engine legitimately needs
is two and will stay two; an allowlist cannot be outflanked by a scheme nobody
thought of.

**Trust pack review.** The premise of ADR-0016 is that pack review is *not*
engine review. Relying on a reviewer to catch one hostile line in a hundred-line
configuration file gives up the property the abstraction was for.

**Sandbox the harvest.** Disproportionate to the risk, and it would not have
caught either finding — both are the engine doing exactly what it was configured
to do.
