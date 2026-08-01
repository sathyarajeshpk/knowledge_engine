"""Context packs: everything a model needs, and nothing it has to be told twice.

This is the module where the whole "AI is a consumer, never a producer inside
the pipeline" design finally pays off (ADR-0004). The engine does not call a
model. It assembles a **self-contained document** — instruction, knowledge,
provenance — that you paste into whichever model you happen to be using, and
paste the answer back with `--attach`.

That copy-paste step looks primitive next to an API call. It is the point:

* **No API key, no bill, no rate limit.** The running cost of this feature is
  zero and stays zero.
* **No vendor.** The same pack works in any model, including ones that do not
  exist yet.
* **A human reads the output before it is stored.** Everything generated here is
  plausible-sounding text about a technical subject, which is exactly the
  category where a wrong answer is hardest to spot. A review step is not
  friction; it is the quality control.

## Self-contained means self-contained

A pack that assumes the model has read something else is not a pack. The test
that matters is the one in the plan: *paste it into a fresh session with no
other context and confirm the result is usable.* So a pack carries the article,
the metadata a model would otherwise guess at, the source link, and the related
objects the instruction might need — with related objects deliberately trimmed
to a summary each, because a tutorial does not need three full articles and a
model given too much context uses it.

## What the engine will not do

`ke generate` never runs during a scheduled harvest. `test_security.py` asserts
that on the workflow file, because this is the one rule whose violation would
quietly reintroduce a per-run cost and a vendor dependency to a system whose
entire design exists to avoid both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ke.models import ArtifactType, KnowledgeObject
from ke.pack import Pack

#: Where the templates live: **inside the package**, not beside it.
#:
#: Packaged with the engine rather than with a domain pack because a prompt is
#: engine behaviour, not knowledge — every pack should get the same instruction
#: for the same artifact type.
#:
#: Inside `ke/` rather than at `engine/prompts/` because anything outside the
#: package is not installed with it. The first version of this pointed one level
#: up; it worked under `pip install -e .` and shipped **zero templates** in a
#: real install, where `ke generate` would have failed for every artifact type
#: with "no prompt template". `test_packaging.py` installs the wheel and checks.
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

#: Related objects are included as a summary line each, not in full. Enough for a
#: model to see the neighbourhood, not so much that the instruction competes with
#: the context for attention.
MAX_RELATED = 8


class GenerateError(Exception):
    """The template or the object could not be resolved."""


@dataclass(frozen=True)
class Template:
    """One prompt template: its front matter and its instruction body."""

    artifact_type: ArtifactType
    prompt_version: int
    output: str
    description: str
    body: str

    @property
    def is_image(self) -> bool:
        """Whether the artifact belongs under `images/` rather than `artifacts/`."""
        return self.output.startswith("images/")


def _split_front_matter(text: str, source: Path) -> tuple[dict, str]:
    """Separate the YAML block from the instruction.

    Hand-rolled rather than reached for a library: the format is three lines of
    structure and a dependency for that would be absurd. `safe_load` still does
    the parsing, so the same guarantee applies here as everywhere else.
    """
    if not text.startswith("---\n"):
        raise GenerateError(f"{source.name}: missing YAML front matter")
    _, _, rest = text.partition("---\n")
    front, sep, body = rest.partition("\n---\n")
    if not sep:
        raise GenerateError(f"{source.name}: front matter is not closed with ---")
    try:
        meta = yaml.safe_load(front) or {}
    except yaml.YAMLError as exc:
        raise GenerateError(f"{source.name}: front matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise GenerateError(f"{source.name}: front matter must be a mapping")
    return meta, body.strip()


def load_template(artifact_type: ArtifactType, *, prompts_dir: Path | None = None) -> Template:
    """Read one template, or explain precisely what is wrong with it."""
    directory = prompts_dir or PROMPTS_DIR
    path = directory / f"{artifact_type}.md"
    if not path.is_file():
        raise GenerateError(f"no prompt template for {artifact_type} (expected {path})")

    meta, body = _split_front_matter(path.read_text(encoding="utf-8"), path)

    declared = meta.get("artifact_type")
    if declared != str(artifact_type):
        # A template whose front matter disagrees with its filename would
        # silently stamp the wrong type onto the generation block.
        raise GenerateError(
            f"{path.name}: front matter says artifact_type: {declared!r}, "
            f"but the filename says {artifact_type!r}"
        )
    version = meta.get("prompt_version")
    if not isinstance(version, int) or version < 1:
        raise GenerateError(
            f"{path.name}: prompt_version must be a positive integer, got {version!r}"
        )
    output = meta.get("output")
    if not isinstance(output, str) or not output:
        raise GenerateError(f"{path.name}: front matter needs an `output` path")
    if not body:
        raise GenerateError(f"{path.name}: the instruction body is empty")

    return Template(
        artifact_type=artifact_type,
        prompt_version=version,
        output=output,
        description=str(meta.get("description", "")),
        body=body,
    )


def available_templates(*, prompts_dir: Path | None = None) -> list[Template]:
    """Every template that loads, in enum order."""
    found = []
    for artifact_type in ArtifactType:
        try:
            found.append(load_template(artifact_type, prompts_dir=prompts_dir))
        except GenerateError:
            continue
    return found


# ---------------------------------------------------------------------------
# Assembling the pack
# ---------------------------------------------------------------------------


def _related_ids(obj: KnowledgeObject) -> list[str]:
    """The objects a model might need to make sense of this one.

    Prerequisites first: a tutorial that assumes knowledge the reader does not
    have is the failure this list exists to prevent. Deduplicated while keeping
    that order, because `dict.fromkeys` preserves insertion and `set` would make
    the output non-deterministic (ADR-0022).
    """
    ordered = [*obj.prerequisites, *obj.builds_on, *obj.related_topics]
    return list(dict.fromkeys(ordered))[:MAX_RELATED]


def _facts(obj: KnowledgeObject) -> list[str]:
    """The metadata a model would otherwise guess at.

    Deliberately not the whole `metadata.yaml`. Learning state, revision history
    and content hashes tell a model nothing about the subject and would dilute
    what does.
    """
    rows = [
        ("Feature ID", str(obj.id)),
        ("Title", obj.title),
        ("Category", obj.category or "unclassified"),
        ("Tags", ", ".join(obj.tags) or "none"),
        ("Tier", f"{int(obj.tier)} ({obj.tier.name.lower().replace('_', '-')})"),
        ("Difficulty", str(obj.difficulty)),
        ("Status", str(obj.status)),
    ]
    if obj.published_date:
        rows.append(
            ("Published", f"{obj.published_date} ({obj.date_precision} precision)")
        )
    else:
        rows.append(("Published", f"unknown; first seen {obj.discovered_date}"))
    rows.append(("Source", f"{obj.source_name} ({obj.source_authority})"))
    if obj.version:
        rows.append(("Release", obj.version))
    return [f"- **{label}:** {value}" for label, value in rows]


def _article(directory: Path) -> str:
    """The stored article, or an honest note that it is missing.

    Raising here would refuse to generate anything for an object whose
    `feature.md` was deleted by hand, which is worse than generating from the
    metadata alone and saying so.
    """
    path = Path(directory) / "feature.md"
    if not path.is_file():
        return "_(the stored article is missing; work from the metadata above)_"
    return path.read_text(encoding="utf-8").strip()


def build_pack(
    pack: Pack,
    obj: KnowledgeObject,
    directory: Path,
    template: Template,
) -> str:
    """The full context pack, ready to paste into any model."""
    from ke.retrieve import get

    lines = [
        f"<!-- Knowledge Engine context pack -->",
        f"<!-- {obj.id} · {template.artifact_type} · "
        f"prompt v{template.prompt_version} · object revision "
        f"{obj.current_revision} -->",
        "",
        template.body,
        "",
        "---",
        "",
        "# Knowledge",
        "",
        "## Facts",
        "",
        *_facts(obj),
        "",
        "## Article",
        "",
        _article(directory),
        "",
    ]

    related = _related_ids(obj)
    if related:
        lines += [
            "## Related knowledge",
            "",
            "Summaries only. Use these for context and continuity; they are not "
            "the subject of this task.",
            "",
        ]
        for feature_id in related:
            try:
                other, _ = get(pack, feature_id)
            except KeyError:
                # A dangling reference is a data problem for `ke validate` to
                # report, not a reason this command should fail.
                lines.append(f"- `{feature_id}` — not found in this pack")
                continue
            lines.append(f"- **{other.id} — {other.title}** ({other.category or 'unclassified'})")
        lines.append("")

    lines += [
        "## Source",
        "",
        f"- {obj.announcement_url or obj.source_url}",
        "",
        "The article above is an original short summary written by the Knowledge "
        "Engine, not the source's text. Follow the link for the full "
        "announcement.",
        "",
        "---",
        "",
        "Produce only the artifact described in the task. If a fact you need is "
        "not above, say so rather than inventing it.",
        "",
    ]
    return "\n".join(lines)


def suggested_filename(template: Template) -> str:
    """Where `--attach` will write, relative to the object's directory."""
    return template.output
