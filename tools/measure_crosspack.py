"""Measure how index rebuild scales with pack count, and rule on decision gate A.

Run from the repository root::

    python tools/measure_crosspack.py --baseline    # record the pre-change state
    python tools/measure_crosspack.py               # compare against it, print verdict

## Why this exists separately from `measure_performance.py`

That tool answers "what does the engine cost?" across many operations. This one
answers a single question with a decision attached:

> Does computing cross-pack duplicates once per run, instead of once per pack,
> reduce index rebuild from O(packs²) to O(packs)?

M9 decision gate A turns that answer into PROCEED, REVISE or ABANDON. The
thresholds live in this file as constants, committed **before** any architectural
code exists, so the bar cannot be moved after the numbers arrive. That is the
whole point of writing the measurement first.

## Why read counts are the primary signal, not wall-clock

Wall-clock is what we care about, and it is also what fooled us in M8: the first
benchmark ran clean, passed its own abort guard, and was wrong by 4-5x because it
timed everything with `tracemalloc` attached. A clean run is not a correct result.

Full-pack read counts have no such failure mode. They are integers produced by
counting calls, they do not vary between runs, and the current design has a known
closed form (`packs²`) that this harness verifies against before trusting
anything else it measures. Wall-clock is reported alongside as confirmation that
the reads were actually the cost -- if reads go linear and time does not move,
the reads were never the bottleneck and the gate says REVISE rather than PROCEED.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "engine"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from measure_performance import CLOCK, build_pack, make_items  # noqa: E402

#: Where `--baseline` writes, so before/after comparison is mechanical rather
#: than a matter of remembering what the old numbers were.
BASELINE_PATH = REPO_ROOT / "docs" / "reviews" / "M9_gate_a_baseline.json"

#: Pack counts to measure. 1 is the control: the current design does zero
#: cross-pack reads with a single pack, so it isolates the per-object cost from
#: the per-pack-count cost.
PACK_COUNTS = (1, 2, 4, 8, 10)
OBJECTS_PER_PACK = 200

#: The large shape, run last because it is slow. Kept separate so a quick run
#: still produces the full curve.
LARGE_SHAPE = (10, 1000)

# ---------------------------------------------------------------------------
# Decision gate A thresholds -- FIXED 2026-08-07, BEFORE any implementation
# ---------------------------------------------------------------------------
#
# Do not edit these to match a result. If a threshold turns out to be wrong,
# that is a conversation to have explicitly, recorded in the plan, not a number
# to quietly relax. The commit that changes one should say why in its message.

#: Reads must become linear in pack count. `2 * packs` allows the harvest-side
#: read plus one detection read per pack without permitting anything quadratic.
LINEAR_READ_BUDGET = lambda packs: 2 * packs  # noqa: E731

#: Wall-clock improvement at the 10-pack shape required to PROCEED.
PROCEED_SPEEDUP = 0.50

#: Below this, the reads were not the cost. REVISE rather than PROCEED.
REVISE_SPEEDUP = 0.25

# Wall-clock is compared on a MACHINE-SPEED-NORMALISED basis: the 10-pack index
# time divided by the 1-pack index time from the same run.
#
# Why, with the evidence that forced it. The merged baseline recorded 68.99s at
# 10 packs. A later run of the *identical* code recorded 80.47s -- 17% drift,
# nearly twice the 9% previously observed, and enough to move a borderline
# verdict on its own. Normalising against the 1-pack row removes it:
#
#     merged baseline : 68.99 / 1.59 = 43.39
#     later run       : 80.47 / 1.90 = 42.35   (-2.4%)
#
# The 1-pack row is the right control precisely because it performs ZERO
# cross-pack reads, so the change under test cannot affect it. Any movement in
# it is machine speed, not architecture.
#
# The THRESHOLDS above are unchanged. Only the basis of comparison is. This was
# added before any implementation existed, so it cannot have been fitted to a
# result -- which is the whole reason to do it now rather than after.


class ReadCounter:
    """Counts full-pack reads by instrumenting `ke.harvest.load_objects_with_dirs`.

    Patching the module attribute works because `crosspack.py` imports the
    function *inside* each function body rather than at module scope, so the
    lookup happens at call time. That is load-bearing: `pipeline.STAGES` holds
    direct function references and is famously NOT affected by patching, and
    confusing the two would produce a confident count of zero.
    """

    def __init__(self) -> None:
        self.count = 0
        self._real = None

    def __enter__(self) -> ReadCounter:
        import ke.harvest as harvest

        self._real = harvest.load_objects_with_dirs

        def counted(pack):
            self.count += 1
            return self._real(pack)

        harvest.load_objects_with_dirs = counted
        return self

    def __exit__(self, *exc) -> None:
        import ke.harvest as harvest

        harvest.load_objects_with_dirs = self._real


def build_repo(objects_per_pack: int, pack_count: int) -> tuple[Path, list]:
    """A synthetic repository, harvested, ready to re-index."""
    import ke.pipeline as pipeline_module
    from ke.acquisition import DiscoveryResult
    from ke.harvest import harvest_pack

    repo = Path(tempfile.mkdtemp())
    (repo / "domain-packs").mkdir()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    packs = [
        build_pack(repo, f"pack{i}", f"P{alphabet[i % 26]}") for i in range(pack_count)
    ]

    for index, pack in enumerate(packs):
        items = make_items(objects_per_pack, f"pack{index}")
        pipeline_module.discover_all = lambda *a, _i=items, **k: DiscoveryResult(items=_i)
        harvest_pack(pack, clock=CLOCK)

    written = sum(1 for p in packs for _ in p.knowledge_dir.rglob("metadata.yaml"))
    expected = objects_per_pack * pack_count
    if written != expected:
        raise SystemExit(
            f"aborted: {written} objects on disk, expected {expected}. "
            "Nothing was measured."
        )
    return repo, packs


def measure_shape(
    objects_per_pack: int, pack_count: int, *, with_memory: bool = False
) -> dict:
    """Rebuild every pack's indexes once, counting reads and wall-clock.

    `with_memory` is off by default and deliberately so. The memory pass runs
    the whole rebuild a second time under `tracemalloc`, which is 4-5x slower --
    measuring it at every shape took the full curve past ten minutes for a
    number that only matters at the shape the gate turns on. It is requested for
    the 10-pack row and skipped elsewhere.
    """
    from ke.harvest import load_objects_with_dirs
    from ke.indexer import write_indexes

    repo, packs = build_repo(objects_per_pack, pack_count)
    try:
        # Snapshot the objects BEFORE instrumenting, so the loads this harness
        # performs for its own setup are not counted as engine work.
        loaded = {p.name: [(o, "..") for o, _ in load_objects_with_dirs(p)] for p in packs}

        def rebuild():
            for pack in packs:
                write_indexes(
                    pack.indexes_dir, loaded[pack.name], [], pack.name, pack=pack
                )

        gc.collect()
        with ReadCounter() as counter:
            start = time.perf_counter()
            rebuild()
            elapsed = time.perf_counter() - start

        # Memory in a SEPARATE pass, timings discarded. tracemalloc hooks the
        # allocator and inflated every M8 timing by 4-5x; measuring both at once
        # is the exact mistake that milestone made.
        peak = 0
        if with_memory:
            gc.collect()
            tracemalloc.start()
            rebuild()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

        return {
            "packs": pack_count,
            "objects_per_pack": objects_per_pack,
            "objects": objects_per_pack * pack_count,
            "reads": counter.count,
            "index_s": elapsed,
            "peak_mb": peak / 1_048_576 if with_memory else None,
        }
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def verify_baseline_law(rows: list[dict]) -> None:
    """Confirm the harness reproduces the known `packs²` law before it is trusted.

    M8 measured the current design at exactly `packs²` full-pack reads. If this
    harness disagrees on the unchanged code, the harness is wrong -- and a
    broken harness that reports plausible numbers is precisely the failure this
    milestone is trying not to repeat.

    Only meaningful in `--baseline` mode; after the change the law is expected
    to break, which is the entire point.

    The law is `packs²` **from two packs upward, and zero at one**, because
    `cross_pack_tasks` returns early when there is no other pack to be
    cross-pack with. The first version of this function asserted `packs²`
    throughout and fired on the single-pack row -- the check catching its own
    author, which is the correct outcome and the reason it runs before anything
    else here is believed.
    """
    problems = []
    for row in rows:
        packs = row["packs"]
        expected = packs * packs if packs >= 2 else 0
        if row["reads"] != expected:
            problems.append(f"  {packs} packs: counted {row['reads']}, expected {expected}")
    if problems:
        raise SystemExit(
            "aborted: the harness does not reproduce the packs^2 law measured in "
            "M8 on unchanged code.\n" + "\n".join(problems) + "\n"
            "The instrumentation is wrong, or the engine changed. Either way "
            "nothing measured here can be trusted yet."
        )


def verdict(baseline: dict, current: dict) -> tuple[str, list[str]]:
    """Rule on decision gate A. Returns (PROCEED|REVISE|ABANDON, reasons)."""
    reasons: list[str] = []

    by_packs = {r["packs"]: r for r in current["rows"]}
    base_by_packs = {r["packs"]: r for r in baseline["rows"]}

    # 1. Reads linear?
    over_budget = [
        f"{p} packs: {r['reads']} reads > budget {LINEAR_READ_BUDGET(p)}"
        for p, r in sorted(by_packs.items())
        if r["reads"] > LINEAR_READ_BUDGET(p)
    ]
    if over_budget:
        reasons.append("Reads are NOT linear in pack count:")
        reasons.extend(f"  {line}" for line in over_budget)
        return "ABANDON", reasons
    reasons.append(f"Reads are linear in pack count (budget {LINEAR_READ_BUDGET(10)} at 10 packs).")

    # 2. Wall-clock at the decisive shape, normalised by the 1-pack control.
    ten_now, ten_before = by_packs.get(10), base_by_packs.get(10)
    one_now, one_before = by_packs.get(1), base_by_packs.get(1)
    if not all((ten_now, ten_before, one_now, one_before)):
        reasons.append("Missing a 1-pack or 10-pack row; cannot rule.")
        return "REVISE", reasons

    ratio_now = ten_now["index_s"] / one_now["index_s"]
    ratio_before = ten_before["index_s"] / one_before["index_s"]
    speedup = 1 - (ratio_now / ratio_before)

    reasons.append(
        f"10-pack index rebuild, raw: {ten_before['index_s']:.2f}s -> "
        f"{ten_now['index_s']:.2f}s."
    )
    reasons.append(
        f"Normalised by the 1-pack control: {ratio_before:.1f}x -> "
        f"{ratio_now:.1f}x ({speedup:+.0%})."
    )
    if speedup >= PROCEED_SPEEDUP:
        reasons.append(f"Meets the {PROCEED_SPEEDUP:.0%} threshold fixed before implementation.")
        return "PROCEED", reasons
    if speedup < REVISE_SPEEDUP:
        reasons.append(
            f"Below the {REVISE_SPEEDUP:.0%} floor: the reads were not the cost. "
            "Profile before continuing."
        )
        return "REVISE", reasons
    reasons.append(
        f"Between {REVISE_SPEEDUP:.0%} and {PROCEED_SPEEDUP:.0%}: real but under target."
    )
    return "REVISE", reasons


#: Observed wall-clock variance on UNCHANGED code at the 10-pack shape, which is
#: the number the gate turns on. Every reading taken while building this harness:
#:
#:   80.11s  first run, cold container
#:   66.20, 65.57, 65.55, 66.92, 66.18s   four back-to-back, warm
#:   71.41s  warm, but measured after the 1/2/4/8-pack rows ran first
#:
#: So: ~2% within a tight loop, **~9% across warm full-curve runs**, ~20% cold.
#: The tight-loop figure is the flattering one and is not the one that matters,
#: because the gate compares full-curve runs. 9% against a 50% threshold is
#: still a comfortable margin -- but the honest number is 9, not 2.
#:
#: Read counts, by contrast, were identical on every single run.
MEASURED_VARIANCE_PCT = 9


def run(include_large: bool) -> dict:
    # Discard one small shape before measuring anything.
    #
    # The very first baseline run reported 80.11s at 10 packs; every warm run
    # since has reported 65-67s. A cold reading is ~20% slow, and a baseline
    # recorded cold against an after-run recorded warm would manufacture a ~20%
    # improvement out of nothing -- which is exactly the size of effect this
    # gate is trying to detect. The warm-up costs a few seconds and removes the
    # single most likely way for this harness to lie.
    measure_shape(50, 2)

    # Memory only at the decisive shape: it is the row the gate turns on, and
    # the tracemalloc pass costs ~4x the shape's runtime.
    rows = [
        measure_shape(OBJECTS_PER_PACK, n, with_memory=(n == max(PACK_COUNTS)))
        for n in PACK_COUNTS
    ]
    if include_large:
        rows.append(measure_shape(LARGE_SHAPE[1], LARGE_SHAPE[0]))
    return {"rows": rows, "measured_variance_pct": MEASURED_VARIANCE_PCT}


def render(rows: list[dict]) -> None:
    print(f"{'packs':>6} {'objects':>8} {'reads':>7} {'reads/packs²':>13} "
          f"{'index':>9} {'peakMB':>8}")
    print("-" * 57)
    for r in rows:
        law = r["reads"] / (r["packs"] ** 2) if r["packs"] else 0
        print(
            f"{r['packs']:>6} {r['objects']:>8} {r['reads']:>7} "
            f"{law:>13.2f} {r['index_s']:>8.2f}s "
            f"{('%.1f' % r['peak_mb']) if r.get('peak_mb') else '-':>8}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                        help="record the pre-change state and verify the packs^2 law")
    parser.add_argument("--large", action="store_true",
                        help="also run the 10 packs x 1,000 objects shape (slow)")
    args = parser.parse_args()

    result = run(include_large=args.large)
    render(result["rows"])

    if args.baseline:
        verify_baseline_law([r for r in result["rows"] if r["objects_per_pack"] == OBJECTS_PER_PACK])
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nBaseline written to {BASELINE_PATH.relative_to(REPO_ROOT)}")
        print("The packs^2 law was reproduced, so the harness is measuring what it claims to.")
        return 0

    if not BASELINE_PATH.is_file():
        print(f"\nNo baseline at {BASELINE_PATH.relative_to(REPO_ROOT)}. "
              "Run with --baseline first.", file=sys.stderr)
        return 2

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    call, reasons = verdict(baseline, result)
    print(f"\n=== DECISION GATE A: {call} ===")
    for line in reasons:
        print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
