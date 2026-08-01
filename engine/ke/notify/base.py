"""The notifier contract, and the redaction every notifier's errors pass through."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

#: Environment variables that hold credentials. Their *values* are redacted
#: wherever they appear, which catches a secret echoed back by a remote service.
SECRET_ENV_VARS = (
    "KE_SMTP_PASSWORD",
    "KE_SMTP_USER",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

#: Patterns that look like credentials regardless of where they came from.
#: Pattern-based rather than value-based on purpose: a token the engine was
#: never told about -- one echoed in a server's error reply, say -- is still
#: caught. A value-based redactor only protects secrets it already knows.
SECRET_PATTERNS = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}", re.IGNORECASE),      # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9._%+-]+:[^@\s/]{6,}@"),                  # user:pass@host
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{16,}", re.IGNORECASE),
    re.compile(r"\bAuthorization:\s*\S+", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}", re.IGNORECASE),     # Slack
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                              # AWS key id
)

REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Remove anything that looks like a credential.

    Applied to every notifier failure before it is recorded, because those
    messages end up in run logs and workflow output, and an SMTP library will
    happily include the connection URL -- password and all -- in an exception.

    Known secret values are redacted first, then patterns. A short value is
    skipped: redacting a two-character username would mangle unrelated text for
    no security benefit.
    """
    if not text:
        return text
    cleaned = text
    for name in SECRET_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            cleaned = cleaned.replace(value, REDACTED)
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(REDACTED, cleaned)
    return cleaned


@dataclass(frozen=True)
class Notification:
    """What every notifier is asked to deliver."""

    subject: str
    body: str
    pack_name: str


@runtime_checkable
class Notifier(Protocol):
    """One delivery channel. Adding Telegram or Slack is one of these."""

    name: str

    def send(self, notification: Notification) -> str:
        """Deliver, returning a short confirmation. Raise to signal failure."""
        ...


def notify_all(
    notifiers: list[Notifier], notification: Notification
) -> tuple[list[str], list[str]]:
    """Deliver through every channel. Returns `(delivered, failures)`.

    Never raises. Knowledge is already committed by this point, so a
    notification failure is an inconvenience, not a reason to fail a run — and
    failing here would also suppress the run-log commit that keeps the cron
    alive (ADR-0019).
    """
    delivered: list[str] = []
    failures: list[str] = []
    for notifier in notifiers:
        try:
            delivered.append(f"{notifier.name}: {notifier.send(notification)}")
        except Exception as exc:  # noqa: BLE001 - see the docstring
            failures.append(redact(f"{notifier.name}: {type(exc).__name__}: {exc}"))
    return delivered, failures
