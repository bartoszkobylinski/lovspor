"""The opt-in forarbeider reader over a synthetic sandbox, end to end (#738, ADR-0018 PR3B-3).

Everything goes through supported commands: sandbox-create, the PR3A review,
the PR3B-1 preflight, plan, simulate, sandbox-withdraw and then the reader.
The reader reads one sandbox, named on every call, and writes nothing.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.corpus_render import NOT_LAW
from lovspor.forarbeider.corpus_tree import read_dataset
from lovspor.forarbeider.models import JSON_LAYOUT
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import said, unwrapped
from tests.unit.forarbeider_promotion_support import plan, sandbox, simulate, tree, withdraw
from tests.unit.forarbeider_reader_support import (
    L_DOCUMENT,
    L_ID,
    LS_ID,
    SESSION,
    exact,
    loaded,
    read,
    released_box,
)
from tests.unit.forarbeider_release_support import (
    OWNER,
    PRIVATE_MARKER,
    REVIEWED,
    REVIEWER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

NOTICE_START = "Forarbeid: preparatory material, NOT enacted law."
EN_DASH = "\u2013"


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "l")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    return released_box(desk, tmp_path / "sandbox")


@pytest.fixture
def both(tmp_path: Path) -> Path:
    """One sandbox holding the synthetic Prop. 901 L and Prop. 902 LS."""
    path = released_box(Desk(tmp_path / "l"), tmp_path / "sandbox")
    return released_box(Desk(tmp_path / "ls", kind="ls"), path)


def test_list_names_every_released_proposition_with_its_labels(both: Path) -> None:
    listing = loaded(read(both, "list"))

    assert listing["schema_version"] == 1
    assert listing["record_type"] == "forarbeider_reader_list"
    assert listing["synthetic"] is True
    assert listing["notice"].startswith(NOTICE_START)
    assert listing["manifest_sha256"] == read_dataset(both).manifest_sha256
    documents = {item["document"]["id"]: item for item in listing["documents"]}
    assert sorted(documents) == [L_ID, LS_ID]
    first = documents[L_ID]
    assert first["reference"] == f"Prop. 901 L (2099{EN_DASH}2100)"
    assert first["document"]["session"] == SESSION
    assert first["document"]["document_type"] == "prop-l"
    assert documents[LS_ID]["document"]["document_type"] == "prop-ls"
    assert first["release_status"] == "released"
    assert first["released_chapters"] == [2, 3]


def test_show_states_labels_time_axes_and_every_released_receipt(box: Path, desk: Desk) -> None:
    result = read(box, "show", *exact(L_ID))
    shown = loaded(result)
    member = read_dataset(box).manifest.documents[0]

    assert shown["record_type"] == "forarbeider_reader_document"
    assert shown["labels"] == {
        "source_document_type": "forarbeid",
        "normative": False,
        "source_provider": "regjeringen.no",
        "rights_basis": "åndsverkloven § 14",
        "source_license": None,
        "asserts_complete_proposition": False,
    }
    assert shown["path"] == L_DOCUMENT
    assert shown["manifest_sha256"] == read_dataset(box).manifest_sha256
    assert shown["ministry"] == member.ministry
    assert shown["markdown_sha256"] == member.markdown_sha256
    assert member.ministry is not None
    assert member.markdown_sha256 is not None
    assert result.stdout.rstrip("\n") == json.dumps(shown, **JSON_LAYOUT)
    assert "å" in result.stdout
    assert shown["document"]["source_url"].startswith("https://www.regjeringen.no/no/dokumenter/")
    times = shown["times"]
    assert set(times) == {
        "source_published_at",
        "reviewed_at",
        "policy_decided_at",
        "owner_decided_at",
        "released_at",
        "time_basis",
        "recorded_at",
    }
    assert times["time_basis"] == "operator_supplied_unauthenticated"
    assert times["recorded_at"] == "git_commit_time"
    receipts = shown["chapters"]
    assert [item["chapter_number"] for item in receipts] == [2, 3]
    assert receipts[0]["source_page_url"] == desk.results[2].source_page_url
    assert receipts[0]["text_sha256"] == desk.results[2].text_sha256
    assert "retrieved_at" in receipts[0]
    assert receipts[0]["clearance"]["review_decided_at"] == REVIEWED


def test_show_marks_a_proposition_missing_a_listed_chapter_as_a_partial_excerpt(
    box: Path,
) -> None:
    shown = loaded(read(box, "show", *exact(L_ID)))
    member = read_dataset(box).manifest.documents[0]

    assert shown["coverage"] == member.coverage
    assert shown["partial_curated_excerpt"] is (member.coverage == "partial_curated_excerpt")
    assert shown["listed_chapters"] == list(member.listed_chapters)


def test_a_held_chapter_is_neither_listed_nor_readable(desk: Desk, tmp_path: Path) -> None:
    box = tmp_path / "sandbox"
    assert sandbox(desk, box).exit_code == 0
    desk.decide(2, ReviewStatus.APPROVED)
    desk.decide(3, ReviewStatus.HELD)
    _, authorization = desk.released(chapters=(2,))
    checkpoints = [desk.checkpoint(2)]
    report = said_digest(desk.preflight(authorization, checkpoints).output, "report")
    planned_sha = said_digest(plan(desk, report, checkpoints, box).output, "plan")
    assert simulate(desk, planned_sha, box).exit_code == 0

    shown = loaded(read(box, "show", *exact(L_ID)))
    available = loaded(read(box, "chapter", *exact(L_ID), "--chapter", "2"))
    held = read(box, "chapter", *exact(L_ID), "--chapter", "3")

    assert [item["chapter_number"] for item in shown["chapters"]] == [2]
    assert shown["coverage"] == "partial_curated_excerpt"
    assert shown["partial_curated_excerpt"] is True
    assert available["coverage"] == "partial_curated_excerpt"
    assert available["partial_curated_excerpt"] is True
    assert held.exit_code == 1
    assert said("refused: chapter_not_released: chapter", held.output)
    assert desk.results[3].source_page_url not in held.output


def test_chapter_prints_only_that_chapter_annotated_as_preparatory(box: Path, desk: Desk) -> None:
    chapter = loaded(read(box, "chapter", *exact(L_ID), "--chapter", "2"))

    assert chapter["record_type"] == "forarbeider_reader_chapter"
    assert chapter["chapter"]["chapter_number"] == 2
    assert chapter["partial_curated_excerpt"] is True
    assert chapter["chapter"]["source_page_url"] == desk.results[2].source_page_url
    assert chapter["notice"].startswith(NOTICE_START)
    assert chapter["synthetic"] is True
    assert chapter["markdown"].startswith(f"Source: <{desk.results[2].source_page_url}>")
    assert desk.results[3].source_page_url not in chapter["markdown"]
    markdown = (box / L_DOCUMENT).read_text()
    assert chapter["markdown"] in markdown
    assert NOT_LAW not in chapter["markdown"], "the label is the record's, not the excerpt's"


def test_provenance_ties_every_chapter_to_its_source_and_the_verified_bytes(
    box: Path, desk: Desk
) -> None:
    provenance = loaded(read(box, "provenance", *exact(L_ID)))
    member = read_dataset(box).manifest.documents[0]

    assert provenance["record_type"] == "forarbeider_reader_provenance"
    assert provenance["verification"] == "synthetic_rerender_byte_identical"
    assert provenance["markdown_sha256"] == member.markdown_sha256
    assert provenance["manifest_sha256"] == read_dataset(box).manifest_sha256
    assert provenance["renderer_version"] == 1
    assert provenance["source_url"] == member.document.source_url
    sources = {item["chapter_number"]: item for item in provenance["chapters"]}
    assert sources[2]["source_sha256"] == desk.results[2].source_sha256
    assert sources[3]["extractor_version"] == 1


def test_reading_writes_nothing_anywhere(box: Path, desk: Desk, tmp_path: Path) -> None:
    before = tree(tmp_path)

    for arguments in (["list"], ["show", *exact(L_ID)], ["provenance", *exact(L_ID)]):
        assert read(box, *arguments).exit_code == 0
    assert read(box, "chapter", *exact(L_ID), "--chapter", "2").exit_code == 0
    assert read(box, "show", *exact(LS_ID)).exit_code == 1

    assert tree(tmp_path) == before


def test_reading_never_prints_a_reviewer_owner_handle_dossier_or_evidence_marker(
    box: Path, desk: Desk
) -> None:
    """docs/operations.md's own promise for PR3B-3: only the manifest's labels, receipts
    and digests are shown, never the PR3A reviewer, the owner who decided release, the
    dossier's evidence or the desk's private ledger and workspace directories.
    """
    private = (REVIEWER, OWNER, PRIVATE_MARKER, str(desk.ledger), str(desk.workspace))

    for arguments in (
        ["list"],
        ["show", *exact(L_ID)],
        ["chapter", *exact(L_ID), "--chapter", "2"],
        ["provenance", *exact(L_ID)],
    ):
        printed = unwrapped(read(box, *arguments).output)
        for marker in private:
            assert unwrapped(marker) not in printed, (arguments, marker)


def test_the_same_sandbox_reads_byte_identically_twice(both: Path) -> None:
    for arguments in (["list"], ["show", *exact(LS_ID)], ["provenance", *exact(L_ID)]):
        assert read(both, *arguments).stdout == read(both, *arguments).stdout


def test_a_withdrawn_document_is_never_listed_or_served(desk: Desk, box: Path) -> None:
    assert withdraw(desk, box, L_ID).exit_code == 0

    listing = loaded(read(box, "list"))

    assert listing["documents"] == []
    for arguments in (
        ["show", *exact(L_ID)],
        ["provenance", *exact(L_ID)],
        ["chapter", *exact(L_ID), "--chapter", "2"],
    ):
        refused = read(box, *arguments)
        assert refused.exit_code == 1
        assert said("refused: document_not_released: document", refused.output)
        assert "Source:" not in refused.output


def test_an_empty_sandbox_lists_nothing(desk: Desk, tmp_path: Path) -> None:
    path = tmp_path / "empty"
    assert sandbox(desk, path).exit_code == 0

    listing = loaded(read(path, "list"))

    assert listing["documents"] == []
    assert listing["manifest_sha256"] is None


def test_the_sandbox_option_is_required() -> None:
    result = CliRunner().invoke(app, ["ops", "forarbeider-read", "list"])

    assert result.exit_code == 2
    assert said("--sandbox", result.output)


def _without(options: list[str], flag: str) -> list[str]:
    """``options`` as pairs, with the pair naming ``flag`` dropped."""
    pairs = [options[index : index + 2] for index in range(0, len(options), 2)]
    return [item for pair in pairs if pair[0] != flag for item in pair]


@pytest.mark.parametrize("command", ["show", "chapter", "provenance"])
@pytest.mark.parametrize("flag", ["--document", "--session", "--type"])
def test_a_document_naming_option_missing_is_a_usage_error(
    box: Path, command: str, flag: str
) -> None:
    arguments = [command, *_without(exact(L_ID), flag)]
    if command == "chapter":
        arguments += ["--chapter", "2"]

    result = read(box, *arguments)

    assert result.exit_code == 2, result.output
    assert said(flag, result.output)
    assert result.stdout == ""
