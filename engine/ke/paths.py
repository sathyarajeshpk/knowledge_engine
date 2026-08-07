"""One invariant: the engine only reads and writes inside the repository.

## The hole this closes

Every path the engine touches is built by joining a known root with a name it
derived itself — `pack.root / "state" / "id-registry.json"`, `knowledge_dir /
subpath / directory_name`. None of it is attacker-controlled string
concatenation, and `--pack` filters a list of already-discovered packs by name
rather than joining a path, so there is no classic `../../etc/passwd` traversal
anywhere in the CLI.

Symlinks make that reasoning insufficient. A path can be entirely engine-derived
and still land outside the repository, because a directory *component* of it is a
link. `domain-packs/x/state` pointing at `../../../.git` is a legal git object;
it survives a clone; and every path the engine builds under it is well formed.

That matters here more than in most projects because of what M8 introduced:
**packs are data, and data arrives by pull request.** ADR-0016's whole premise is
that adding a pack requires no engine change and therefore no engine review. The
weekly workflow then runs unattended holding a repository write token. A
contributed pack that can redirect a write is a contributed pack that can write
to `.github/workflows/` on a machine that will later `git push`.

## Containment rather than a list of attacks

`contained()` asks one question — does this path, with every symlink followed,
sit inside the repository root? — and the answer does not depend on having
predicted the attack. A pack root, a state file, an object directory and an
artifact target are all checked by the same function.

`Path.resolve()` is non-strict, so this works on paths that do not exist yet:
symlinked ancestors still resolve, which is exactly the case that matters when
the engine is about to create a file.

## Two layers, deliberately different

* **Write time** (`ensure_contained`) refuses. A write that escapes is stopped
  even on a machine where nothing was validated.
* **Validation time** (`SEC001` in `validate.py`) reports. CI runs `ke validate`
  on every pull request, which is where a pack definition is first seen, so an
  escaping path cannot reach `main` unnoticed in the first place.

Reading is deliberately *not* blocked. A symlink somebody put in their own
checkout — a knowledge tree on another volume, say — is their business, and
refusing to read it would break a legitimate setup to prevent nothing. CI still
flags it, so the choice is visible rather than silent.
"""

from __future__ import annotations

from pathlib import Path


class PathEscape(Exception):
    """A path resolved outside the boundary it was supposed to stay inside."""


def resolved(path: Path) -> Path:
    """`path` with symlinks followed, whether or not it exists.

    Non-strict resolution is the point: the engine checks a file it is about to
    create, and the link that redirects it is a directory above it.
    """
    return Path(path).resolve()


def contained(path: Path, root: Path) -> bool:
    """Whether `path` sits inside `root` once every symlink is followed.

    `root` is resolved too. Without that, a repository checked out under a
    symlinked path — `/tmp` on macOS, a home directory on a mounted volume —
    would report every one of its own files as an escape.
    """
    target = resolved(path)
    base = resolved(root)
    return target == base or base in target.parents


def ensure_contained(path: Path, root: Path, *, what: str = "path") -> Path:
    """Return `path`, or refuse with an explanation naming both ends.

    The message quotes the resolved location rather than the one that was
    requested. "state/id-registry.json is outside the repository" reads like a
    bug in the engine; naming where it actually lands makes the symlink obvious,
    which is the only fact that leads to a fix.
    """
    if not contained(path, root):
        raise PathEscape(
            f"{what} {path} resolves to {resolved(path)}, which is outside "
            f"{resolved(root)}. Refusing to touch it. A symlink in a domain "
            f"pack can redirect an automated write out of the repository, so "
            f"the engine treats this as fatal rather than following it."
        )
    return Path(path)


def escaping_links(root: Path) -> list[tuple[Path, Path]]:
    """Every symlink under `root` that points outside it, as `(link, target)`.

    Walked with `os.walk(followlinks=False)` so a link cycle cannot hang the
    validator, and so a link into a huge external tree is reported once rather
    than descended into.
    """
    import os

    base = resolved(root)
    found: list[tuple[Path, Path]] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            candidate = here / name
            if candidate.is_symlink() and not contained(candidate, base):
                found.append((candidate, resolved(candidate)))
    return sorted(found)
