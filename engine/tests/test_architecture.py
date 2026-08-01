"""Tests that enforce architectural boundaries rather than behaviour.

A boundary nobody checks is a boundary that decays. These tests exist so that
the acquisition subsystem stays reusable by construction — when knowledge starts
arriving from APIs, PDFs or videos, the pipeline should need a new adapter and
nothing else.

They are deliberately crude (import scanning, protocol shape) because a subtle
architecture test is one nobody trusts when it fails.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from ke.acquisition.discover import ADAPTERS

ENGINE = pathlib.Path(__file__).resolve().parents[1] / "ke"
ACQUISITION = ENGINE / "acquisition"

#: Modules that consume acquisition's output. Acquisition must never import
#: them: it produces knowledge items and must not know what becomes of them.
#: Most do not exist yet — that is the point. The rule is cheapest to enforce
#: before the modules that would break it are written.
DOWNSTREAM = {
    "store", "classify", "indexer", "digest", "generate", "retrieve",
    "graph", "revisions", "notify", "migrate", "ids", "dedupe", "validate",
}


def imported_modules(path: pathlib.Path) -> set[str]:
    """Every `ke.*` module a file imports, as dotted names."""
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return {name for name in found if name.startswith("ke")}


def acquisition_files() -> list[pathlib.Path]:
    return [p for p in ACQUISITION.rglob("*.py") if "__pycache__" not in str(p)]


def test_acquisition_never_imports_a_downstream_module():
    """The rule that keeps the subsystem reusable.

    Acquisition fetches, parses, identifies and grades. The moment it imports
    storage or classification, a second knowledge source can no longer reuse it
    without dragging those along.
    """
    offenders = []
    for path in acquisition_files():
        for module in imported_modules(path):
            tail = module.split(".")[-1]
            if tail in DOWNSTREAM:
                offenders.append(f"{path.relative_to(ENGINE)} imports {module}")
    assert not offenders, "acquisition must not depend on what happens next:\n" + "\n".join(
        offenders
    )


def test_acquisition_depends_only_on_core_and_itself():
    """Allowed downward dependencies, stated explicitly."""
    allowed_roots = {"ke.models", "ke.normalize", "ke.clock", "ke.acquisition", "ke"}
    offenders = []
    for path in acquisition_files():
        for module in imported_modules(path):
            if not any(
                module == root or module.startswith(root + ".") for root in allowed_roots
            ):
                offenders.append(f"{path.relative_to(ENGINE)} imports {module}")
    assert not offenders, "unexpected dependency out of acquisition:\n" + "\n".join(offenders)


def test_models_does_not_import_acquisition():
    """Core types must not depend on the subsystem that uses them.

    `ItemIdentity` and `IdentityBasis` live in `models` for exactly this reason:
    they are types, and types belong below the code that computes them.
    """
    assert not [m for m in imported_modules(ENGINE / "models.py") if "acquisition" in m]


CORE_MODULES = ("models.py", "normalize.py", "clock.py")


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_modules_do_not_import_acquisition(module):
    """Core must not depend on the subsystem built on top of it.

    This is the mirror of `test_acquisition_never_imports_a_downstream_module`,
    and it is not theoretical: `normalize` briefly imported `TRACKING_PARAMS`
    from `acquisition.identity`, which created a genuine import cycle — the
    acquisition package's `__init__` pulls in the adapters, which import
    `normalize`, which was still initialising.

    It only surfaced when a new module imported `normalize` first. Tests passed
    because their import order happened to avoid it.
    """
    offenders = [m for m in imported_modules(ENGINE / module) if "acquisition" in m]
    assert not offenders, f"{module} must not import acquisition: {offenders}"


def test_the_package_imports_cleanly_from_a_cold_start():
    """A cycle that only appears for one entry point is still a cycle."""
    import subprocess
    import sys

    for entry in ("ke.normalize", "ke.store", "ke.acquisition", "ke.models"):
        result = subprocess.run(
            [sys.executable, "-c", f"import {entry}"],
            capture_output=True,
            text=True,
            cwd=str(ENGINE.parent),
        )
        assert result.returncode == 0, f"importing {entry} first fails:\n{result.stderr}"


@pytest.mark.parametrize("adapter_type,adapter", sorted(ADAPTERS.items()))
def test_every_registered_adapter_satisfies_the_source_contract(adapter_type, adapter):
    """One interface, so downstream never learns where knowledge came from."""
    assert hasattr(adapter, "discover"), f"{adapter!r} has no discover()"
    assert callable(adapter.discover)


def test_the_grading_stage_knows_nothing_about_any_particular_source():
    """Confidence must grade a PDF item exactly as it grades an HTML one.

    If `confidence.py` ever imports an adapter, the grading rules have become
    source-specific and the subsystem is no longer reusable.
    """
    imports = imported_modules(ACQUISITION / "confidence.py")
    assert not [m for m in imports if "sources" in m], (
        "confidence must not know about individual sources"
    )


def test_adapters_do_not_import_the_orchestrator():
    """Dependency direction: `discover` imports adapters, never the reverse.

    An adapter that imported `discover` could decide what happens when it fails,
    and failure handling has to live in exactly one place (ADR-0019).
    """
    offenders = [
        str(path.relative_to(ENGINE))
        for path in (ACQUISITION / "sources").rglob("*.py")
        if "__pycache__" not in str(path)
        and any("discover" in m for m in imported_modules(path))
    ]
    assert not offenders, f"adapters must not import the orchestrator: {offenders}"


def test_the_public_surface_is_importable_from_the_package_root():
    """The subsystem's port. A consumer should not need its internal layout."""
    import ke.acquisition as acquisition

    for name in acquisition.__all__:
        assert hasattr(acquisition, name), f"{name} is exported but missing"
