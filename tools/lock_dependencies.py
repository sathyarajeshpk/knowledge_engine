"""Regenerate `requirements.lock` from what `pyproject.toml` actually resolves to.

Run from the repository root::

    python tools/lock_dependencies.py

## Why a generator rather than a hand-written file

The first version of the lockfile was written by hand from what the two declared
dependencies looked like. It pinned `sgmllib3k`, which feedparser used to depend
on and no longer does — the real transitive dependency is `feedparser-sgmllib`.
`pip install --require-hashes` rejected the file immediately, which was the good
outcome; the bad one would have been a lockfile that installed and pinned the
wrong graph.

So the closure is **discovered**, never asserted. `pip install --dry-run
--report` resolves exactly what a real install would, and this reads the answer.

## Why every published distribution is hashed

pip accepts any one matching hash per requirement. Listing only the wheel that
this machine happens to need would produce a lockfile that works on one runner
and fails on every other platform — including the maintainer's laptop. Listing
all of them costs a longer file and nothing else.

## Why the transitive dependencies are pinned too

`--require-hashes` applies to the whole graph. A lockfile that pins only the
direct dependencies does not install at all, which is at least loud.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import urllib.request
from datetime import date, timezone, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements.lock"

HEADER = """\
# Hash-pinned runtime dependencies for the scheduled harvest.
#
# GENERATED — do not edit by hand. Run `python tools/lock_dependencies.py`.
#
# Why this exists (M6 security review, finding S-1): the weekly workflow runs
# unattended in a process holding a repository write token. `pip install .`
# resolves the newest compatible release at workflow time, so a compromised
# release of any dependency would execute there. Two widely-used packages make
# that unlikely, not impossible.
#
# Installed with `--require-hashes`, so pip refuses anything whose bytes do not
# match. A tampered artifact fails the install rather than running.
#
# Every published distribution of each version is listed — wheels for every
# platform plus the sdist — because pip accepts any one matching hash, and a
# lockfile pinned to one runner's wheel would fail on every other machine.
# Transitive dependencies are pinned too: --require-hashes applies to the whole
# graph, so a lockfile listing only the direct ones does not install at all.
#
# Last generated: {generated}
"""


def resolve_closure() -> list[tuple[str, str]]:
    """Every package a real install would fetch, with its resolved version.

    Uses pip's own resolver rather than reading `pyproject.toml`, so the answer
    is what will actually be installed rather than what the declaration implies.
    """
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp) / "report.json"
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--quiet", "--dry-run", "--ignore-installed",
                "--report", str(report_path), str(REPO_ROOT),
            ],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise SystemExit(f"pip could not resolve the dependencies:\n{result.stderr}")
        report = json.loads(report_path.read_text(encoding="utf-8"))

    packages = []
    for item in report["install"]:
        meta = item["metadata"]
        # The project itself is installed from the working tree and has no
        # published artifact to hash.
        if meta["name"].lower().replace("_", "-") == "knowledge-engine":
            continue
        packages.append((meta["name"], meta["version"]))
    return sorted(packages, key=lambda pair: pair[0].lower())


def published_hashes(name: str, version: str) -> list[str]:
    """Every sha256 PyPI publishes for one release."""
    url = f"https://pypi.org/pypi/{name}/json"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    if version not in data["releases"]:
        raise SystemExit(f"PyPI has no release {name}=={version}")
    digests = {f["digests"]["sha256"] for f in data["releases"][version]}
    if not digests:
        raise SystemExit(f"{name}=={version} publishes no files to hash")
    return sorted(digests)


def render(packages: list[tuple[str, str]]) -> str:
    generated = datetime.now(timezone.utc).date().isoformat()
    lines = [HEADER.format(generated=generated)]
    for name, version in packages:
        digests = published_hashes(name, version)
        lines.append(f"{name}=={version} \\")
        for index, digest in enumerate(digests):
            trailer = "" if index == len(digests) - 1 else " \\"
            lines.append(f"    --hash=sha256:{digest}{trailer}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    packages = resolve_closure()
    print("Resolved:")
    for name, version in packages:
        print(f"  {name}=={version}")

    LOCKFILE.write_text(render(packages), encoding="utf-8")
    total = LOCKFILE.read_text(encoding="utf-8").count("--hash=")
    print(f"\nWrote {LOCKFILE.relative_to(REPO_ROOT)} — "
          f"{len(packages)} package(s), {total} hashes.")
    print("\nVerify with:")
    print("  python -m venv /tmp/lockcheck && "
          "/tmp/lockcheck/bin/pip install --require-hashes -r requirements.lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
