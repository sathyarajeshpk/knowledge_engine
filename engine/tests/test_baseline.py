"""The REV002 grandfather baseline: what it accepts, and what it must never hide.

The nine validation rules of M9 Gate D step 4. V1-V5 together are the proof that
a baseline cannot suppress future history; V6-V9 keep the baseline itself
honest.

Nothing here asserts on REV002 as its own oracle. Where an independent signal is
needed it comes from `ke.audit`, which reads `run_id` and never `changed_fields`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ke.baseline import BASELINE_PATH, Baseline, BaselineKey
from ke.models import Revision
from ke.pack import Pack
from ke.validate import Level, has_errors, validate_repo

from tests.conftest import make_object, write_object

FLIP = ("date_confidence", "date_precision", "published_date")

#: Placeholder for the fixture object's own Feature ID.
SELF = "<self>"


def _revisions(spec):
    """`[(revision_number, changed_fields), ...]` -> Revision objects."""
    return tuple(
        Revision(
            revision=number,
            date=date(2026, 8, 1),
            changed_fields=fields,
            content_hash=f"sha256:{number:064d}",
            run_id=f"run-{number}",
        )
        for number, fields in spec
    )


@pytest.fixture
def repo_with(tmp_path):
    """A one-pack repository, an object with the given revisions, and a baseline."""

    def build(revision_spec, baseline_entries):
        root = tmp_path / "domain-packs" / "p"
        (root / "state").mkdir(parents=True)
        (root / "pack.yml").write_text(
            "name: p\nid_prefix: PP\nschema_version: 1\n"
            "limits:\n  max_summary_words: 120\nsources: []\n",
            encoding="utf-8",
        )
        (root / "state" / "id-registry.json").write_text('{"prefix": "PP"}\n')

        obj = make_object(revisions=_revisions(revision_spec))
        write_object(root, obj)

        if baseline_entries is not None:
            # `SELF` stands for "this object", resolved here rather than
            # hard-coded in each test: guessing the fixture's Feature ID made
            # every entry stale and the tests pass for the wrong reason.
            baseline_entries = [
                {**e, "feature_id": str(obj.id) if e["feature_id"] == SELF else e["feature_id"]}
                for e in baseline_entries
            ]
            path = tmp_path / BASELINE_PATH
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"_comment": "test", "findings": baseline_entries}, indent=2),
                encoding="utf-8",
            )
        return tmp_path, obj

    return build


def _rev002(repo):
    return [f for f in validate_repo(repo) if f.code == "REV002"]


def _entry(feature_id, first, last, fields):
    return {
        "feature_id": feature_id,
        "first_revision": first,
        "last_revision": last,
        "changed_fields": list(fields),
    }


HISTORICAL = [(1, ())] + [(n, FLIP) for n in range(2, 12)]


# ---------------------------------------------------------------------------
# V1-V5 — the non-suppression proof
# ---------------------------------------------------------------------------


def test_v1_an_exact_match_is_downgraded_to_info(repo_with):
    """All four key components match, so the finding is accepted as history."""
    repo, _ = repo_with(HISTORICAL, [_entry(SELF, 2, 11, FLIP)])

    findings = _rev002(repo)

    assert len(findings) == 1
    assert findings[0].level is Level.INFO


def test_v2_exactly_the_baselined_findings_are_downgraded(repo_with):
    """Set equality: everything baselined is INFO, everything else is not."""
    spec = HISTORICAL + [(12, ("title",))] + [(n, ("summary",)) for n in range(13, 16)]
    repo, _ = repo_with(spec, [_entry(SELF, 2, 11, FLIP)])

    findings = _rev002(repo)
    info = {f.level is Level.INFO for f in findings}

    assert len(findings) == 2
    assert info == {True, False}, "expected exactly one downgraded and one not"


def test_v3_a_new_run_on_a_baselined_object_is_not_suppressed(repo_with):
    """**The core proof.** A later run has a different range, so it matches nothing.

    Revisions are append-only, so a range that has been used can never be
    re-issued. The baselined range ends at 11; this new run starts at 13.
    """
    spec = HISTORICAL + [(12, ("title",))] + [(n, ("summary",)) for n in range(13, 16)]
    repo, _ = repo_with(spec, [_entry(SELF, 2, 11, FLIP)])

    findings = _rev002(repo)
    fresh = [f for f in findings if "13-15" in f.message]

    assert fresh, "the new run produced no finding at all"
    assert fresh[0].level is Level.WARNING, "a new run was suppressed by the baseline"


def test_item7_same_object_same_fields_different_range_stays_a_warning(repo_with):
    """Stated explicitly because the revision range is what prevents suppression.

    Identical `feature_id`, identical `changed_fields` — only the range differs.
    That must not match.
    """
    spec = HISTORICAL + [(12, ("title",))] + [(n, FLIP) for n in range(13, 16)]
    repo, _ = repo_with(spec, [_entry(SELF, 2, 11, FLIP)])

    findings = _rev002(repo)
    later = [f for f in findings if "13-15" in f.message]

    assert later, "the later run produced no finding"
    assert later[0].level is Level.WARNING


def test_v4_same_range_different_fields_is_not_a_match(repo_with):
    """Guards the key against degenerating to (feature_id, range)."""
    repo, _ = repo_with(
        HISTORICAL, [_entry(SELF, 2, 11, ("summary", "title"))]
    )

    findings = _rev002(repo)

    assert findings[0].level is Level.WARNING


def test_v5_a_finding_on_another_object_is_not_a_match(repo_with):
    """Guards the key against degenerating to something object-independent."""
    repo, _ = repo_with(HISTORICAL, [_entry("PP-2026-99-999", 2, 11, FLIP)])

    findings = _rev002(repo)

    assert findings[0].level is Level.WARNING


def test_a_partial_match_is_not_a_match(repo_with):
    """No fuzzy matching: one component differing is enough to miss."""
    repo, _ = repo_with(HISTORICAL, [_entry(SELF, 2, 10, FLIP)])

    findings = _rev002(repo)

    assert findings[0].level is Level.WARNING


# ---------------------------------------------------------------------------
# V6-V9 — keeping the baseline honest
# ---------------------------------------------------------------------------


def test_v6_a_stale_baseline_entry_is_reported(repo_with):
    """An entry matching nothing is surfaced, never silently ignored.

    It means the object was removed, its history was rewritten (both forbidden),
    or the entry was wrong. Dropping it quietly would let the baseline decay
    into matchers nobody can account for.
    """
    repo, _ = repo_with(HISTORICAL, [
        _entry(SELF, 2, 11, FLIP),
        _entry("PP-2026-99-999", 2, 11, FLIP),          # matches nothing
    ])

    stale = [f for f in validate_repo(repo) if f.code == "REV003"]

    assert len(stale) == 1
    assert "PP-2026-99-999" in stale[0].message
    assert stale[0].level is Level.WARNING


def test_v6_a_stale_entry_does_not_suppress_anything(repo_with):
    """A stale entry is inert as a matcher, not merely reported."""
    repo, _ = repo_with(HISTORICAL, [_entry("PP-2026-99-999", 2, 11, FLIP)])

    findings = _rev002(repo)

    assert findings[0].level is Level.WARNING


def test_v7_the_audit_oracle_does_not_consult_the_baseline():
    """The oracle must stay independent of anything the baseline decides.

    A post-M9-3 duplicate write stays visible through the oracle even if the
    affected object carries a grandfathered REV002 finding.
    """
    import ast
    import inspect

    from ke import audit

    source = inspect.getsource(audit)
    tree = ast.parse(source)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.Import) for alias in node.names
    }

    assert not any(name and "baseline" in name for name in imported)


def test_v8_nothing_in_the_engine_writes_the_baseline():
    """Immutable historical state: the engine reads it and never writes it.

    Generation lives in `tools/`, is run by hand, and lands as a reviewed diff.
    A baseline that maintains itself accepts whatever arrives.
    """
    engine = Path(__file__).resolve().parents[1] / "ke"
    writers = []
    for module in engine.rglob("*.py"):
        text = module.read_text(encoding="utf-8")
        if "BASELINE_PATH" not in text and "rev002-baseline" not in text:
            continue
        for line in text.splitlines():
            if ("write_text" in line or "json.dump(" in line) and "baseline" in line.lower():
                writers.append(f"{module.name}: {line.strip()}")

    assert not writers, f"engine code writes the baseline: {writers}"


def test_v9_the_committed_baseline_is_canonical_and_sorted():
    """Byte-stable regeneration depends on both (ADR-0022)."""
    from ke.pack import find_repo_root
    from ke.validate import canonical_fields

    path = find_repo_root(Path(__file__)) / BASELINE_PATH
    if not path.is_file():
        pytest.skip("no baseline committed yet")

    entries = json.loads(path.read_text(encoding="utf-8"))["findings"]
    keys = [(e["feature_id"], e["first_revision"]) for e in entries]

    assert keys == sorted(keys), "entries are not sorted"
    for entry in entries:
        fields = tuple(entry["changed_fields"])
        assert fields == canonical_fields(fields), f"non-canonical: {entry['feature_id']}"


# ---------------------------------------------------------------------------
# The INFO tier
# ---------------------------------------------------------------------------


def test_info_findings_are_still_reported(repo_with):
    """Downgraded, not removed. The finding stays visible in the output."""
    repo, _ = repo_with(HISTORICAL, [_entry(SELF, 2, 11, FLIP)])

    findings = _rev002(repo)

    assert len(findings) == 1, "the finding was removed rather than downgraded"
    assert "revisions 2-11" in findings[0].message


def test_info_does_not_make_has_errors_true():
    """Including under --strict, which is the entire point of the tier."""
    from ke.validate import Finding

    info = [Finding(Level.INFO, "REV002", "x", "accepted")]

    assert not has_errors(info)
    assert not has_errors(info, strict=True)


def test_warning_and_error_behaviour_is_unchanged():
    """Adding a tier must not move the existing lines."""
    from ke.validate import Finding

    warning = [Finding(Level.WARNING, "REV002", "x", "w")]
    error = [Finding(Level.ERROR, "REF001", "x", "e")]

    assert not has_errors(warning)
    assert has_errors(warning, strict=True)
    assert has_errors(error)
    assert has_errors(error, strict=True)


def test_info_alongside_a_warning_still_fails_strict():
    """The baseline must not make an unrelated warning pass."""
    from ke.validate import Finding

    mixed = [
        Finding(Level.INFO, "REV002", "x", "accepted"),
        Finding(Level.WARNING, "REV002", "y", "new"),
    ]

    assert has_errors(mixed, strict=True)


def test_a_missing_baseline_grandfathers_nothing(repo_with):
    """The default state, and the one the repository is in until one lands."""
    repo, _ = repo_with(HISTORICAL, None)

    findings = _rev002(repo)

    assert findings[0].level is Level.WARNING


def test_a_corrupt_baseline_is_not_silently_treated_as_empty(repo_with, tmp_path):
    """"Nothing grandfathered" and "35 findings we cannot read" must differ.

    Contrast `cross-pack.json`, where degrading to empty costs a repeated review
    item. Here it would silently un-accept history and turn CI red for reasons
    nobody could see in the file.
    """
    repo, _ = repo_with(HISTORICAL, [_entry(SELF, 2, 11, FLIP)])
    (repo / BASELINE_PATH).write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        Baseline.load(repo)
