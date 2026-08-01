# ADR-0038: Redact what looks like a secret, not only what we know is one

**Status:** Accepted
**Date:** 2026-08-01
**Milestone:** M6

## Context

M6 gives the engine credentials for the first time: an SMTP password, an SMTP
username, a recipient address, and a GitHub token. It also gives it two outbound
channels — a GitHub Issue and an email — whose entire purpose is to carry text
out of the run and put it somewhere a human will read.

Those two facts together are the risk. Everything the engine reports flows
through a channel that publishes, and the most common thing a failing system
reports is an exception message. Exception messages are written by libraries,
not by us, and libraries interpolate whatever they were handed:

```
SMTPAuthenticationError: (535, b'5.7.8 Username and Password not accepted')
URLError: <urlopen error [Errno -2] Name or service not known: smtp://user:hunter2@host>
```

The obvious defence is to collect the values of the environment variables the
engine reads and scrub those strings from anything it emits. It is easy,
targeted and cheap.

It is also insufficient in the case that actually matters. Scrubbing known
values only protects secrets **the engine holds**. It does nothing about a
credential the engine never had: a connection string embedded in a source URL, a
token pasted into a `pack.yml` by mistake, a bearer header echoed by a library,
a password that arrives inside somebody else's error text. Those are exactly the
ones nobody has thought to register, which is why they leak.

## Decision

**Redaction is pattern-based first and value-based second.**

`ke.notify.base.redact()` applies:

* **`SECRET_PATTERNS`** — shapes that are secrets regardless of provenance:
  GitHub tokens (`ghp_`, `github_pat_`, `gho_`, …), `scheme://user:pass@host`
  credentials in URLs, `Authorization: Bearer …`, Slack tokens, AWS access key
  IDs.
* **`SECRET_ENV_VARS`** — the values the engine is known to hold, scrubbed by
  exact match as a backstop.

Two supporting rules:

* **Short values are never used for value-based redaction.** A three-character
  secret would turn every message into `[redacted]`, and a redactor that
  destroys the whole message has denied the reader the incident report it was
  supposed to protect.
* **The SMTP recipient is masked in confirmations.** Reporting "notified
  `sathyarajeshpk@gmail.com`" in a public-ish audit trail leaks an address for
  no benefit; the confirmation only needs to say a channel succeeded.

`notify_all` never raises. A notifier that fails is recorded as a failure and
the run continues — the knowledge is already committed by then, and losing a
harvest because an SMTP server was down would be an absurd trade.

## Consequences

**Redaction is best-effort and this ADR says so.** A pattern list cannot be
complete. It is a defence in depth, not a guarantee, and it does not replace not
putting secrets in messages in the first place. The value it adds is over the
class of leak nobody predicted, which is the class that happens.

**False positives are possible and acceptable.** A string that merely looks like
an AWS key ID will be redacted from a digest. The cost is one unreadable token
in a report; the cost the other way is a published credential.

**One blast radius.** `test_security.py` asserts that no module outside
`ke/notify/` reads `os.environ` or `getenv` at all, so credentials cannot spread
into the pipeline, the store or the validator. That boundary is what makes a
redactor in one module a meaningful control rather than a hopeful one.

## Alternatives considered

**Value-based scrubbing only.** Simpler, no false positives, and it covers the
secrets we configured. Rejected: it protects only what was registered, and an
unregistered credential is the definition of the leak you did not see coming.

**Never include exception text in notifications.** Genuinely safer, and
rejected on usefulness: a notification saying "the harvest failed" with no
detail forces a human into the Actions log to learn anything, which is precisely
the friction that makes people stop reading notifications.

**Redact at the logging layer instead.** Rejected as the wrong boundary. The
engine's risk is not what it logs — the runner's logs are already private — it
is what it *publishes*. Putting the control at the publishing boundary keeps the
protected surface small enough to audit.
