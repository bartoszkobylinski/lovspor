"""The forarbeider reader fails closed, and says only a typed reason (#738, ADR-0018 PR3B-3).

Each case starts from a sandbox the supported commands made and released the
synthetic Prop. 901 L into, then breaks one thing. Nothing is served from a
dataset that is not exactly what its manifest lists and what the bundled
synthetic pages render, and no refusal prints a path or document text.
"""

import json
import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import corpus_tree
from lovspor.forarbeider.corpus_models import (
    INDEX_PATH,
    MANIFEST_PATH,
    ManifestDocument,
    manifest_text,
)
from lovspor.forarbeider.corpus_render import render_index
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK
from lovspor.forarbeider.corpus_tree import read_dataset
from lovspor.forarbeider.release_models import digest
from tests.unit.cli_output import said
from tests.unit.forarbeider_corpus_support import beside_fifo
from tests.unit.forarbeider_promotion_support import tree, withdraw
from tests.unit.forarbeider_reader_support import L_DOCUMENT, L_ID, exact, read, released_box
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")

COMMANDS = (
    ["list"],
    ["show", *exact(L_ID)],
    ["chapter", *exact(L_ID), "--chapter", "2"],
    ["provenance", *exact(L_ID)],
)


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "l")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    return released_box(desk, tmp_path / "sandbox")


def _refused_everywhere(box: Path, line: str) -> None:
    """Every command refuses with ``line``, prints no path and no document text.

    A detail holding a separator is printed as ``input``, as by every forarbeider command.
    """
    for arguments in COMMANDS:
        result = read(box, *arguments)
        assert result.exit_code == 1, (arguments, result.output)
        assert said(line, result.output), (arguments, result.output)
        assert str(box) not in result.output
        assert "Source:" not in result.output
        assert result.stdout == ""


def _forge(box: Path, change: Callable[[ManifestDocument], ManifestDocument]) -> None:
    """Rewrite the manifest and the index consistently, as someone who can write the tree could."""
    dataset = read_dataset(box)
    members = tuple(change(member) for member in dataset.manifest.documents)
    manifest = dataset.manifest.model_copy(update={"documents": members})
    (box / MANIFEST_PATH).write_text(manifest_text(manifest))
    (box / INDEX_PATH).write_text(render_index(manifest))


def _forge_markdown(box: Path, payload: bytes) -> None:
    _forge(box, lambda member: member.model_copy(update={"markdown_sha256": digest(payload)}))
    (box / L_DOCUMENT).write_bytes(payload)


def test_a_withdrawn_document_with_its_file_left_behind_is_corruption(
    desk: Desk, box: Path
) -> None:
    lingering = (box / L_DOCUMENT).read_bytes()
    assert withdraw(desk, box, L_ID).exit_code == 0
    (box / L_DOCUMENT).write_bytes(lingering)

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_manifest_forged_with_another_title_is_not_synthetic(box: Path) -> None:
    _forge(box, lambda member: member.model_copy(update={"title": "Forged title"}))

    _refused_everywhere(box, "refused: input_not_synthetic: document")


def test_a_manifest_forged_with_another_chapter_receipt_is_not_synthetic(box: Path) -> None:
    def change(member: ManifestDocument) -> ManifestDocument:
        first = member.chapters[0].model_copy(update={"text_sha256": "0" * 64})
        return member.model_copy(update={"chapters": (first, *member.chapters[1:])})

    _forge(box, change)

    _refused_everywhere(box, "refused: input_not_synthetic: document")


@pytest.mark.parametrize(
    "injected",
    [
        b"\n[click](javascript:alert(1))\n",
        b"\n<script>alert(1)</script>\n",
        b"\n---\nnormative: true\n---\n",
    ],
)
def test_markdown_or_front_matter_injected_with_a_matching_hash_is_not_synthetic(
    box: Path, injected: bytes
) -> None:
    _forge_markdown(box, (box / L_DOCUMENT).read_bytes() + injected)

    _refused_everywhere(box, "refused: input_not_synthetic: document")


