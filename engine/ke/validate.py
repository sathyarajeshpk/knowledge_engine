"""Structural validation for Domain Packs.

`ke validate` is the guardrail that runs in CI on every push. It exists because
the rules in CLAUDE.md -- never delete knowledge, no duplicate Feature IDs,
preserve chronological order -- are only real if something checks them.

Every check reports a `Finding` with a stable code, so CI output is greppable
and tests can assert on the specific failure rather than on message wording.

Scope note: graph checks (referential integrity of `prerequisites` /
`builds_on`, cycle detection, `replaced_by` inverse consistency) belong to M4,
when relationships are actually populated. M0 validates the *schema* of those
fields only.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

from ke import SCHEMA_VERSION
from ke.models import (
    ALL_METADATA_FIELDS,
    ENGINE_PROPOSED_FIELDS,
    GenerationStatus,
    KnowledgeObject,
)
from ke.baseline import BASELINE_PATH
from ke.pack import OBJECT_SUBDIRS, PACKS_DIRNAME, REQUIRED_PACK_KEYS, Pack, PackError

#: Schema versions this build of the engine understands.
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

#: First `# ` heading in feature.md.
H1_PATTERN = re.compile(r"^#\s+(?P<title>.+?)\s*$", re.MULTILINE)


class Level(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    #: Reported, but never a build failure -- not even under `--strict`.
    #: Currently used only for findings a committed baseline has accepted as
    #: history (`ke.baseline`). A downgraded finding stays visible; hiding it
    #: would be the silent suppression this milestone removed.
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """One validation result.

    `location` is repository-relative so CI output can be pasted into an editor.
    """

    level: Level
    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():7} {self.code}  {self.location}: {self.message}"


@dataclass(frozen=True)
class LoadedObject:
    """A knowledge object that parsed cleanly, with its directory."""

    directory: Path
    obj: KnowledgeObject


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def validate_pack(pack: Pack, baseline=None) -> list[Finding]:
    """Validate one Domain Pack end to end."""
    findings: list[Finding] = []
    findings.extend(_check_pack_config(pack))

    loaded: list[LoadedObject] = []
    for object_dir in pack.iter_object_dirs():
        object_findings, parsed = _check_object(pack, object_dir, baseline)
        findings.extend(object_findings)
        if parsed is not None:
            loaded.append(parsed)

    findings.extend(_check_duplicate_ids(pack, loaded))
    findings.extend(_check_registry(pack, loaded))
    return findings


def validate_repo(repo_root: Path, pack_name: str | None = None) -> list[Finding]:
    """Validate every pack in the repository, or just the one named.

    A pack that cannot be loaded at all becomes a `PACK005` finding and the
    remaining packs are still validated. One malformed `pack.yml` must not
    suppress every other pack's results -- the same "fail fast within a unit,
    continue across units" rule already applied to knowledge objects.
    """
    roots = Pack.find_roots(repo_root)
    if pack_name:
        roots = [root for root in roots if _matches_pack_name(root, pack_name)]
        if not roots:
            return [
                Finding(
                    Level.ERROR,
                    "PACK000",
                    PACKS_DIRNAME,
                    f"no pack named {pack_name!r}",
                )
            ]

    from ke.baseline import Baseline

    baseline = Baseline.load(repo_root)

    findings: list[Finding] = []

    # Runs whatever `--pack` says. A symlink out of `domain-packs/` is a fact
    # about the repository, not about one pack, and a security check that a flag
    # can switch off is a security check that will be switched off. It also has
    # to run here rather than inside `validate_pack`, because a symlinked pack
    # *root* is skipped by `find_roots` and would otherwise be reported by
    # nothing at all.
    findings.extend(_check_escaping_links(repo_root))

    loaded: list[Pack] = []
    for root in roots:
        try:
            pack = Pack.load(root)
        except PackError as exc:
            # Parser errors are multi-line; collapse them so each finding stays
            # one greppable line.
            detail = " ".join(str(exc).split())
            findings.append(
                Finding(
                    Level.ERROR,
                    "PACK005",
                    f"{root.parent.name}/{root.name}/pack.yml",
                    f"pack could not be loaded, so it was skipped: {detail}",
                )
            )
            continue
        findings.extend(validate_pack(pack, baseline))
        loaded.append(pack)

    # Whole-repository checks. These cannot be done per pack by definition: a
    # reference from one pack into another resolves only when both are in view,
    # and a duplicate spanning two packs belongs to neither.
    #
    # Only run when the whole repository was validated. Under `--pack` the
    # other packs were deliberately not loaded, and reporting every cross-pack
    # reference as dangling because the target was filtered out would be a
    # false alarm caused by the flag rather than by the data.
    if pack_name is None and len(loaded) > 1:
        findings.extend(_validate_across_packs(loaded))

    # Stale baseline entries -- only meaningful when the whole repository was
    # validated. Under `--pack` the other packs were deliberately not walked, so
    # their entries would look stale because of the flag rather than the data.
    if pack_name is None:
        findings.extend(
            Finding(
                Level.WARNING,
                "REV003",
                str(BASELINE_PATH),
                f"baseline entry matches no current finding: {key.describe()}. "
                "Either the object was removed, its history was rewritten (both "
                "forbidden), or the entry was wrong. Entries are never dropped "
                "automatically -- remove it in a reviewed change if it is right "
                "to.",
            )
            for key in baseline.stale()
        )
    return findings


def _check_escaping_links(repo_root: Path) -> list[Finding]:
    """SEC001 -- a symlink under `domain-packs/` that points outside it.

    ADR-0016 makes a pack pure data so that adding one needs no engine change
    and therefore no engine review. A symlink is the one thing a data-only
    change can contain that reaches back out at engine-owned files: the weekly
    workflow writes state, knowledge and indexes under the pack root while
    holding a repository write token, so a redirected component sends those
    writes wherever the link points.

    The boundary is `domain-packs/`, not the repository root, and the difference
    is the whole point. `domain-packs/x/state -> ../../.git` never leaves the
    repository and is exactly the attack: engine-owned paths are *inside* the
    repository, so a check that only caught escapes from it would catch nothing
    that matters.

    ERROR, not warning. Every other cross-pack finding in this module is a
    warning because the engine is not entitled to judge the knowledge; this one
    judges the *filesystem*, where a pack has no legitimate reason to reach
    outside its own tree and no ambiguity about what it means when it does.
    """
    from ke.paths import escaping_links, resolved

    packs_dir = resolved(Path(repo_root) / PACKS_DIRNAME)
    if not packs_dir.is_dir():
        return []
    return [
        Finding(
            Level.ERROR,
            "SEC001",
            f"{PACKS_DIRNAME}/{link.relative_to(packs_dir)}",
            f"symlink points outside {PACKS_DIRNAME}/, to {target}. Packs are "
            "data and are reviewed as data; a link out of one redirects "
            "automated writes to a path nobody reviewed.",
        )
        for link, target in escaping_links(packs_dir)
    ]


def _validate_across_packs(packs: list[Pack]) -> list[Finding]:
    """Referential integrity and duplicate reporting across the whole repository.

    Cross-pack duplicates are a **warning**, never an error. Two packs holding
    the same canonical URL is often correct -- the same announcement filed under
    two taxonomies, useful to two different questions -- and the engine has no
    way to tell that from a true duplicate. Failing CI over it would make a
    judgement the engine is not entitled to make (ADR-0044).
    """
    from ke.crosspack import dangling_references, find_duplicates

    findings: list[Finding] = []

    for feature_id, field, missing in dangling_references(packs):
        findings.append(
            Finding(
                Level.ERROR,
                "REF001",
                PACKS_DIRNAME,
                f"{feature_id}: {field} references {missing}, which exists in no pack",
            )
        )

    for pair in find_duplicates(packs):
        where = " and ".join(
            f"{side.pack_name}:{side.feature_id}" for side in pair.sides
        )
        findings.append(
            Finding(
                Level.WARNING,
                "XPK001",
                PACKS_DIRNAME,
                f"the same {pair.basis} is held by {where}; both are kept — "
                "review with `ke review --kind cross-pack`",
            )
        )
    return findings


def _matches_pack_name(root: Path, pack_name: str) -> bool:
    """Match `--pack` against the directory name, falling back to `pack.yml`.

    The directory name is checked first so that a pack whose `pack.yml` is
    unparseable can still be selected -- and therefore still reported.
    """
    if root.name == pack_name:
        return True
    try:
        return Pack.load(root).name == pack_name
    except PackError:
        return False


def scan_summary(repo_root: Path) -> tuple[int, int]:
    """`(pack count, knowledge object count)` for the run summary line.

    Tolerates unloadable packs: they are counted but contribute no objects.
    """
    roots = Pack.find_roots(repo_root)
    objects = 0
    for root in roots:
        try:
            objects += sum(1 for _ in Pack.load(root).iter_object_dirs())
        except PackError:
            continue
    return len(roots), objects


def has_errors(findings: Iterable[Finding], strict: bool = False) -> bool:
    """Whether these findings should fail the build.

    Under `--strict` a warning is also a failure, which is what CI uses once a
    pack is clean enough to hold that line.
    """
    findings = list(findings)
    if strict:
        # `bool(findings)` would fail on INFO too, which would make the
        # baseline downgrade achieve nothing -- the whole point of INFO is that
        # it is reported without failing the build.
        return any(finding.level is not Level.INFO for finding in findings)
    return any(finding.level is Level.ERROR for finding in findings)


# ---------------------------------------------------------------------------
# Pack-level checks
# ---------------------------------------------------------------------------


def _check_pack_config(pack: Pack) -> list[Finding]:
    findings: list[Finding] = []
    location = pack.location(pack.root / "pack.yml")

    for key in REQUIRED_PACK_KEYS:
        if not pack.config.get(key):
            findings.append(
                Finding(Level.ERROR, "PACK001", location, f"missing required key {key!r}")
            )

    if pack.schema_version is not None and pack.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        findings.append(
            Finding(
                Level.ERROR,
                "PACK002",
                location,
                f"schema_version {pack.schema_version} is not supported by this "
                f"engine (supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}); run `ke migrate`",
            )
        )

    prefix = pack.id_prefix
    if prefix and not re.fullmatch(r"[A-Z]{2,4}", prefix):
        findings.append(
            Finding(
                Level.ERROR,
                "PACK003",
                location,
                f"id_prefix {prefix!r} must be 2-4 upper-case letters",
            )
        )

    # Only `state/` is required. It is the one pack directory that always holds
    # committed files, so it is the one Git can actually preserve. `knowledge/`,
    # `indexes/` and `digests/` are created on demand (ADR-0015): requiring a
    # directory Git cannot store would fail on every fresh clone.
    if not pack.state_dir.is_dir():
        findings.append(
            Finding(
                Level.ERROR,
                "PACK004",
                pack.location(pack.state_dir),
                "missing required pack directory (must contain id-registry.json)",
            )
        )

    # SEC002 -- force the source definitions to be built.
    #
    # `Pack.source_definitions` is a lazy property, so the scheme allowlist in
    # `SourceDefinition.from_config` only fires when something asks for the
    # sources. Validation never did, which meant a pack declaring
    # `url: file:///etc/hostname` reported "no findings" in CI and failed at
    # 03:00 on Sunday instead -- inside the process holding the write token,
    # which is the one place it must not first be discovered.
    #
    # Found by running the installed CLI rather than the library: the guard was
    # real, the path to it was not.
    try:
        pack.source_definitions
    except Exception as exc:  # noqa: BLE001 - any malformed source is a finding
        findings.append(
            Finding(
                Level.ERROR,
                "SEC002",
                location,
                " ".join(str(exc).split()) or f"{type(exc).__name__} while reading sources",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Object-level checks
# ---------------------------------------------------------------------------


def _check_object(pack: Pack, object_dir: Path, baseline=None) -> tuple[list[Finding], LoadedObject | None]:
    """Validate one knowledge object directory.

    Returns the findings plus the parsed object, or `None` if it could not be
    parsed -- an unparseable object cannot take part in the duplicate-ID or
    registry checks.
    """
    findings: list[Finding] = []
    location = pack.location(object_dir)

    metadata_path = object_dir / "metadata.yaml"
    feature_path = object_dir / "feature.md"

    if not feature_path.is_file():
        findings.append(Finding(Level.ERROR, "OBJ003", location, "missing feature.md"))

    # Note: `artifacts/`, `images/` and `references/` are created on demand, not
    # up front (ADR-0015), so their absence is normal and is not checked. What
    # is checked is that every artifact the metadata *claims* exists really
    # does -- see `_check_generation`.

    if not metadata_path.is_file():
        findings.append(Finding(Level.ERROR, "OBJ002", location, "missing metadata.yaml"))
        return findings, None

    try:
        metadata = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        findings.append(
            Finding(Level.ERROR, "OBJ004", location, f"metadata.yaml is not valid YAML: {exc}")
        )
        return findings, None

    if not isinstance(metadata, dict):
        findings.append(
            Finding(Level.ERROR, "OBJ004", location, "metadata.yaml must contain a mapping")
        )
        return findings, None

    schema_findings = _check_metadata_shape(location, metadata)
    findings.extend(schema_findings)
    if any(f.level is Level.ERROR for f in schema_findings):
        return findings, None

    try:
        obj = KnowledgeObject.from_metadata_dict(metadata)
    except (ValueError, KeyError, TypeError) as exc:
        findings.append(
            Finding(Level.ERROR, "SCHEMA003", location, f"invalid metadata value: {exc}")
        )
        return findings, None

    findings.extend(_check_identity(pack, object_dir, obj))
    findings.extend(_check_ownership(location, obj))
    findings.extend(_check_category(pack, location, obj))
    findings.extend(_check_generation(location, object_dir, obj))
    if feature_path.is_file():
        findings.extend(_check_feature_document(pack, feature_path, obj))
        findings.extend(_check_revision_history(pack, feature_path, obj, baseline))

    return findings, LoadedObject(directory=object_dir, obj=obj)


def _check_metadata_shape(location: str, metadata: dict[str, Any]) -> list[Finding]:
    """Check keys and schema version before attempting to build the model.

    The engine always writes the full field set, so a missing key means the file
    was truncated or hand-edited incorrectly, and an unknown key usually means a
    typo that would otherwise be silently ignored.
    """
    findings: list[Finding] = []

    version = metadata.get("schema_version")
    if version is None:
        findings.append(
            Finding(Level.ERROR, "SCHEMA002", location, "missing required field 'schema_version'")
        )
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        findings.append(
            Finding(
                Level.ERROR,
                "SCHEMA001",
                location,
                f"schema_version {version!r} is not supported by this engine "
                f"(supports {sorted(SUPPORTED_SCHEMA_VERSIONS)}); run `ke migrate`",
            )
        )

    for name in sorted(ALL_METADATA_FIELDS - set(metadata)):
        findings.append(
            Finding(Level.ERROR, "SCHEMA002", location, f"missing required field {name!r}")
        )

    for name in sorted(set(metadata) - ALL_METADATA_FIELDS):
        findings.append(
            Finding(
                Level.WARNING,
                "SCHEMA004",
                location,
                f"unknown field {name!r} will be dropped on the next write",
            )
        )
    return findings


def _check_identity(pack: Pack, object_dir: Path, obj: KnowledgeObject) -> list[Finding]:
    """The ID must agree with the pack, the directory name and the path."""
    findings: list[Finding] = []
    location = pack.location(object_dir)

    if pack.id_prefix and obj.id.prefix != pack.id_prefix:
        findings.append(
            Finding(
                Level.ERROR,
                "ID002",
                location,
                f"Feature ID prefix {obj.id.prefix!r} does not match pack prefix "
                f"{pack.id_prefix!r}",
            )
        )

    if object_dir.name != obj.directory_name:
        findings.append(
            Finding(
                Level.ERROR,
                "OBJ001",
                location,
                f"directory name should be {obj.directory_name!r} "
                f"(from id + slug), found {object_dir.name!r}",
            )
        )

    expected_parent = obj.id.knowledge_subpath  # e.g. "2026/04"
    actual_parent = f"{object_dir.parent.parent.name}/{object_dir.parent.name}"
    if actual_parent != expected_parent:
        findings.append(
            Finding(
                Level.ERROR,
                "ID004",
                location,
                f"object should live under knowledge/{expected_parent} "
                f"to match its Feature ID, found knowledge/{actual_parent}",
            )
        )
    return findings


def _check_ownership(location: str, obj: KnowledgeObject) -> list[Finding]:
    """`overrides` may only lock engine-proposed fields.

    Locking an engine-owned field is meaningless (the engine owns it
    regardless); locking a user-owned field is redundant (the engine never
    writes it). Either indicates a misunderstanding worth surfacing.
    """
    findings: list[Finding] = []
    for name in obj.overrides:
        if name not in ALL_METADATA_FIELDS:
            findings.append(
                Finding(
                    Level.ERROR,
                    "OWN002",
                    location,
                    f"overrides names unknown field {name!r}",
                )
            )
        elif name not in ENGINE_PROPOSED_FIELDS:
            findings.append(
                Finding(
                    Level.ERROR,
                    "OWN001",
                    location,
                    f"overrides may only lock engine-proposed fields; {name!r} is not one "
                    f"(lockable: {sorted(ENGINE_PROPOSED_FIELDS)})",
                )
            )
    return findings


def _check_generation(
    location: str, object_dir: Path, obj: KnowledgeObject
) -> list[Finding]:
    """Every artifact the metadata claims exists must actually exist.

    This replaces the old "missing standard subdirectory" warning. Checking for
    empty scaffolding told us nothing -- Git cannot store an empty directory, so
    it was guaranteed to fire on every object after a fresh clone (ADR-0015).
    Checking that a `generated` artifact's file is really there is the integrity
    property that was actually wanted.
    """
    findings: list[Finding] = []
    present_statuses = (GenerationStatus.GENERATED, GenerationStatus.STALE)

    for artifact_type, entry in sorted(obj.generation.items()):
        if entry.status not in present_statuses:
            # `none`, `requested` and `rejected` have no file by definition.
            continue

        if not entry.path:
            findings.append(
                Finding(
                    Level.ERROR,
                    "GEN001",
                    location,
                    f"{artifact_type} is marked {entry.status} but records no path",
                )
            )
            continue

        top_level = PurePosixPath(entry.path).parts[0] if entry.path else ""
        if top_level not in OBJECT_SUBDIRS:
            findings.append(
                Finding(
                    Level.ERROR,
                    "GEN002",
                    location,
                    f"{artifact_type} path {entry.path!r} must live under one of "
                    f"{list(OBJECT_SUBDIRS)}",
                )
            )
            continue

        if not (object_dir / entry.path).is_file():
            findings.append(
                Finding(
                    Level.ERROR,
                    "GEN003",
                    location,
                    f"{artifact_type} is marked {entry.status} but {entry.path} is missing; "
                    "artifacts are marked stale, never deleted",
                )
            )
    return findings


def _check_category(pack: Pack, location: str, obj: KnowledgeObject) -> list[Finding]:
    if obj.category and pack.categories and obj.category not in pack.categories:
        return [
            Finding(
                Level.WARNING,
                "SCHEMA005",
                location,
                f"category {obj.category!r} is not declared in pack.yml categories",
            )
        ]
    return []


def _check_revision_history(pack: Pack, path: Path, obj: KnowledgeObject, baseline=None) -> list[Finding]:
    """Check an object's history for the ways it can be wrong.

    A history that cannot be believed is worse than no history, because nothing
    about it looks broken. `ke history` reads these snapshots to reconstruct the
    past; if the chain is misnumbered, undated or self-contradicting, it will
    answer confidently and wrongly.

    Also catches **repeated identical revisions**, which is not a schema error
    but a real symptom: it means something rewrote the object with the same
    change over and over. That is exactly what the M3 flip-flop bug produced —
    35 objects with 11 revisions each, all recording the same field change —
    and it went unnoticed because every individual revision was well-formed.
    """
    from ke.history import verify_chain

    location = pack.location(path)
    findings = [
        Finding(Level.ERROR, "REV001", location, problem)
        for problem in verify_chain(obj)
    ]

    for fields, length, start in identical_runs(
        [canonical_fields(rev.changed_fields) for rev in obj.revisions[1:]]
    ):
        # Exact match only -- all four key components. A partial match is not a
        # match, and there is no fuzzy matching anywhere in this path.
        grandfathered = False
        if baseline is not None:
            from ke.baseline import BaselineKey

            grandfathered = baseline.match(
                BaselineKey(
                    feature_id=str(obj.id),
                    first_revision=start,
                    last_revision=start + length - 1,
                    changed_fields=fields,
                )
            )
        findings.append(
            Finding(
                Level.INFO if grandfathered else Level.WARNING,
                "REV002",
                location,
                f"revisions {start}-{start + length - 1} all record the same change "
                f"({', '.join(fields)}); this is the signature of a value "
                "flip-flopping between runs rather than genuine edits",
            )
        )
    return findings


#: A run this long or longer is the flip-flop signature. Three is the shortest
#: sequence that cannot be a coincidence of two ordinary edits touching the same
#: field, and it is the threshold the check has used since M5.
FLIP_FLOP_RUN = 3


def canonical_fields(fields) -> tuple[str, ...]:
    """A change-set in canonical form: sorted, so equality is order-independent.

    Run detection compares change-sets for equality, so two revisions recording
    the same fields in a different order would break a run and hide a flip-flop.

    Nothing on disk is currently out of order (measured: 0 of 524 revisions),
    because `revisions.py` sorts before writing. But `history.py` writes
    `("status", "replaced_by")` literally on the supersede path, which is not
    sorted, so the guarantee holds by habit rather than by contract.
    Canonicalising here makes it hold by contract, at no cost — verified
    set-neutral against the live repository.

    It is also the form the M9 Gate D baseline keys on, so the detector and the
    baseline cannot disagree about what a change-set *is*.
    """
    return tuple(sorted(fields))


def identical_runs(changes: list[tuple[str, ...]]) -> list[tuple[tuple[str, ...], int, int]]:
    """**Every** run of consecutive identical change-sets that is long enough.

    Returns `(fields, length, first_revision_number)` per run, in chain order.

    ## Why a run rather than whole-chain uniformity

    This check used to require the **entire** post-initial chain to be uniform::

        len(changes) >= 3 and len(set(changes)) == 1

    which meant a single genuine edit *anywhere afterwards* erased the finding
    permanently. Measured on the live repository: 35 objects carried the
    signature, one ordinary weekly harvest appended one real edit to 29 of them,
    and the reported count silently fell to 6 with **nothing fixed** (M9 Gate B).

    That is the wrong shape for a warning. A flip-flop that happened is a fact
    about the object's history; later legitimate activity does not undo it. So
    the check now looks for the signature *anywhere in the chain* and is
    append-insensitive: adding revisions to the end can lengthen a run, never
    shorten one.

    ## Why every run, and not just the longest

    Reporting only the longest run made a grandfather baseline impossible to
    build safely. Baselining a finding suppresses it; if the detector reports
    only the longest run, a **new, shorter** flip-flop on an already-baselined
    object produces no new finding at all — the detector keeps reporting the
    old, longer run, the baseline keeps matching it, and the new damage is
    invisible forever.

    Demonstrated in M9 Gate D against a real object: appending a fresh
    three-revision run to `MSF-2026-05-002` left the reported finding completely
    unchanged at "10 long, starting rev 2".

    The invariant this establishes:

        A historical REV002 finding must not become invisible merely because a
        future qualifying run on the same object is shorter than the historical
        run.

    Every run becomes its own finding with its own revision range, which is what
    lets a baseline pin history without blinding the future.

    Measured set-neutral when introduced: no object in the repository has more
    than one qualifying run, so this produced exactly the same 35 findings as
    longest-run reporting.

    ## What this deliberately does not do

    It compares `changed_fields` — field *names*, not values — so it detects
    "the same fields changed repeatedly", which is a proxy for "a value
    oscillated". A stronger check would need the values, and revisions do not
    store them for the fields that flip-flop. The proxy is honest about being
    one, and `ke.audit` carries the independent mechanism-level signal used to
    validate it.
    """
    runs: list[tuple[tuple[str, ...], int, int]] = []
    if len(changes) < FLIP_FLOP_RUN:
        return runs

    current, start = 1, 0
    for index in range(1, len(changes)):
        if changes[index] == changes[index - 1]:
            current += 1
            continue
        if current >= FLIP_FLOP_RUN:
            # `changes` excludes revision 1, so index 0 is revision 2.
            runs.append((changes[start], current, start + 2))
        current, start = 1, index
    if current >= FLIP_FLOP_RUN:
        runs.append((changes[start], current, start + 2))
    return runs


def _check_feature_document(pack: Pack, feature_path: Path, obj: KnowledgeObject) -> list[Finding]:
    """Check feature.md against its metadata.

    Splitting knowledge from metadata buys readability at the cost of drift, so
    the drift is checked here rather than hoped away.
    """
    findings: list[Finding] = []
    location = pack.location(feature_path)
    text = feature_path.read_text(encoding="utf-8")

    match = H1_PATTERN.search(text)
    if match is None:
        findings.append(
            Finding(Level.ERROR, "CONS001", location, "feature.md has no '# ' heading")
        )
    elif match["title"].strip() != obj.title.strip():
        findings.append(
            Finding(
                Level.ERROR,
                "CONS002",
                location,
                f"heading {match['title']!r} does not match metadata title {obj.title!r}",
            )
        )

    # Copyright guard: we store a short original summary and a link, never the
    # full text of a third-party article. Counted over everything below the
    # heading, which slightly over-counts (it includes the source line) and so
    # errs toward being cautious.
    body = H1_PATTERN.sub("", text, count=1)
    word_count = len(body.split())
    if word_count > pack.max_summary_words:
        findings.append(
            Finding(
                Level.ERROR,
                "COPY001",
                location,
                f"summary is {word_count} words, over the {pack.max_summary_words}-word "
                "limit; store a short original summary and a link, not the full article",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Cross-object checks
# ---------------------------------------------------------------------------


def _check_duplicate_ids(pack: Pack, loaded: list[LoadedObject]) -> list[Finding]:
    """No duplicate Feature IDs -- a core CLAUDE.md rule."""
    findings: list[Finding] = []
    seen: dict[str, Path] = {}
    for entry in loaded:
        key = str(entry.obj.id)
        if key in seen:
            findings.append(
                Finding(
                    Level.ERROR,
                    "ID003",
                    pack.location(entry.directory),
                    f"duplicate Feature ID {key} (also at {pack.location(seen[key])})",
                )
            )
        else:
            seen[key] = entry.directory
    return findings


def _check_registry(pack: Pack, loaded: list[LoadedObject]) -> list[Finding]:
    """The ID registry must agree with what is actually on disk.

    Two properties matter. Counters must never sit below a sequence already in
    use, or the next mint would collide. And every object must be registered, or
    a future mint could reuse its number.
    """
    findings: list[Finding] = []
    location = pack.location(pack.registry_path)

    if not pack.registry_path.is_file():
        return [Finding(Level.ERROR, "REG001", location, "missing state/id-registry.json")]

    try:
        registry = json.loads(pack.registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [Finding(Level.ERROR, "REG001", location, f"not valid JSON: {exc}")]

    if not isinstance(registry, dict):
        return [Finding(Level.ERROR, "REG001", location, "must contain a JSON object")]

    counters: dict[str, Any] = registry.get("counters") or {}
    paths: dict[str, Any] = registry.get("paths") or {}

    highest: dict[str, int] = {}
    for entry in loaded:
        key = entry.obj.id.month_key
        highest[key] = max(highest.get(key, 0), entry.obj.id.sequence)

    for month_key, used in sorted(highest.items()):
        recorded = counters.get(month_key)
        if recorded is None:
            findings.append(
                Finding(
                    Level.ERROR,
                    "REG002",
                    location,
                    f"no counter for {month_key} but sequence {used:03d} is already in use",
                )
            )
        elif int(recorded) < used:
            findings.append(
                Finding(
                    Level.ERROR,
                    "REG002",
                    location,
                    f"counter for {month_key} is {recorded}, below the highest sequence "
                    f"in use ({used}); the next mint would collide",
                )
            )

    for entry in loaded:
        key = str(entry.obj.id)
        expected = entry.obj.knowledge_subpath
        if key not in paths:
            findings.append(
                Finding(Level.ERROR, "REG003", location, f"{key} is not registered")
            )
        elif str(paths[key]) != expected:
            findings.append(
                Finding(
                    Level.ERROR,
                    "REG004",
                    location,
                    f"{key} is registered at {paths[key]!r} but lives at {expected!r}",
                )
            )

    known = {str(entry.obj.id) for entry in loaded}
    for key in sorted(set(paths) - known):
        findings.append(
            Finding(
                Level.ERROR,
                "REG005",
                location,
                f"{key} is registered at {paths[key]!r} but no such object exists; "
                "Feature IDs are permanent and objects are never deleted",
            )
        )
    return findings
