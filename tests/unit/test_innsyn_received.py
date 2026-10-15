"""Documents received in answer to a request, written only through the CLI (#509 point 5).

The operator's question: can a document an authority sent be held, and said
to be the text of a requested regulation, using only supported commands? The
pilot register seeds the ledger the way the owner did; the documents are
synthetic bytes, never a kommune's file.
"""

import hashlib
from pathlib import Path

import pytest

from lovspor.innsyn.ledger import RequestState, requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
FJALER = "4646-20261005T074425Z"
SULDAL = "1134-20261005T074406Z"
REQUESTED = "2009-02-16-306"
RECEIVED_AT = "2026-10-07T12:45:06Z"


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


@pytest.fixture
def document(tmp_path: Path) -> Path:
    path = tmp_path / "answer" / "Forskrift om bandtvang.pdf"
    path.parent.mkdir()
    path.write_bytes(b"%PDF-1.4 synthetic regulation text")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receive(workspace: Path, path: Path, request: str = FJALER, at: str = RECEIVED_AT) -> str:
    result = innsyn(
        workspace, "record-received", "--request", request, "--file", str(path), "--at", at
    )
    return result.stdout + result.stderr if result.exit_code else result.stdout


def _classify(workspace: Path, sha: str, *extra: str) -> tuple[int, str]:
    result = innsyn(workspace, "classify-received", "--request", FJALER, "--document", sha, *extra)
    return result.exit_code, result.stdout + result.stderr