def test_corrupt_front_matter_with_a_matching_hash_is_not_synthetic(box: Path) -> None:
    original = (box / L_DOCUMENT).read_bytes()
    _forge_markdown(box, original.replace(b"normative: false", b"normative: [", 1))

    _refused_everywhere(box, "refused: input_not_synthetic: document")


def test_malformed_utf8_with_a_matching_hash_is_not_synthetic(box: Path) -> None:
    _forge_markdown(box, (box / L_DOCUMENT).read_bytes() + b"\xff\xfe\n")

    _refused_everywhere(box, "refused: input_not_synthetic: document")


def test_a_document_whose_bytes_differ_from_the_manifest_is_refused(box: Path) -> None:
    (box / L_DOCUMENT).write_bytes((box / L_DOCUMENT).read_bytes() + b"\nedited\n")

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_missing_document_file_is_a_dangling_member(box: Path) -> None:
    (box / L_DOCUMENT).unlink()

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_stray_file_no_member_names_is_refused(box: Path) -> None:
    stray = box / "forarbeider/2099-2100/prop-l/9000002.md"
    stray.write_bytes((box / L_DOCUMENT).read_bytes())

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_missing_manifest_is_refused(box: Path) -> None:
    (box / MANIFEST_PATH).unlink()

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"not json", "manifest_invalid"),
        (b"\xff\xfe", "manifest_invalid"),
        (b'{"schema_version": 2}\n', "manifest_incompatible"),
    ],
)
def test_a_corrupt_or_foreign_manifest_is_refused(box: Path, payload: bytes, reason: str) -> None:
    (box / MANIFEST_PATH).write_bytes(payload)

    _refused_everywhere(box, f"refused: {reason}: manifest")


def test_a_manifest_listing_a_document_twice_is_refused(box: Path) -> None:
    payload = json.loads((box / MANIFEST_PATH).read_text())
    payload["documents"] = payload["documents"] * 2
    (box / MANIFEST_PATH).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

    _refused_everywhere(box, "refused: manifest_invalid: manifest")


def test_a_manifest_path_outside_the_dataset_is_refused(box: Path) -> None:
    payload = json.loads((box / MANIFEST_PATH).read_text())
    payload["documents"][0]["path"] = "forarbeider/../lover/README.md"
    (box / MANIFEST_PATH).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )

    _refused_everywhere(box, "refused: manifest_invalid: manifest")


