"""Domain Pack loading.

A Domain Pack is pure data: a `pack.yml` plus directories of knowledge objects.
The engine addresses packs by path and holds no knowledge of any specific pack,
which is what allows `engine/` to be extracted into its own repository later
without touching a single pack.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

#: Directory under the repository root where packs live.
PACKS_DIRNAME = "domain-packs"

#: Keys a pack.yml must define as of M0. Later milestones add their own
#: requirements (`sources` in M1, `classification` in M3, `notifiers` in M6);
#: those sections are checked only when present until their milestone lands.
REQUIRED_PACK_KEYS = ("name", "id_prefix", "schema_version")

#: Directories every knowledge object carries, so that attaching the first
#: artifact never has to create structure or move anything.
OBJECT_SUBDIRS = ("artifacts", "images", "references")

DEFAULT_MAX_SUMMARY_WORDS = 120


class PackError(Exception):
    """A pack could not be loaded at all (missing or unparseable pack.yml)."""


@dataclass(frozen=True)
class Pack:
    """One Domain Pack on disk."""

    root: Path
    config: dict[str, Any]

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, root: Path) -> Pack:
        """Load the pack rooted at `root`. Raises `PackError` if unusable."""
        root = Path(root)
        config_path = root / "pack.yml"
        if not config_path.is_file():
            raise PackError(f"no pack.yml in {root}")
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise PackError(f"{config_path} is not valid YAML: {exc}") from exc
        if not isinstance(config, dict):
            raise PackError(f"{config_path} must contain a YAML mapping")
        return cls(root=root, config=config)

    @classmethod
    def find_roots(cls, repo_root: Path) -> list[Path]:
        """Directories under `domain-packs/` that declare themselves a pack.

        Never raises and never parses anything. Callers that must keep going
        when one pack is broken -- `ke validate` across nine packs, say -- use
        this and load each root themselves, so a single malformed `pack.yml`
        cannot suppress every other pack's results.
        """
        packs_dir = Path(repo_root) / PACKS_DIRNAME
        if not packs_dir.is_dir():
            return []
        return sorted(
            child
            for child in packs_dir.iterdir()
            if child.is_dir() and (child / "pack.yml").is_file()
        )

    @classmethod
    def discover(cls, repo_root: Path) -> list[Pack]:
        """Load every pack under `<repo_root>/domain-packs`, sorted by name.

        The weekly workflow iterates over whatever this returns, so adding a
        pack is a matter of creating a directory -- no workflow edit required.

        Raises `PackError` if any pack is unloadable. Use `find_roots()` when
        one bad pack must not stop the others.
        """
        return [cls.load(root) for root in cls.find_roots(repo_root)]

    # -- configuration ---------------------------------------------------

    @property
    def name(self) -> str:
        return str(self.config.get("name") or self.root.name)

    @property
    def id_prefix(self) -> str | None:
        prefix = self.config.get("id_prefix")
        return str(prefix) if prefix else None

    @property
    def schema_version(self) -> int | None:
        version = self.config.get("schema_version")
        return int(version) if isinstance(version, int) else None

    @property
    def source_definitions(self) -> list[Any]:
        """Configured sources, in declaration order.

        Imported lazily so `ke.pack` stays free of adapter dependencies -- the
        validator loads packs without ever needing feedparser.
        """
        from ke.acquisition.sources.base import SourceDefinition

        return [
            SourceDefinition.from_config(entry)
            for entry in (self.config.get("sources") or [])
        ]

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(self.config.get("categories") or ())

    @property
    def max_summary_words(self) -> int:
        limits = self.config.get("limits") or {}
        return int(limits.get("max_summary_words", DEFAULT_MAX_SUMMARY_WORDS))

    # -- paths -----------------------------------------------------------

    @property
    def knowledge_dir(self) -> Path:
        return self.root / "knowledge"

    @property
    def indexes_dir(self) -> Path:
        return self.root / "indexes"

    @property
    def digests_dir(self) -> Path:
        return self.root / "digests"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def near_duplicate_jaccard(self) -> float:
        """Title-similarity threshold above which items are flagged, never dropped.

        Configuration rather than a constant because the right value depends on
        how a vendor words its announcements, and that is pack knowledge.
        """
        dedupe = self.config.get("dedupe") or {}
        try:
            return float(dedupe.get("near_duplicate_jaccard", 0.85))
        except (TypeError, ValueError):
            return 0.85

    @property
    def registry_path(self) -> Path:
        return self.state_dir / "id-registry.json"

    @property
    def seen_path(self) -> Path:
        return self.state_dir / "seen.json"

    @property
    def run_log_path(self) -> Path:
        return self.state_dir / "run-log.md"

    @property
    def source_health_path(self) -> Path:
        return self.state_dir / "source-health.json"

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.jsonl"

    # -- contents --------------------------------------------------------

    def iter_object_dirs(self) -> Iterator[Path]:
        """Yield every knowledge object directory, in ID order.

        Yields any directory at `knowledge/<year>/<month>/<object>` regardless
        of whether it is well formed, so that a directory missing its
        `metadata.yaml` is reported rather than silently skipped.
        """
        if not self.knowledge_dir.is_dir():
            return
        for year_dir in sorted(p for p in self.knowledge_dir.iterdir() if p.is_dir()):
            for month_dir in sorted(p for p in year_dir.iterdir() if p.is_dir()):
                yield from sorted(p for p in month_dir.iterdir() if p.is_dir())

    def relative(self, path: Path) -> str:
        """Path relative to the pack root."""
        try:
            return str(Path(path).relative_to(self.root))
        except ValueError:
            return str(path)

    def location(self, path: Path) -> str:
        """Repository-relative path, for use as a `Finding` location.

        Includes the containing directory and the pack directory
        (`domain-packs/microsoft-fabric/knowledge/...`) so that findings stay
        **globally unique across packs**. A pack-relative path would render two
        different files identically once a second pack exists, and would let a
        reporter group unrelated findings together.
        """
        return f"{self.root.parent.name}/{self.root.name}/{self.relative(path)}"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upwards from `start` to the repository root.

    Identified by a `domain-packs` directory or a `.git` directory, so the CLI
    works from anywhere inside a checkout.
    """
    current = Path(start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / PACKS_DIRNAME).is_dir() or (candidate / ".git").is_dir():
            return candidate
    return current
