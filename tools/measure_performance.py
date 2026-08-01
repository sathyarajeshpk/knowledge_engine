"""Measure the engine against synthetic packs, for the Performance Review.

Run from the repository root::

    python tools/measure_performance.py            # quick: up to 2,000 objects
    python tools/measure_performance.py --big      # adds 10,000

## Why measured rather than reasoned about

Scaling projections written from reading the code are guesses with units
attached. Every number in `docs/reviews/M8_PERFORMANCE_REVIEW.md` comes from
this script, and the script prints what it did so the numbers can be
reproduced or contradicted.

## What it deliberately does not measure

Network time. Discovery is dominated by how fast Microsoft's servers respond,
which is not a property of this engine and varies by an order of magnitude
between runs. Everything here is local work: reading objects, deduplicating,
classifying, rendering indexes, scanning for cross-pack duplicates.

The one number that includes the network is quoted separately in the review and
labelled as such.
"""

from __future__ import annotations

import argparse
import gc
import shutil
import sys
import tempfile
import time
import tracemalloc
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "engine"))

from ke.clock import FrozenClock  # noqa: E402
from ke.models import (  # noqa: E402
    AdapterType,
    DateConfidence,
    DatePrecision,
    ExtractionMethod,
    IdentityConfidence,
    Lifecycle,
    Provenance,
    RawItem,
    SourceAuthority,
    SourceRepresentation,
)
from ke.acquisition.identity import compute_identity  # noqa: E402
from ke.pack import Pack  # noqa: E402

CLOCK = FrozenClock(datetime(2026, 8, 2, 6, 0, tzinfo=timezone.utc))


def make_items(count: int, seed: str) -> list[RawItem]:
    """Distinct, realistic-looking items with durable canonical-URL identity."""
    items = []
    for n in range(count):
        title = f"{seed} feature number {n} is now generally available"
        url = f"https://learn.invalid/{seed}/{n}"
        identity = compute_identity(canonical_url=url, title=title)
        items.append(
            RawItem(
                source_name=f"{seed}-source",
                source_url=url,
                announcement_url=url,
                source_authority=SourceAuthority.OFFICIAL_MICROSOFT,
                title=title,
                summary=f"A short original summary about {title}. " * 4,
                discovered_date=date(2026, 8, 2),
                published_date=date(2026, (n % 12) + 1, 1),
                date_confidence=DateConfidence.EXACT,
                date_precision=DatePrecision.MONTH,
                identity=identity,
                identity_confidence=IdentityConfidence.HIGH,
                lifecycle=Lifecycle.APPROVED,
                provenance=Provenance(
                    source_name=f"{seed}-source",
                    source_representation=SourceRepresentation.HTML,
                    adapter_type=AdapterType.HTML,
                    parser_version=1,
                    extraction_method=ExtractionMethod.HTML_TABLE_ROW,
                    discovered_at=CLOCK.now(),
                    identity_basis=identity.basis,
                    identity_key=identity.key,
                    run_id=CLOCK.run_id(),
                ),
            )
        )
    return items


def build_pack(repo: Path, name: str, prefix: str) -> Pack:
    root = repo / "domain-packs" / name
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        f"name: {name}\nid_prefix: {prefix}\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n"
        "classification:\n"
        "  tier:\n    - name: ga\n      any: [generally available]\n      value: 1\n"
        "  category:\n    - name: p\n      any: [feature]\n      value: platform\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text(f'{{"prefix": "{prefix}"}}\n')
    return Pack.load(root)


def timed(label: str, fn):
    gc.collect()
    start = time.perf_counter()
    result = fn()
    return label, time.perf_counter() - start, result


