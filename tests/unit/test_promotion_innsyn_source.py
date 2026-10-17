"""The innsyn channel's reader over the workspace (ADR-0016 A1, A2).

The ledger is written only through the supported innsyn commands, the way
the operator writes it: the pilot register, then record-received,
classify-received and record-case-number. The documents are synthetic
bytes, never a kommune's file.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.promotion.decisions import ReceivedKey
from lovspor.promotion.innsyn_source import ReceivedArtifact, read_received, received_source
from lovspor.promotion.models import ReceivedSource
from tests.unit.innsyn_support import FIXTURES, innsyn

FJALER = "4646-20261005T074425Z"
SULDAL = "1134-20261005T074406Z"
REQUESTED = "2009-02-16-306"
RECEIVED_AT = "2026-10-07T12:45:06Z"
PAYLOAD = b"%PDF-1.4 synthetic regulation text"
SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _run(workspace: Path, *args: str) -> None:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    _run(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    document = tmp_path / "answer" / "Forskrift om bandtvang.pdf"
    document.parent.mkdir()
    document.write_bytes(PAYLOAD)
    _run(
        target, "record-received", "--request", FJALER, "--file", str(document), "--at", RECEIVED_AT
    )
    return target


def _classify(workspace: Path, kind: str, *extra: str) -> None:
    _run(
        workspace,
        "classify-received",
        "--request",
        FJALER,
        "--document",
        SHA,
        "--kind",
        kind,
        *extra,
    )


def _read(workspace: Path, request: str = FJALER, sha: str = SHA) -> ReceivedArtifact:
    return read_received(InnsynWorkspace(workspace, []), request, sha)


def _refusal(workspace: Path, request: str = FJALER, sha: str = SHA) -> str:
    with pytest.raises(PromotionRefusedError) as caught:
        _read(workspace, request, sha)
    return str(caught.value)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def regulation(workspace: Path) -> Path:
    _classify(workspace, "regulation_text", "--answers", REQUESTED)
    return workspace


class TestReadReceived:
    def test_a_classified_regulation_text_reads_with_its_receipt(self, regulation: Path) -> None:
        artifact = _read(regulation)
        assert artifact.key == ReceivedKey(authority_id="4646", innsyn_request=FJALER, sha256=SHA)
        assert artifact.content_type == "application/pdf"
        assert artifact.payload == PAYLOAD
        assert artifact.received_at == datetime(2026, 10, 7, 12, 45, 6, tzinfo=UTC)
        assert (artifact.saksnummer, artifact.answers) == (None, REQUESTED)

    def test_the_recorded_saksnummer_is_carried(self, regulation: Path) -> None:
        _run(regulation, "record-case-number", "--request", FJALER, "--number", "2026/1234")
        assert _read(regulation).saksnummer == "2026/1234"

    def test_a_regulation_text_answering_no_requested_id(self, workspace: Path) -> None:
        _classify(workspace, "regulation_text")
        assert _read(workspace).answers is None

    def test_the_artifact_has_no_recipient_sender_letter_or_file_name(self) -> None:
        assert set(ReceivedArtifact.model_fields) == {
            "key",
            "content_type",
            "payload",
            "received_at",
            "saksnummer",
            "answers",
        }

    def test_reading_leaves_the_workspace_byte_identical(self, regulation: Path) -> None:
        before = _tree(regulation)
        _read(regulation)
        assert _tree(regulation) == before


class TestRefusals:
    def test_an_unknown_request(self, regulation: Path) -> None:
        assert "no request 4646-20261005T074426Z in the innsyn ledger" in _refusal(
            regulation, request="4646-20261005T074426Z"
        )

    def test_a_document_not_received_on_that_request(self, regulation: Path) -> None:
        message = _refusal(regulation, request=SULDAL)
        assert f"{SULDAL}: document {SHA} was not received on this request" in message

    def test_an_unknown_document(self, regulation: Path) -> None:
        assert "was not received on this request" in _refusal(regulation, sha="0" * 64)

    def test_an_unclassified_document(self, workspace: Path) -> None:
        assert "is not classified yet" in _refusal(workspace)

    @pytest.mark.parametrize("kind", ["vedtak", "minutes", "cover_letter", "other"])
    def test_every_other_kind_is_never_promoted(self, workspace: Path, kind: str) -> None:
        _classify(workspace, kind)
        message = _refusal(workspace)
        assert f"is classified {kind}" in message
        assert "ADR-0016 A1" in message

    def test_a_held_copy_tampered_after_receipt(self, regulation: Path) -> None:
        (regulation / "received" / SHA[:2] / SHA).write_bytes(PAYLOAD + b" edited")
        assert "does not hash to its name" in _refusal(regulation)

    def test_a_deleted_held_copy(self, regulation: Path) -> None:
        (regulation / "received" / SHA[:2] / SHA).unlink()
        assert "held copy is missing or unreadable" in _refusal(regulation)

    def test_an_unreadable_ledger(self, regulation: Path) -> None:
        with (regulation / "requests.jsonl").open("ab") as handle:
            handle.write(b'{"event": "torn"')
        assert "the innsyn ledger cannot be read" in _refusal(regulation)


class TestReceivedSource:
    def test_the_provenance_of_a_read_artifact(self, regulation: Path) -> None:
        _run(regulation, "record-case-number", "--request", FJALER, "--number", "2026/1234-7")
        source = received_source(_read(regulation))
        assert source == ReceivedSource(
            received_at=datetime(2026, 10, 7, 12, 45, 6, tzinfo=UTC),
            innsyn_request=FJALER,
            innsyn_saksnummer="2026/1234-7",
            source_sha256=SHA,
        )

    def test_without_a_saksnummer(self, regulation: Path) -> None:
        assert received_source(_read(regulation)).innsyn_saksnummer is None

    def test_a_saksnummer_with_letters_is_held_and_never_echoed(self, regulation: Path) -> None:
        _run(regulation, "record-case-number", "--request", FJALER, "--number", "2026/1234-7 ABC")
        with pytest.raises(PromotionRefusedError) as caught:
            received_source(_read(regulation))
        message = str(caught.value)
        assert "does not have the digits-only shape" in message
        assert "ABC" not in message
        assert "2026/1234-7" not in message
