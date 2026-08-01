"""Post the digest as a GitHub Issue: the durable, zero-configuration channel.

Chosen as the primary channel because it needs no secret of its own -- Actions
supplies `GITHUB_TOKEN` -- and because an Issue is a permanent, searchable record
that survives a lost inbox.

Writes through the GitHub REST API using stdlib `urllib`, so notification adds no
dependency to a project that has two.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from ke.notify.base import Notification

GITHUB_API = "https://api.github.com"


@dataclass
class GitHubIssueNotifier:
    """Opens one Issue per digest."""

    repository: str
    token: str
    labels: tuple[str, ...] = ("knowledge-engine", "digest")
    name: str = "github-issue"
    timeout: int = 20

    @classmethod
    def from_environment(cls) -> GitHubIssueNotifier | None:
        """Build from the environment Actions provides, or `None` if absent.

        Returning `None` rather than raising means running locally without a
        token simply skips this channel instead of failing the harvest.
        """
        repository = os.environ.get("GITHUB_REPOSITORY")
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not repository or not token:
            return None
        return cls(repository=repository, token=token)

    def send(self, notification: Notification) -> str:
        payload = json.dumps(
            {
                "title": notification.subject,
                "body": notification.body,
                "labels": list(self.labels),
            }
        ).encode("utf-8")

        request = urllib.request.Request(
            f"{GITHUB_API}/repos/{self.repository}/issues",
            data=payload,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "knowledge-engine",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        return f"issue #{body.get('number', '?')}"
