"""Context packs, attachment, and artifact coverage.

Three properties carry this milestone, and they are the three worth testing:

**The pack must be self-contained.** The plan's acceptance criterion is "paste it
into a fresh model session with no other context and get something usable". A
test cannot judge usability, but it can assert that every fact the instruction
depends on is physically present in the output — which is the part that silently
regresses when someone trims a section.

**The ownership split must hold.** `attach` writes an artifact file (yours) and a
generation block (the engine's). If those ever swap, either the engine starts
rewriting your prose or staleness stops being computable.

**Staleness must be derived, never asserted.** An artifact is stale exactly when
`generated_from_revision` is behind the current revision. Every reader derives
it independently, so they must all agree.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import ke.pipeline as pipeline_module
from ke.acquisition import DiscoveryResult
from ke.artifacts import Coverage, refresh_pack, render_index, render_status
from ke.attach import AttachError, attach, read_content, refresh_staleness, request
from ke.generate import (
    PROMPTS_DIR,
    GenerateError,
    available_templates,
    build_pack,
    load_template,
)
from ke.harvest import harvest_pack, load_objects_with_dirs
from ke.models import ArtifactType, GenerationStatus, Revision
from ke.pack import Pack
from ke.store import load_object, update_object

from tests.test_pipeline import CLOCK, make_item

TODAY = date(2026, 8, 2)


@pytest.fixture
def pack(tmp_path) -> Pack:
    root = tmp_path / "domain-packs" / "test-pack"
    (root / "state").mkdir(parents=True)
    (root / "pack.yml").write_text(
        "name: test-pack\nid_prefix: TST\nschema_version: 1\n"
        "limits:\n  max_summary_words: 120\nsources: []\n",
        encoding="utf-8",
    )
    (root / "state" / "id-registry.json").write_text('{"prefix": "TST"}\n')
    loaded = Pack.load(root)

    original = pipeline_module.discover_all
    pipeline_module.discover_all = lambda *a, **k: DiscoveryResult(items=[make_item()])
    try:
        harvest_pack(loaded, clock=CLOCK)
    finally:
        pipeline_module.discover_all = original
    return loaded


@pytest.fixture
def stored(pack):
    """The one object in the fixture pack, with its directory on disk."""
    return load_objects_with_dirs(pack)[0]


def revise(pack, obj, directory, *, revision: int):
    """Push an object forward a revision, the way a source change would."""
    bumped = obj.with_engine_fields(
        revisions=(
            *obj.revisions,
            Revision(
                revision=revision,
                date=date(2026, 9, 1),
                changed_fields=("title",),
                summary="Source retitled",
            ),
        )
    )
    update_object(directory, bumped, obj.title, max_summary_words=pack.max_summary_words)
    return load_object(directory)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def test_every_artifact_type_has_a_template():
    """The enum and the directory must stay in step.

    A type with no template fails at the moment a user asks for it, which is the
    worst possible time to discover a packaging mistake.
    """
    loaded = {t.artifact_type for t in available_templates()}

    assert loaded == set(ArtifactType)


@pytest.mark.parametrize("artifact_type", list(ArtifactType))
def test_each_template_declares_a_version_and_an_output_path(artifact_type):
    template = load_template(artifact_type)

    assert template.prompt_version >= 1
    assert template.output.startswith(("artifacts/", "images/"))
    assert template.body.strip()


def test_a_template_whose_front_matter_contradicts_its_filename_is_refused(tmp_path):
    """Otherwise it would stamp the wrong type onto the generation block."""
    (tmp_path / "quiz.md").write_text(
        "---\nprompt_version: 1\nartifact_type: tutorial\n"
        "output: artifacts/quiz.md\n---\n\nDo a thing.\n",
        encoding="utf-8",
    )
    with pytest.raises(GenerateError, match="artifact_type"):
        load_template(ArtifactType.QUIZ, prompts_dir=tmp_path)


@pytest.mark.parametrize(
    "text,problem",
    [
        ("no front matter at all\n", "front matter"),
        ("---\nprompt_version: 1\nnever closed\n", "closed"),
        ("---\nartifact_type: quiz\noutput: artifacts/quiz.md\n---\n\nBody\n", "prompt_version"),
        ("---\nprompt_version: 1\nartifact_type: quiz\n---\n\nBody\n", "output"),
        ("---\nprompt_version: 1\nartifact_type: quiz\noutput: artifacts/quiz.md\n---\n\n\n", "empty"),
    ],
)
def test_a_malformed_template_says_what_is_wrong(tmp_path, text, problem):
    """Every failure names the specific defect, not "could not load"."""
    (tmp_path / "quiz.md").write_text(text, encoding="utf-8")

    with pytest.raises(GenerateError, match=problem):
        load_template(ArtifactType.QUIZ, prompts_dir=tmp_path)


def test_no_template_uses_vendor_specific_syntax():
    """ADR-0004 in executable form.

    The output must read correctly pasted into any model. A template carrying
    one vendor's role markers or tag conventions would work best in one place,
    which is how vendor-independence erodes — gradually, and for good reasons
    each time.
    """
    forbidden = (
        "<|im_start|>", "<|endoftext|>", "[INST]", "\n\nHuman:", "\n\nAssistant:",
        "system_prompt", "<antThinking", "role: system",
    )
    for path in PROMPTS_DIR.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            assert marker not in text, f"{path.name} contains {marker!r}"


def test_every_template_forbids_inventing_facts():
    """The single most damaging output is a confident fabrication.

    Every template must say so, because the model has no other way to know that
    a plausible menu path is worse than an admission of ignorance here.
    """
    for template in available_templates():
        body = template.body.lower()
        assert "invent" in body, f"{template.artifact_type} does not warn against invention"


# ---------------------------------------------------------------------------
# The pack must be self-contained
# ---------------------------------------------------------------------------


def test_the_pack_contains_the_instruction_and_the_knowledge(pack, stored):
    obj, directory = stored
    template = load_template(ArtifactType.TUTORIAL)

    text = build_pack(pack, obj, directory, template)

    assert template.body in text
    assert obj.title in text
    assert str(obj.id) in text


def test_the_pack_carries_the_article_text(pack, stored):
    """A pack that points at a file the model cannot open is not a pack."""
    obj, directory = stored
    article = (Path(directory) / "feature.md").read_text(encoding="utf-8").strip()

    text = build_pack(pack, obj, directory, load_template(ArtifactType.TUTORIAL))

    assert article in text


def test_the_pack_carries_the_source_link(pack, stored):
    """Attribution has to survive the copy-paste, not just the repository."""
    obj, directory = stored

    text = build_pack(pack, obj, directory, load_template(ArtifactType.QUIZ))

    assert (obj.announcement_url or obj.source_url) in text


def test_the_pack_states_that_the_article_is_not_the_source_text(pack, stored):
    """ADR-0003. A model told nothing will assume it received the original.

    That matters for what it produces: a model that thinks it has the full
    announcement will fill in detail confidently rather than flagging the gap.
    """
    obj, directory = stored

    text = build_pack(pack, obj, directory, load_template(ArtifactType.TUTORIAL))

    assert "not the source's text" in text


def test_the_pack_records_which_revision_it_was_built_from(pack, stored):
    """So a saved pack can be matched to the knowledge that produced it."""
    obj, directory = stored

    text = build_pack(pack, obj, directory, load_template(ArtifactType.TUTORIAL))

    assert f"object revision {obj.current_revision}" in text


def test_a_missing_article_degrades_rather_than_failing(pack, stored):
    """A hand-deleted `feature.md` must not make the command unusable."""
    obj, directory = stored
    (Path(directory) / "feature.md").unlink()

    text = build_pack(pack, obj, directory, load_template(ArtifactType.TUTORIAL))

    assert "missing" in text
    assert obj.title in text  # the metadata is still there and still useful


def test_a_dangling_relationship_is_reported_not_raised(pack, stored):
    """`ke validate` reports broken references; `ke generate` must still work."""
    obj, directory = stored
    linked = replace(obj, prerequisites=("TST-1999-01-001",))

    text = build_pack(pack, linked, directory, load_template(ArtifactType.TUTORIAL))

    assert "TST-1999-01-001" in text and "not found" in text


def test_the_pack_is_byte_identical_across_repeated_builds(pack, stored):
    """ADR-0022. A pack that reorders itself makes a saved copy undiffable."""
    obj, directory = stored
    template = load_template(ArtifactType.TUTORIAL)

    assert build_pack(pack, obj, directory, template) == build_pack(
        pack, obj, directory, template
    )


# ---------------------------------------------------------------------------
# Attaching
# ---------------------------------------------------------------------------


def test_attaching_writes_the_artifact_and_records_it(pack, stored):
    obj, directory = stored
    template = load_template(ArtifactType.TUTORIAL)

    updated, path = attach(
        pack, obj, directory, template, "# A tutorial\n\nBody.\n", today=TODAY
    )

    assert path.read_text(encoding="utf-8") == "# A tutorial\n\nBody.\n"
    entry = updated.generation[ArtifactType.TUTORIAL]
    assert entry.status is GenerationStatus.GENERATED
    assert entry.path == "artifacts/tutorial.md"
    assert entry.generated_at == TODAY
    assert entry.generated_from_revision == obj.current_revision
    assert entry.prompt_version == template.prompt_version


def test_the_generation_block_survives_a_reload(pack, stored):
    """Recorded in `metadata.yaml`, not only in memory."""
    obj, directory = stored
    attach(pack, obj, directory, load_template(ArtifactType.QUIZ), "Q1?\n", today=TODAY)

    reloaded = load_object(directory)

    assert reloaded.generation[ArtifactType.QUIZ].status is GenerationStatus.GENERATED


def test_an_image_artifact_goes_under_images(pack, stored):
    obj, directory = stored

    _, path = attach(
        pack, obj, directory, load_template(ArtifactType.INFOGRAPHIC),
        "A specification.\n", today=TODAY,
    )

    assert path.parent.name == "images"


def test_the_model_name_is_recorded_when_given(pack, stored):
    """Provenance only — nothing in the engine reads it (ADR-0004)."""
    obj, directory = stored

    updated, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n",
        today=TODAY, model="some-model-v2",
    )

    assert updated.generation[ArtifactType.TUTORIAL].model == "some-model-v2"


def test_attaching_without_a_model_records_nothing_rather_than_a_guess(pack, stored):
    obj, directory = stored

    updated, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )

    assert updated.generation[ArtifactType.TUTORIAL].model is None


def test_an_empty_artifact_is_refused(pack, stored, tmp_path):
    """A failed paste must not silently record a generated artifact."""
    empty = tmp_path / "empty.md"
    empty.write_text("   \n\n", encoding="utf-8")

    with pytest.raises(AttachError, match="empty"):
        read_content(str(empty))


def test_a_missing_trailing_newline_is_added(pack, stored):
    obj, directory = stored

    _, path = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL),
        "no trailing newline", today=TODAY,
    )

    assert path.read_text(encoding="utf-8").endswith("\n")


def test_replacing_a_current_artifact_needs_force(pack, stored):
    """The one place the engine would overwrite user-owned content.

    It happens only because a human typed the command, and it refuses by
    default — an artifact you have since edited by hand must not vanish because
    you re-ran a command.
    """
    obj, directory = stored
    template = load_template(ArtifactType.TUTORIAL)
    updated, _ = attach(pack, obj, directory, template, "first\n", today=TODAY)

    with pytest.raises(AttachError, match="--force"):
        attach(pack, updated, directory, template, "second\n", today=TODAY)

    attach(pack, updated, directory, template, "second\n", today=TODAY, force=True)
    assert (Path(directory) / "artifacts" / "tutorial.md").read_text() == "second\n"


def test_replacing_a_stale_artifact_needs_no_force(pack, stored):
    """Regenerating something the source has outgrown is the normal path."""
    obj, directory = stored
    template = load_template(ArtifactType.TUTORIAL)
    attached, _ = attach(pack, obj, directory, template, "first\n", today=TODAY)
    revised = revise(pack, attached, directory, revision=2)

    updated, _ = attach(pack, revised, directory, template, "second\n", today=TODAY)

    assert updated.generation[ArtifactType.TUTORIAL].generated_from_revision == 2


# ---------------------------------------------------------------------------
# The ownership split
# ---------------------------------------------------------------------------


def test_attaching_does_not_disturb_user_owned_fields(pack, stored):
    """The whole safety property, at the one command that writes to both sides."""
    obj, directory = stored
    from ke.models import LearningStatus

    edited = replace(
        obj,
        learning_status=LearningStatus.IN_PROGRESS,
        notes="My own notes, written by hand.",
        prerequisites=("TST-2026-01-001",),
        overrides=("difficulty",),
    )
    update_object(directory, edited, obj.title, max_summary_words=pack.max_summary_words)
    reloaded = load_object(directory)

    attach(
        pack, reloaded, directory, load_template(ArtifactType.TUTORIAL), "x\n",
        today=TODAY,
    )
    after = load_object(directory)

    assert after.learning_status is LearningStatus.IN_PROGRESS
    assert after.notes == "My own notes, written by hand."
    assert after.prerequisites == ("TST-2026-01-001",)
    assert after.overrides == ("difficulty",)


def test_the_engine_never_rewrites_an_attached_artifact(pack, stored, monkeypatch):
    """Artifact content is user-owned. A harvest must not touch it."""
    obj, directory = stored
    _, path = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL),
        "# Mine\n\nI edited this by hand.\n", today=TODAY,
    )
    path.write_text("# Mine\n\nAnd then I edited it again.\n", encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        pipeline_module, "discover_all", lambda *a, **k: DiscoveryResult(items=[make_item()])
    )
    harvest_pack(pack, clock=CLOCK)

    assert path.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------


def test_requesting_records_an_intention_without_a_file(pack, stored):
    obj, directory = stored

    updated = request(pack, obj, directory, load_template(ArtifactType.QUIZ))

    assert updated.generation[ArtifactType.QUIZ].status is GenerationStatus.REQUESTED
    assert not (Path(directory) / "artifacts" / "quiz.md").exists()


def test_requesting_twice_is_refused_rather_than_silently_ignored(pack, stored):
    obj, directory = stored
    template = load_template(ArtifactType.QUIZ)
    updated = request(pack, obj, directory, template)

    with pytest.raises(AttachError, match="already records"):
        request(pack, updated, directory, template)


def test_attaching_over_a_request_fulfils_it(pack, stored):
    """A requested artifact is a promise, not a lock."""
    obj, directory = stored
    template = load_template(ArtifactType.QUIZ)
    requested = request(pack, obj, directory, template)

    updated, _ = attach(pack, requested, directory, template, "Q1?\n", today=TODAY)

    assert updated.generation[ArtifactType.QUIZ].status is GenerationStatus.GENERATED


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


def test_an_artifact_goes_stale_when_the_object_is_revised(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revised = revise(pack, attached, directory, revision=2)

    assert revised.stale_artifacts == (ArtifactType.TUTORIAL,)


def test_a_stale_artifact_is_never_deleted(pack, stored):
    """CLAUDE.md: artifacts are marked, never removed."""
    obj, directory = stored
    attached, path = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "keep me\n",
        today=TODAY,
    )
    revised = revise(pack, attached, directory, revision=2)
    refresh_staleness(pack, revised, directory)

    assert path.exists()
    assert path.read_text(encoding="utf-8") == "keep me\n"


def test_refresh_writes_the_computed_status_into_metadata(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revised = revise(pack, attached, directory, revision=2)

    assert refresh_staleness(pack, revised, directory) is True
    assert load_object(directory).generation[
        ArtifactType.TUTORIAL
    ].status is GenerationStatus.STALE


def test_refresh_is_idempotent(pack, stored):
    """The second run must write nothing, or the weekly diff becomes noise."""
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revised = revise(pack, attached, directory, revision=2)
    refresh_staleness(pack, revised, directory)

    assert refresh_staleness(pack, load_object(directory), directory) is False


def test_refresh_preserves_the_revision_the_artifact_came_from(pack, stored):
    """Marking it stale must not erase the evidence of *how* stale."""
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revised = revise(pack, attached, directory, revision=2)
    refresh_staleness(pack, revised, directory)

    entry = load_object(directory).generation[ArtifactType.TUTORIAL]
    assert entry.generated_from_revision == 1
    assert entry.prompt_version == 1


def test_an_unrevised_object_has_nothing_to_refresh(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )

    assert refresh_staleness(pack, attached, directory) is False


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


def test_coverage_of_an_untouched_pack_reports_nothing(pack):
    coverage = Coverage.of(pack)

    assert coverage.rows == []
    assert not coverage.has_anything_to_report
    assert "No artifacts yet" in render_status(coverage)


def test_coverage_counts_generated_requested_and_stale(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    requested = request(pack, attached, directory, load_template(ArtifactType.QUIZ))
    revise(pack, requested, directory, revision=2)

    coverage = Coverage.of(pack)

    assert len(coverage.generated) == 1
    assert len(coverage.requested) == 1
    assert len(coverage.stale) == 1


def test_coverage_ignores_entries_that_record_no_artifact(pack, stored):
    """`none` is the absence of an artifact, not an artifact."""
    obj, directory = stored
    from ke.models import GenerationEntry

    with_none = obj.with_engine_fields(
        generation={ArtifactType.QUIZ: GenerationEntry(status=GenerationStatus.NONE)}
    )
    update_object(directory, with_none, obj.title, max_summary_words=pack.max_summary_words)

    assert Coverage.of(pack).rows == []


def test_coverage_is_ordered_deterministically(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    request(pack, attached, directory, load_template(ArtifactType.QUIZ))

    first = render_status(Coverage.of(pack))
    second = render_status(Coverage.of(pack))

    assert first == second


def test_the_stale_listing_says_how_far_behind(pack, stored):
    """"Stale" alone does not tell you whether to care."""
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revised = revise(pack, attached, directory, revision=2)
    revise(pack, revised, directory, revision=3)

    rendered = render_status(Coverage.of(pack), stale_only=True)

    assert "r1 → r3" in rendered and "2 behind" in rendered


def test_refresh_pack_reports_how_many_objects_changed(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revise(pack, attached, directory, revision=2)

    assert refresh_pack(pack) == 1
    assert refresh_pack(pack) == 0


def test_the_generation_index_renders_for_the_github_ui(pack, stored):
    obj, directory = stored
    attached, _ = attach(
        pack, obj, directory, load_template(ArtifactType.TUTORIAL), "x\n", today=TODAY
    )
    revise(pack, attached, directory, revision=2)

    markdown = render_index(Coverage.of(pack))

    assert markdown.startswith("# test-pack — artifact coverage")
    assert "| tutorial |" in markdown
    assert "Stale" in markdown


def test_the_empty_index_explains_rather_than_showing_an_empty_table(pack):
    markdown = render_index(Coverage.of(pack))

    assert "No artifacts yet" in markdown
    assert "ADR-0004" in markdown
