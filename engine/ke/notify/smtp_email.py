"""Email the digest over SMTP. The inbox copy, not the durable record.

Secondary to the GitHub Issue on purpose: this is the channel with a secret, an
external dependency and an expiry date. When it breaks -- and an app password
will eventually be rotated or revoked -- the Issue is still there.
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from ke.notify.base import Notification


@dataclass
class SmtpNotifier:
    """Sends the digest as a plain-text email."""

    host: str
    port: int
    username: str
    password: str
    recipient: str
    name: str = "smtp-email"
    timeout: int = 20

    @classmethod
    def from_environment(cls) -> SmtpNotifier | None:
        """Build from `KE_SMTP_*`, or `None` when not configured.

        Absent configuration is not an error: a user who has not set up email
        should not see a failed notification every week.
        """
        host = os.environ.get("KE_SMTP_HOST")
        user = os.environ.get("KE_SMTP_USER")
        password = os.environ.get("KE_SMTP_PASSWORD")
        recipient = os.environ.get("KE_SMTP_TO") or user
        if not (host and user and password and recipient):
            return None
        return cls(
            host=host,
            port=int(os.environ.get("KE_SMTP_PORT", "587")),
            username=user,
            password=password,
            recipient=recipient,
        )

    def send(self, notification: Notification) -> str:
        message = EmailMessage()
        message["Subject"] = notification.subject
        message["From"] = self.username
        message["To"] = self.recipient
        message.set_content(notification.body)

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
            server.starttls()
            server.login(self.username, self.password)
            server.send_message(message)
        return f"sent to {_mask(self.recipient)}"


def _mask(address: str) -> str:
    """`s***@example.com`. The confirmation goes into a public run log."""
    name, _, domain = address.partition("@")
    if not domain:
        return "[redacted]"
    return f"{name[:1]}***@{domain}"