class TestRecordReceived:
    def test_the_document_is_held_by_content_and_recorded_on_its_request(
        self, workspace: Path, document: Path
    ) -> None:
        sha = _sha(document)
        out = _receive(workspace, document)
        assert f"{FJALER}: document {sha} received" in out
        blob = workspace / "received" / sha[:2] / sha
        assert blob.read_bytes() == document.read_bytes()
        held = _held(workspace)[FJALER].documents
        assert [(d.sha256, d.content_type, d.kind, d.answers) for d in held] == [
            (sha, "application/pdf", None, None)
        ]
        assert held[0].received_at.isoformat() == "2026-10-07T12:45:06+00:00"

    def test_the_filename_is_not_kept(self, workspace: Path, document: Path) -> None:
        _receive(workspace, document)
        assert "bandtvang" not in (workspace / "requests.jsonl").read_text()

    def test_the_same_document_twice_on_one_request_is_refused(
        self, workspace: Path, document: Path
    ) -> None:
        _receive(workspace, document)
        again = _receive(workspace, document)
        assert "already received" in again
        assert len(_held(workspace)[FJALER].documents) == 1

    def test_one_document_may_answer_two_requests(self, workspace: Path, document: Path) -> None:
        _receive(workspace, document)
        _receive(workspace, document, request=SULDAL)
        assert len(_held(workspace)[SULDAL].documents) == 1

    @pytest.mark.parametrize(
        ("request_id", "at", "reason"),
        [
            (FJALER, "2026-10-05T07:44:24Z", "before it was sent"),
            ("4646-20261005T074426Z", RECEIVED_AT, "no request"),
        ],
    )
    def test_a_receipt_the_ledger_cannot_take_stores_nothing(
        self, workspace: Path, document: Path, request_id: str, at: str, reason: str
    ) -> None:
        out = _receive(workspace, document, request=request_id, at=at)
        assert reason in out
        assert not (workspace / "received").exists()

    @pytest.mark.parametrize(
        ("name", "media_type"),
        [
            ("a.PDF", "application/pdf"),
            ("a.html", "text/html"),
            ("a.htm", "text/html"),
            (
                "a.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        ],
    )
    def test_the_media_type_is_read_from_the_suffix(
        self, workspace: Path, tmp_path: Path, name: str, media_type: str
    ) -> None:
        path = tmp_path / name
        path.write_bytes(name.encode())
        _receive(workspace, path)
        assert _held(workspace)[FJALER].documents[0].content_type == media_type

    @pytest.mark.parametrize("name", ["a.doc", "a.odt", "a"])
    def test_a_format_the_extractor_cannot_read_is_refused(
        self, workspace: Path, tmp_path: Path, name: str
    ) -> None:
        path = tmp_path / name
        path.write_bytes(b"x")
        out = _receive(workspace, path)
        assert "not a format the extractor reads" in out
        assert _held(workspace)[FJALER].documents == ()

    def test_a_missing_or_empty_file_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        assert "no such file" in _receive(workspace, tmp_path / "none.pdf")
        empty = tmp_path / "empty.pdf"
        empty.write_bytes(b"")
        assert "is empty" in _receive(workspace, empty)
        assert _held(workspace)[FJALER].documents == ()


class TestClassifyReceived:
    def test_a_regulation_text_names_the_requested_id_it_answers(
        self, workspace: Path, document: Path
    ) -> None:
        sha = _sha(document)
        _receive(workspace, document)
        code, out = _classify(workspace, sha, "--kind", "regulation_text", "--answers", REQUESTED)
        assert code == 0, out
        held = _held(workspace)[FJALER].documents[0]
        assert (held.kind, held.answers) == ("regulation_text", REQUESTED)

    def test_without_answers_the_document_was_not_requested(
        self, workspace: Path, document: Path
    ) -> None:
        sha = _sha(document)
        _receive(workspace, document)
        assert _classify(workspace, sha, "--kind", "minutes")[0] == 0
        listed = innsyn(workspace, "received").stdout
        assert (
            f"{FJALER}\t{RECEIVED_AT[:-1]}+00:00\t{sha}\tapplication/pdf\tminutes\tunrequested"
            in (listed)
        )

    def test_a_later_classification_replaces_the_earlier(
        self, workspace: Path, document: Path
    ) -> None:
        sha = _sha(document)
        _receive(workspace, document)
        _classify(workspace, sha, "--kind", "minutes")
        _classify(workspace, sha, "--kind", "regulation_text", "--answers", REQUESTED)
        held = _held(workspace)[FJALER].documents[0]
        assert (held.kind, held.answers) == ("regulation_text", REQUESTED)

    @pytest.mark.parametrize(
        ("extra", "reason"),
        [
            (("--kind", "regulation_text", "--answers", "2009-02-16-307"), "did not ask for"),
            (("--kind", "minutes", "--answers", REQUESTED), "only a regulation text or vedtak"),
            (("--kind", "draft"), "kind"),
        ],
    )
    def test_a_classification_the_request_does_not_support_is_refused(
        self, workspace: Path, document: Path, extra: tuple[str, ...], reason: str
    ) -> None:
        sha = _sha(document)
        _receive(workspace, document)
        code, out = _classify(workspace, sha, *extra)
        assert code == 1
        assert reason in out
        assert _held(workspace)[FJALER].documents[0].kind is None

    def test_a_document_not_received_on_the_request_is_refused(self, workspace: Path) -> None:
        code, out = _classify(workspace, "0" * 64, "--kind", "minutes")
        assert code == 1
        assert "not received" in out


class TestListing:
    def test_unclassified_documents_are_listed_and_counted(
        self, workspace: Path, document: Path
    ) -> None:
        _receive(workspace, document)
        result = innsyn(workspace, "received")
        assert result.exit_code == 0, result.output
        assert result.stdout.endswith("\tapplication/pdf\tunclassified\t\n")
        assert "1 documents, 1 unclassified" in result.stderr

    def test_requested_ids_without_a_received_text_are_counted(
        self, workspace: Path, document: Path
    ) -> None:
        sha = _sha(document)
        _receive(workspace, document)
        _classify(workspace, sha, "--kind", "regulation_text", "--answers", REQUESTED)
        result = innsyn(workspace, "received")
        assert "0 unclassified" in result.stderr
        assert f"{FJALER}: 2 of 3 requested ids without a received text" in result.stderr
