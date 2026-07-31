# ADR-0013: Pluggable notifier interface

**Status:** Accepted
**Date:** 2026-07-31
**Milestone:** M0 (decided) · M6 (implemented)

## Context

The weekly run produces a digest. Something has to deliver it, or the automation
is a tree falling in an empty forest.

Requirements: free, reliable, and durable enough that a digest from two years ago
is still findable. Delivery preferences also change over time — today email, next
year perhaps Telegram or Teams — and that change must not require touching the
pipeline.

Two candidate channels emerged immediately, with complementary strengths:

- **A GitHub Issue** containing the digest. Free, no secrets (the workflow's
  built-in `GITHUB_TOKEN` suffices), permanently searchable in the repository,
  and GitHub emails repository watchers automatically.
- **SMTP via a Gmail app password.** A real email in the inbox with full control
  over subject and formatting, but it requires storing a secret.

## Decision

Implement **both**, behind a **pluggable `Notifier` interface**.

```python
# engine/ke/notify/base.py  (M6)
class Notifier(Protocol):
    def send(self, digest: Digest) -> NotifyResult: ...
```

- `notify/github_issue.py` — the durable audit trail. No secret required.
- `notify/smtp_email.py` — the inbox copy. Uses a repository secret.

Which notifiers run is configured per pack in `pack.yml`:

```yaml
notifiers: [github-issue, smtp-email]
```

Adding Telegram, Slack, Discord or Teams later means writing one module and
adding one name to that list. **No core engine change.**

**Notifier failures are non-fatal.** A failed send is recorded in the run report
and the run still succeeds.

## Consequences

### Positive
- **Two channels with different failure modes.** An expired app password does not
  lose the digest, because the Issue still exists.
- **The Issue is a permanent, searchable archive** of every weekly run, inside
  the repository that is already the single source of truth.
- **New channels are additive.** The Protocol is the contract; the core never
  learns about specific services.
- **Configuration is data.** Turning off email is a `pack.yml` edit.
- **Non-fatal delivery protects the pipeline's real job.** Storing knowledge
  succeeded; failing the run over an SMTP timeout would discard that work and,
  worse, skip the run-log commit that keeps the cron alive.
- **Zero cost**, satisfying the project's budget constraint.

### Negative
- **The SMTP path requires a secret** — a Gmail app password in GitHub Secrets.
  That is a credential to create, store and eventually rotate, and the only
  secret the project has. `docs/RUNBOOK.md` (M9) will cover rotation.
- **Two implementations to maintain** instead of one.
- **Silent delivery failure is possible.** If both notifiers fail, the digest is
  still committed to the repository, but nothing actively tells you. Mitigated by
  the run log recording notifier outcomes.
- **A Protocol adds indirection** for what is currently two implementations.
  Justified by the explicit requirement to add channels later without core
  changes.

### Neutral
- The Issue notifier is GitHub-specific, which is consistent with ADR-0002.
  The Protocol keeps that dependency contained to one module.
- Digest content is identical across channels; only formatting differs.

## Alternatives considered

**GitHub Issue only.** Zero secrets, zero cost, permanent record. Rejected as the
sole channel: it relies on GitHub's watch notifications, which the user may have
tuned down, and it lands in a different place from where you actually read mail.

**SMTP only.** A real email where you already look. Rejected as the sole channel:
no durable archive, and a credential problem silently ends all notification.

**A third-party email API** (Resend, Mailgun, SendGrid). Better deliverability
and nicer templating. Rejected: another vendor account, a free tier that can
change, and no advantage over SMTP for one recipient.

**A hard-coded pair with no interface.** Simpler today. Rejected: the requirement
to add channels later was explicit, and retrofitting an interface after two
call sites are wired into the pipeline is more work than defining it now.

**Failing the run when notification fails.** Rejected: it would discard
successful harvesting work and skip the guaranteed weekly commit that prevents
GitHub disabling the schedule — turning a cosmetic problem into an outage.