def measure(objects_per_pack: int, pack_count: int) -> dict:
    """Harvest, then measure each read-side operation separately.

    ## Timing and memory are measured in separate passes, deliberately

    The first version of this function timed everything with `tracemalloc`
    running, because doing both at once looked like one fewer pass. Every
    number it produced was **4-5x too large**: tracemalloc hooks the allocator,
    and this workload allocates constantly. It reported 17s to harvest 100
    objects; the real figure is 3.2s.

    Nothing about that run looked wrong. It completed, the guard confirmed the
    objects were on disk, and the numbers were internally consistent -- they
    were consistently inflated. A benchmark can be precise, reproducible and
    entirely false, which is why timings here run with tracemalloc off and the
    memory pass throws its timings away.
    """
    import ke.pipeline as pipeline_module
    from ke.acquisition import DiscoveryResult
    from ke.artifacts import Coverage
    from ke.crosspack import find_duplicates
    from ke.harvest import harvest_pack, load_objects_with_dirs
    from ke.indexer import write_indexes
    from ke.retrieve import Query, search

    repo = Path(tempfile.mkdtemp())
    (repo / "domain-packs").mkdir()
    # Feature ID prefixes are letters only, which the engine enforces by
    # refusing to mint. The first version of this script used `P00`, `P01`, …
    # and every mint failed with "invalid Feature ID components" — the guard
    # working, and a reminder that a benchmark reporting zero work looks
    # exactly like a benchmark reporting fast work.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    packs = [
        build_pack(repo, f"pack{i}", f"P{alphabet[i % 26]}")
        for i in range(pack_count)
    ]

    # Timed pass. No tracemalloc: see the docstring.
    harvest_seconds = 0.0
    for index, pack in enumerate(packs):
        items = make_items(objects_per_pack, f"pack{index}")
        pipeline_module.discover_all = lambda *a, _i=items, **k: DiscoveryResult(items=_i)
        start = time.perf_counter()
        harvest_pack(pack, clock=CLOCK)
        harvest_seconds += time.perf_counter() - start

    # A benchmark that silently measured nothing is worse than no benchmark:
    # zero work and fast work produce the same numbers.
    written = sum(1 for p in packs for _ in p.knowledge_dir.rglob("metadata.yaml"))
    expected = objects_per_pack * pack_count
    if written != expected:
        raise SystemExit(
            f"benchmark aborted: {written} objects on disk, expected {expected}. "
            "Nothing was measured."
        )

    total = objects_per_pack * pack_count
    results = {
        "objects": total,
        "packs": pack_count,
        "harvest_s": harvest_seconds,
    }

    operations = {
        "load_s": lambda: [load_objects_with_dirs(p) for p in packs],
        "search_s": lambda: [search(p, Query(text="number 7")) for p in packs],
        "index_s": lambda: [
            write_indexes(p.indexes_dir,
                          [(o, "..") for o, _ in load_objects_with_dirs(p)],
                          [], p.name, pack=p)
            for p in packs
        ],
        "coverage_s": lambda: [Coverage.of(p) for p in packs],
        "crosspack_s": lambda: find_duplicates(packs),
    }
    for label, operation in operations.items():
        _, seconds, _ = timed(label, operation)
        results[label] = seconds

    # Memory pass. Timings from this pass are discarded -- they are the ones
    # that were wrong before. Peak RSS of the read side is what matters: it is
    # the figure that decides whether a 100,000-object pack fits on a runner.
    gc.collect()
    tracemalloc.start()
    operations["load_s"]()
    _, peak_read = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    results["peak_read_mb"] = peak_read / 1_048_576

    results["repo_mb"] = sum(
        f.stat().st_size for f in repo.rglob("*") if f.is_file()
    ) / 1_048_576
    results["files"] = sum(1 for f in repo.rglob("*") if f.is_file())

    shutil.rmtree(repo, ignore_errors=True)
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--big", action="store_true", help="include the 10,000-object run")
    args = parser.parse_args()

    shapes = [(100, 1), (500, 1), (1000, 1), (2000, 1), (250, 4), (200, 10)]
    if args.big:
        shapes.append((10000, 1))

    header = (
        f"{'objects':>8} {'packs':>6} {'harvest':>9} {'load':>7} {'search':>7} "
        f"{'index':>7} {'cover':>7} {'xpack':>7} {'peakMB':>7} {'repoMB':>7} {'files':>7}"
    )
    print(header)
    print("-" * len(header))
    rows = []
    for objects, packs in shapes:
        r = measure(objects, packs)
        rows.append(r)
        print(
            f"{r['objects']:>8} {r['packs']:>6} {r['harvest_s']:>8.2f}s "
            f"{r['load_s']:>6.2f}s {r['search_s']:>6.2f}s {r['index_s']:>6.2f}s "
            f"{r['coverage_s']:>6.2f}s {r['crosspack_s']:>6.2f}s "
            f"{r['peak_read_mb']:>6.1f} "
            f"{r['repo_mb']:>6.1f} {r['files']:>7}"
        )

    print("\nPer-object cost at the largest single-pack shape:")
    biggest = max((r for r in rows if r["packs"] == 1), key=lambda r: r["objects"])
    for key in ("harvest_s", "load_s", "search_s", "index_s"):
        per = biggest[key] / biggest["objects"] * 1000
        print(f"  {key:12} {per:.3f} ms/object")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
