"""What one harvest did.

Lives in its own module so the pipeline stages and the CLI can both depend on
it without depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HarvestReport:
    """What one harvest did. Everything the CLI and the digest need."""

    pack_name: str
    discovered: int = 0
    minted: list[str] = field(default_factory=list)
    queued: int = 0
    already_known: int = 0
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0
    classified: list[str] = field(default_factory=list)
    digest_path: str = ""
    notifications: list[str] = field(default_factory=list)
    notification_failures: list[str] = field(default_factory=list)
    near_duplicates: int = 0
    written_paths: list[str] = field(default_factory=list)
    index_paths: list[str] = field(default_factory=list)
    review_items: list[str] = field(default_factory=list)
    #: The run completed, but something about the pack's configuration means the
    #: result is not what the reader would assume. Kept separate from `errors`
    #: because conflating "this run failed" with "this run succeeded and you
    #: should know something" makes both easier to ignore.
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.minted or self.index_paths)

    def summary_line(self) -> str:
        return (
            f"{self.pack_name}: {self.discovered} discovered, "
            f"{len(self.minted)} minted, {len(self.updated)} updated, "
            f"{self.unchanged} unchanged, {len(self.classified)} classified, "
            f"{self.queued} queued, "
            f"{self.near_duplicates} near-duplicate(s)"
        )
