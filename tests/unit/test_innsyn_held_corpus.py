"""``innsyn letters`` leaves out the LF ids the promoted local corpus holds (#606).

The operator's question: can a wave ask an authority again for a regulation
whose text ``lovverk`` already publishes? It cannot: the local dataset's
manifest is read through the supported reader, its current ``lf-`` ids are
held like the operator's ``--held`` ids, and the manifest says which ids
were left out and from which source. A corpus that is missing or unreadable
refuses the wave rather than holding nothing.
"""

import json
from pathlib import Path

import pytest
from click.testing import Result
from pytest_httpx import HTTPXMock

from lovspor.observatory.storage import engine_root
from lovspor.promotion.identity import ledger_lf_id
from tests.unit.innsyn_support import (
    innsyn,
    letters_workspace,
    lovverk_checkout,
    sample_lf_ledger,
)
from tests.unit.local_dataset_fixtures import local_record, write_local_manifest

FJALER_HELD = "lf-20221215-2677"


@pytest.fixture
def lf_ledger(tmp_path: Path) -> Path:
    return sample_lf_ledger(tmp_path / "lf-ledger.jsonl")


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return letters_workspace(tmp_path / "innsyn", httpx_mock)


def _corpus(root: Path, status: str = "current", doc_id: str = FJALER_HELD) -> Path:
    corpus = lovverk_checkout(root)
    record = local_record(slug="forskrift-om-noko", authority_id="4646", status=status)
    write_local_manifest(corpus, {doc_id: record})
    return corpus


def _letters(workspace: Path, out: Path, ledger: Path, *extra: str) -> Result:
    """``letters``; ``extra`` may open with the group's ``--corpus``, then the command's options."""
    before = extra[:2] if extra[:1] == ("--corpus",) else ()
    after = extra[len(before) :]
    return innsyn(
        workspace, *before, "letters", "--out", str(out), "--lf-ledger", str(ledger), *after
    )


def _wave(workspace: Path, out: Path, ledger: Path, *extra: str) -> dict[str, object]:
    result = _letters(workspace, out, ledger, *extra)
    assert result.exit_code == 0, result.output
    manifest: dict[str, object] = json.loads((out / "manifest.json").read_text())
    return manifest


def _ids(manifest: dict[str, object], authority_id: str) -> list[str]:
    rows = manifest["letters"]
    assert isinstance(rows, list)
    return next(row["lf_ids"] for row in rows if row["authority_id"] == authority_id)


class TestCorpusHeldIds:
    def test_a_current_lf_document_of_the_corpus_is_held(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger, "--corpus", str(corpus))
        assert _ids(manifest, "4646") == ["2009-02-16-306", "2024-09-19-2196"]
        assert manifest["held_out"] == [
            {"authority_id": "4646", "lf_id": "2022-12-15-2677", "held": "corpus"}
        ]

    def test_the_corpus_and_the_held_file_are_combined_with_their_sources(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        corpus = _corpus(tmp_path / "lovverk")
        held = tmp_path / "held.txt"
        held.write_text("2019-03-20-400\n2022-12-15-2677  # also in the corpus\n")
        args = ("--corpus", str(corpus), "--held", str(held))
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger, *args)
        assert _ids(manifest, "4646") == ["2009-02-16-306", "2024-09-19-2196"]
        assert _ids(manifest, "0301") == []
        assert manifest["held_out"] == [
            {"authority_id": "0301", "lf_id": "2019-03-20-400", "held": "file"},
            {"authority_id": "4646", "lf_id": "2022-12-15-2677", "held": "corpus"},
        ]

    @pytest.mark.parametrize(
        ("status", "doc_id"),
        [("removed", FJALER_HELD), ("current", "lk-4646-0a1b2c3d4e5f")],
    )
    def test_removed_documents_and_lk_ids_hold_nothing(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, status: str, doc_id: str
    ) -> None:
        corpus = _corpus(tmp_path / "lovverk", status=status, doc_id=doc_id)
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger, "--corpus", str(corpus))
        assert "2022-12-15-2677" in _ids(manifest, "4646")
        assert manifest["held_out"] == []

    def test_the_corpus_comes_from_the_environment_when_not_given(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        corpus = _corpus(tmp_path / "elsewhere" / "lovverk")
        monkeypatch.setenv("LOVVERK_CORPUS_PATH", str(corpus))
        (tmp_path / "lovverk-held" / "manifest.json").unlink()  # the fixture's checkout, not passed
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger)
        assert _ids(manifest, "4646") == ["2009-02-16-306", "2024-09-19-2196"]

    def test_an_absent_corpus_is_refused_and_nothing_is_written(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)
        (tmp_path / "lovverk-held" / "manifest.json").unlink()  # the fixture's checkout, not passed
        out = tmp_path / "wave"
        result = _letters(workspace, out, lf_ledger)
        assert result.exit_code == 1
        assert "letters needs the lovverk checkout" in result.stderr
        assert "--corpus" in result.stderr
        assert not out.exists()


class TestCorpusRefusals:
    @pytest.mark.parametrize(
        ("make", "said"),
        [
            (lambda root: root / "nowhere", "no .git"),
            (lambda root: lovverk_checkout(root / "bare", local=False), "no lokale-forskrifter"),
            (lambda root: Path("relative/lovverk"), "must be an absolute path"),
            (lambda root: engine_root(), "lovspor"),
        ],
    )
    def test_a_missing_or_foreign_corpus_refuses_the_wave(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, make: object, said: str
    ) -> None:
        corpus = make(tmp_path)  # type: ignore[operator]
        out = tmp_path / "wave"
        result = _letters(workspace, out, lf_ledger, "--corpus", str(corpus))
        assert result.exit_code == 1, result.output
        assert said in result.stderr
        assert not out.exists()

    def test_an_unreadable_local_manifest_refuses_the_wave(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        corpus = lovverk_checkout(tmp_path / "lovverk")
        (corpus / "lokale-forskrifter" / "manifest.json").write_text("{", encoding="utf-8")
        out = tmp_path / "wave"
        result = _letters(workspace, out, lf_ledger, "--corpus", str(corpus))
        assert result.exit_code == 1, result.output
        assert "does not read as the local manifest" in result.stderr
        assert not out.exists()


@pytest.mark.parametrize(
    ("doc_id", "expected"),
    [
        ("lf-20221215-2677", "2022-12-15-2677"),
        ("lf-20190320-0400", "2019-03-20-400"),
        ("lf-20190320-000001", "2019-03-20-1"),
        ("lk-4646-0a1b2c3d4e5f", None),
        ("lf-2019-03-20-400", None),
        ("sf-20190320-0400", None),
        ("", None),
    ],
)
def test_ledger_lf_id_maps_a_minted_lf_doc_id_to_the_ledger_form(
    doc_id: str, expected: str | None
) -> None:
    assert ledger_lf_id(doc_id) == expected