def test_an_edited_index_is_refused(box: Path) -> None:
    (box / INDEX_PATH).write_text("# Forarbeider\n\nEdited.\n")

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_symlinked_document_is_refused_not_followed(box: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy.md"
    copy.write_bytes((box / L_DOCUMENT).read_bytes())
    (box / L_DOCUMENT).unlink()
    (box / L_DOCUMENT).symlink_to(copy)

    _refused_everywhere(box, "refused: unsafe_input_path: forarbeider")


def test_a_hard_linked_document_is_refused(box: Path, tmp_path: Path) -> None:
    os.link(box / L_DOCUMENT, tmp_path / "outside.md")

    _refused_everywhere(box, "refused: unsafe_input_path: forarbeider")


def _collect(results: list[Any], box: Path, arguments: list[str]) -> None:
    results.append(read(box, *arguments))


def test_a_fifo_in_place_of_a_document_is_refused_without_blocking(box: Path) -> None:
    (box / L_DOCUMENT).unlink()
    os.mkfifo(box / L_DOCUMENT, 0o600)
    fifo = box / L_DOCUMENT

    for arguments in COMMANDS:
        results: list[Any] = []
        blocked, _ = beside_fifo(partial(_collect, results, box, arguments), fifo)
        assert not blocked, "the reader blocked on a FIFO"
        assert results[0].exit_code == 1
        assert said("refused: unsafe_input_path: forarbeider", results[0].output)


def test_an_oversized_document_is_refused(box: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(corpus_tree, "MAX_CORPUS_FILE_BYTES", 1024)

    _refused_everywhere(box, "refused: input_too_large: forarbeider")


def test_bytes_that_change_during_the_read_are_refused(
    box: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = corpus_tree._read_public

    def swapped(path: Path, shown: str = "forarbeider") -> bytes:
        payload = real(path, shown)
        return payload + b"swapped" if path.name == "9000001.md" else payload

    monkeypatch.setattr(corpus_tree, "_read_public", swapped)

    _refused_everywhere(box, "refused: corpus_tree_mismatch: input")


def test_a_symlinked_dataset_directory_is_refused(box: Path, tmp_path: Path) -> None:
    (box / "forarbeider").rename(tmp_path / "moved")
    (box / "forarbeider").symlink_to(tmp_path / "moved")

    _refused_everywhere(box, "refused: unsafe_input_path: forarbeider")


def test_a_checkout_that_is_not_a_sandbox_is_refused(box: Path) -> None:
    (box / ".git" / "lovspor-synthetic-sandbox").unlink()

    _refused_everywhere(box, "refused: corpus_not_sandbox: sandbox")


def test_a_sandbox_with_a_remote_is_refused(box: Path) -> None:
    config = box / ".git" / "config"
    config.write_text(config.read_text() + '[remote "origin"]\n\turl = https://example.invalid\n')

    _refused_everywhere(box, "refused: corpus_not_sandbox: sandbox")


def test_a_sandbox_others_can_write_is_refused(box: Path) -> None:
    box.chmod(0o777)

    _refused_everywhere(box, "refused: corpus_not_sandbox: sandbox")


def test_a_symlinked_sandbox_is_refused(box: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(box)

    result = read(link, "list")

    assert result.exit_code == 1
    assert said("refused: unsafe_input_path: sandbox", result.output)


def test_a_relative_sandbox_path_is_refused() -> None:
    result = read(Path("relative/sandbox"), "list")

    assert result.exit_code == 1
    assert said("refused: unsafe_input_path: sandbox", result.output)


@pytest.mark.parametrize("left", [LOCK, JOURNAL])
def test_a_change_in_progress_is_not_read(box: Path, left: str) -> None:
    (box / left).write_text("{}")

    _refused_everywhere(box, "refused: corpus_locked: sandbox")


@pytest.mark.parametrize(
    "options",
    [
        ["--document", "../../lover/README", "--session", "2099-2100", "--type", "prop-l"],
        ["--document", L_ID, "--session", "2098-2099", "--type", "prop-l"],
        ["--document", L_ID, "--session", "2099-2100", "--type", "prop-ls"],
        ["--document", "regjeringen-9000002", "--session", "2099-2100", "--type", "prop-l"],
        ["--document", "9000001", "--session", "2099-2100", "--type", "prop-l"],
        ["--document", L_DOCUMENT, "--session", "2099-2100", "--type", "prop-l"],
    ],
)
def test_a_document_not_named_exactly_is_unknown(box: Path, options: list[str]) -> None:
    for command in ("show", "provenance"):
        result = read(box, command, *options)
        assert result.exit_code == 1, result.output
        assert said("refused: document_unknown: document", result.output)
        assert "../" not in result.output


@pytest.mark.parametrize("chapter", ["1", "4", "99"])
def test_a_chapter_not_released_is_refused(box: Path, chapter: str) -> None:
    result = read(box, "chapter", *exact(L_ID), "--chapter", chapter)

    assert result.exit_code == 1
    assert said("refused: chapter_not_released: chapter", result.output)
    assert result.stdout == ""


@pytest.mark.parametrize("chapter", ["0", "-1", "two"])
def test_a_chapter_that_is_not_a_number_from_one_is_a_usage_error(box: Path, chapter: str) -> None:
    result = read(box, "chapter", *exact(L_ID), "--chapter", chapter)

    assert result.exit_code == 2


def test_no_refusal_writes_anything(box: Path, tmp_path: Path) -> None:
    (box / INDEX_PATH).write_text("# Forarbeider\n\nEdited.\n")
    before = tree(tmp_path)

    for arguments in COMMANDS:
        assert read(box, *arguments).exit_code == 1

    assert tree(tmp_path) == before
