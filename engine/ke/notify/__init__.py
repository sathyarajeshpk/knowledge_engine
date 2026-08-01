"""Telling a human what happened, without being able to break the run.

The rule every notifier obeys (ADR-0013):

    A notifier failure must never fail the harvest.

Knowledge is already committed by the time notification runs. A dead SMTP
server, an expired token or a rate limit must not turn a successful harvest
into a failed workflow -- and, worse, must not stop the run-log commit that
keeps the weekly cron alive.

So `notify_all` catches everything, records it, and returns.

## Secrets

Notifiers are the only part of the engine that touch credentials, which makes
this the one place a secret can leak into a log, a commit or an error message.
Two defences, both in `redact`:

* every notifier's failure message is redacted before it is recorded;
* the redaction is by *pattern*, not by looking up known secret values, so a
  credential the engine has never been told about is still caught.
"""

from ke.notify.base import Notification, Notifier, notify_all, redact

__all__ = ["Notification", "Notifier", "notify_all", "redact"]
