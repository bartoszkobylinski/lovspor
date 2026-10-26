"""Documents an authority delivered as links, read and held through the CLI (#688).

The operator's question: when a kommune answers with 47 links instead of
attachments, can each be held with the URL it was delivered as and when it
was read, using only supported commands? The server is mocked at the
transport; the bytes are synthetic.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import DocumentReceived, RequestState, ledger_journal, requests
from lovspor.innsyn.received import blob_path, link_client
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.cli_output import said
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
FJALER = "4646-20261005T074425Z"
REQUESTED = "2009-02-16-306"
ARRIVED = "2026-10-21T16:01:12Z"
URL = "https://opengov.360online.com/Dokumenter/4646/Documents/forskrift-1.pdf"
PDF = b"%PDF-1.4 synthetic regulation text, delivered as a link"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
URL_COLUMN = 6


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


def _lines(workspace: Path) -> int:
    return len(ledger_journal(InnsynWorkspace(workspace, [])).records())


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


def _link(workspace: Path, url: str = URL, at: str = ARRIVED) -> tuple[int, str]:
    args = ("--request", FJALER, "--url", url, "--at", at)
    result = innsyn(workspace, "record-received-link", *args)
    return result.exit_code, result.stdout + result.stderr


class TestRecordReceivedLink:
    def test_the_link_is_read_held_and_recorded_with_its_url(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=URL, content=PDF, headers={"content-type": "application/pdf"})
        before = datetime.now(UTC)
        code, output = _link(workspace)
        assert code == 0, output
        sha = hashlib.sha256(PDF).hexdigest()
        assert f"{FJALER}: document {sha} received from {URL}" in output
        assert blob_path(InnsynWorkspace(workspace, []), sha).read_bytes() == PDF
        (document,) = _held(workspace)[FJALER].documents
        assert (document.sha256, document.content_type, document.source_url) == (
            sha,
            "application/pdf",
            URL,
        )
        assert document.received_at == datetime(2026, 10, 21, 16, 1, 12, tzinfo=UTC)
        last = ledger_journal(InnsynWorkspace(workspace, [])).records()[-1]
        assert isinstance(last, DocumentReceived)
        assert last.fetched_at is not None
        assert before <= last.fetched_at <= datetime.now(UTC)
        request = httpx_mock.get_request()
        assert request is not None
        assert request.headers["user-agent"].startswith("lovspor/")

    def test_the_held_document_classifies_and_lists_like_an_attachment(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=URL, content=PDF, headers={"content-type": "application/pdf"})
        assert _link(workspace)[0] == 0
        sha = hashlib.sha256(PDF).hexdigest()
        args = ("--document", sha, "--kind", "regulation_text", "--answers", REQUESTED)
        result = innsyn(workspace, "classify-received", "--request", FJALER, *args)
        assert result.exit_code == 0, result.output
        listing = innsyn(workspace, "received")
        assert listing.exit_code == 0, listing.output
        (row,) = listing.stdout.splitlines()
        assert row.split("\t")[URL_COLUMN] == URL
        assert row.split("\t")[4:6] == ["regulation_text", REQUESTED]
        assert REQUESTED not in _held(workspace)[FJALER].unanswered_ids

    def test_an_attachment_lists_with_an_empty_url(self, workspace: Path, tmp_path: Path) -> None:
        path = tmp_path / "attachment.pdf"
        path.write_bytes(PDF)
        args = ("--request", FJALER, "--file", str(path), "--at", ARRIVED)
        assert innsyn(workspace, "record-received", *args).exit_code == 0
        listing = innsyn(workspace, "received")
        (row,) = listing.stdout.splitlines()
        assert row.split("\t")[URL_COLUMN] == ""
        assert _held(workspace)[FJALER].documents[0].source_url is None

    @pytest.mark.parametrize(
        ("url", "header", "expected"),
        [
            (URL, "application/pdf; charset=binary", "application/pdf"),
            (URL, "application/octet-stream", "application/pdf"),
            (URL, "text/html; charset=utf-8", "text/html"),
            ("https://example.no/answer.PDF?token=42#page=1", "", "application/pdf"),
            ("https://einnsyn.no/api/document/42", "text/html; charset=utf-8", "text/html"),
            ("https://einnsyn.no/api/document/42", "application/pdf", "application/pdf"),
            ("https://einnsyn.no/api/document/42.docx", "", DOCX),
        ],
    )
    def test_the_format_is_the_header_or_else_the_suffix(
        self, workspace: Path, httpx_mock: HTTPXMock, url: str, header: str, expected: str
    ) -> None:
        headers = {"content-type": header} if header else {}
        httpx_mock.add_response(url=url, content=PDF, headers=headers)
        assert _link(workspace, url)[0] == 0
        assert _held(workspace)[FJALER].documents[0].content_type == expected

    def test_the_client_times_out_follows_redirects_and_names_lovspor(self) -> None:
        with link_client() as client:
            assert client.timeout == httpx.Timeout(60.0)
            assert client.follow_redirects
            assert client.headers["User-Agent"].startswith("lovspor/")

    def test_a_redirect_is_followed_and_the_url_as_delivered_is_kept(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        final = "https://cdn.example.no/forskrift-1.pdf"
        httpx_mock.add_response(url=URL, status_code=302, headers={"location": final})
        httpx_mock.add_response(url=final, content=PDF, headers={"content-type": "application/pdf"})
        assert _link(workspace)[0] == 0
        assert _held(workspace)[FJALER].documents[0].source_url == URL


class TestRefusals:
    @pytest.mark.parametrize(
        ("status", "header", "content", "reason"),
        [
            (404, "text/html", b"<p>Not found</p>", "HTTP 404"),
            (206, "application/pdf", PDF[:12], "HTTP 206"),
            (200, "application/pdf", b"", "is empty"),
            (200, "image/png", b"\x89PNG", "not a format the extractor reads"),
            (200, "application/octet-stream", b"bytes", "not a format the extractor reads"),
        ],
    )
    def test_a_link_the_engine_cannot_hold_is_refused_and_nothing_is_held(
        self,
        workspace: Path,
        httpx_mock: HTTPXMock,
        status: int,
        header: str,
        content: bytes,
        reason: str,
    ) -> None:
        url = "https://einnsyn.no/api/document/42"
        httpx_mock.add_response(
            url=url, status_code=status, content=content, headers={"content-type": header}
        )
        before = _lines(workspace)
        code, output = _link(workspace, url)
        assert code == 1
        assert "Refused" in output
        assert reason in output
        assert _lines(workspace) == before
        assert not (workspace / "received").exists()

    @pytest.mark.parametrize(
        ("headers", "named"),
        [
            ({"content-type": "image/png"}, "image/png"),
            (
                {"content-type": "Application/Octet-Stream; charset=binary"},
                "application/octet-stream",
            ),
            ({}, "no content type"),
        ],
    )
    def test_the_refusal_names_the_declared_type_or_its_absence(
        self, workspace: Path, httpx_mock: HTTPXMock, headers: dict[str, str], named: str
    ) -> None:
        url = "https://einnsyn.no/api/document/42"
        httpx_mock.add_response(url=url, content=b"bytes", headers=headers)
        code, output = _link(workspace, url)
        assert code == 1
        assert (
            f"Refused: {url}: {named} is not a format the extractor reads (PDF, HTML, DOCX)"
            in output
        )

    @pytest.mark.parametrize(
        ("command", "at", "reason"),
        [
            ("record-received-link", "2026-10-21T16:01:12", "--at: give an offset or Z"),
            ("record-received", "yesterday", "--at: not an ISO-8601 instant: yesterday"),
        ],
    )
    def test_a_bad_at_names_the_option_before_anything_is_read(
        self, workspace: Path, tmp_path: Path, command: str, at: str, reason: str
    ) -> None:
        """No transport is mocked here: a bad --at is refused before the link is read."""
        path = tmp_path / "attachment.pdf"
        path.write_bytes(PDF)
        source = ("--url", URL) if command == "record-received-link" else ("--file", str(path))
        before = _lines(workspace)
        result = innsyn(workspace, command, "--request", FJALER, *source, "--at", at)
        assert result.exit_code == 2
        assert said(reason, result.output)
        assert _lines(workspace) == before

    def test_a_link_that_does_not_answer_is_refused(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_exception(httpx.ConnectError("name resolution failed"), url=URL)
        before = _lines(workspace)
        code, output = _link(workspace)
        assert code == 1
        assert "could not fetch" in output
        assert _lines(workspace) == before

    @pytest.mark.parametrize("error", [httpx.ReadTimeout, httpx.ReadError])
    def test_an_interrupted_read_holds_no_document(
        self, workspace: Path, httpx_mock: HTTPXMock, error: type[httpx.RequestError]
    ) -> None:
        httpx_mock.add_exception(error("interrupted response"), url=URL)
        before = _lines(workspace)
        code, output = _link(workspace)
        assert code == 1
        assert "could not fetch: interrupted response" in output
        assert _lines(workspace) == before
        assert not (workspace / "received").exists()

    def test_a_link_fetched_before_the_mail_arrived_is_refused(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=URL, content=PDF, headers={"content-type": "application/pdf"})
        before = _lines(workspace)
        code, output = _link(workspace, at="2099-01-01T00:00:00Z")
        assert code == 1
        assert "fetched after the mail" in output
        assert _lines(workspace) == before

    def test_a_link_that_is_not_http_is_refused_before_anything_is_read(
        self, workspace: Path
    ) -> None:
        before = _lines(workspace)
        code, output = _link(workspace, url="ftp://example.no/forskrift.pdf")
        assert code == 1
        assert "Refused: ftp://example.no/forskrift.pdf: could not fetch" in output
        assert _lines(workspace) == before

    def test_the_same_bytes_from_two_links_are_one_document(
        self, workspace: Path, httpx_mock: HTTPXMock
    ) -> None:
        other = "https://opengov.360online.com/Dokumenter/4646/Documents/forskrift-1-copy.pdf"
        for url in (URL, other):
            httpx_mock.add_response(
                url=url, content=PDF, headers={"content-type": "application/pdf"}
            )
        assert _link(workspace)[0] == 0
        code, output = _link(workspace, other)
        assert code == 1
        assert "already received" in output


def test_a_linked_receipt_needs_both_url_and_fetch_time() -> None:
    base = {
        "request_id": FJALER,
        "recorded_at": datetime(2026, 10, 21, 16, 5, tzinfo=UTC),
        "received_at": datetime(2026, 10, 21, 16, 1, 12, tzinfo=UTC),
        "sha256": hashlib.sha256(PDF).hexdigest(),
        "content_type": "application/pdf",
    }
    with pytest.raises(ValueError, match="both"):
        DocumentReceived.model_validate({**base, "source_url": URL})
    with pytest.raises(ValueError, match="both"):
        DocumentReceived.model_validate(
            {**base, "fetched_at": datetime(2026, 10, 21, 16, 5, tzinfo=UTC)}
        )


@pytest.mark.parametrize("fetched_at", ["2026-10-21T16:05:00", "2026-10-21T18:05:00+02:00"])
def test_link_fetch_time_must_be_explicit_utc(fetched_at: str) -> None:
    with pytest.raises(ValueError, match="UTC"):
        DocumentReceived.model_validate(
            {
                "request_id": FJALER,
                "recorded_at": ARRIVED,
                "received_at": ARRIVED,
                "sha256": hashlib.sha256(PDF).hexdigest(),
                "content_type": "application/pdf",
                "source_url": URL,
                "fetched_at": fetched_at,
            }
        )


def test_receipts_round_trip_with_legacy_attachments_and_simultaneous_link_fetch() -> None:
    base = {
        "request_id": FJALER,
        "recorded_at": ARRIVED,
        "received_at": ARRIVED,
        "sha256": hashlib.sha256(PDF).hexdigest(),
        "content_type": "application/pdf",
    }
    attachment = DocumentReceived.model_validate(base)
    assert attachment.source_url is None
    assert attachment.fetched_at is None
    linked = DocumentReceived.model_validate({**base, "source_url": URL, "fetched_at": ARRIVED})
    assert linked.fetched_at == linked.received_at
    for receipt in (attachment, linked):
        assert DocumentReceived.model_validate_json(receipt.model_dump_json()) == receipt
