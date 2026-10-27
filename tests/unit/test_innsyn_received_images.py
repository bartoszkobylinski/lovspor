"""Scanned archive documents and maps arrive as images (#691): held, never read."""

import hashlib
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import RequestState, requests
from lovspor.innsyn.received import blob_path
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.promotion.extract import ExtractionHoldReason, HeldExtraction, extract_received
from tests.unit.innsyn_support import FIXTURES, innsyn

PILOT = FIXTURES / "pilot"
FJALER = "4646-20261005T074425Z"
ARRIVED = "2026-10-26T09:55:00Z"
TIFF = b"II*\x00" + b"\x00" * 60
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60


def _held(workspace: Path) -> dict[str, RequestState]:
    return requests(InnsynWorkspace(workspace, []))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    target = tmp_path / "innsyn"
    result = innsyn(target, "import-pilot", "--register", str(PILOT / "sent.csv"))
    assert result.exit_code == 0, result.output
    return target


def _receive(workspace: Path, path: Path) -> str:
    args = ("--request", FJALER, "--file", str(path), "--at", ARRIVED)
    result = innsyn(workspace, "record-received", *args)
    assert result.exit_code == 0, result.output
    return result.stdout


class TestScans:
    def test_a_tiff_scan_is_held_by_content_without_its_name(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        scan = tmp_path / "Forskrift om hundehald.TIF"
        scan.write_bytes(TIFF)
        _receive(workspace, scan)
        document = _held(workspace)[FJALER].documents[0]
        assert document.content_type == "image/tiff"
        assert blob_path(InnsynWorkspace(workspace, []), document.sha256).read_bytes() == TIFF
        assert "hundehald" not in (workspace / "requests.jsonl").read_text(encoding="utf-8")

    def test_a_scan_of_the_regulation_text_answers_the_requested_id(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        scan = tmp_path / "Forskrift.TIF"
        scan.write_bytes(TIFF)
        _receive(workspace, scan)
        sha = _held(workspace)[FJALER].documents[0].sha256
        lf_id = _held(workspace)[FJALER].sent.lf_ids[0]
        classify = ("--request", FJALER, "--document", sha, "--kind", "regulation_text")
        result = innsyn(workspace, "classify-received", *classify, "--answers", lf_id)
        assert result.exit_code == 0, result.output
        state = _held(workspace)[FJALER]
        assert lf_id not in state.unanswered_ids
        listing = innsyn(workspace, "received")
        assert f"{sha}\timage/tiff\tregulation_text\t{lf_id}" in listing.stdout

    def test_a_map_is_held_as_jpeg_and_classified_other(
        self, workspace: Path, tmp_path: Path
    ) -> None:
        kart = tmp_path / "kart-765916.jpg"
        kart.write_bytes(JPEG)
        _receive(workspace, kart)
        sha = _held(workspace)[FJALER].documents[0].sha256
        classify = ("--request", FJALER, "--document", sha, "--kind", "other")
        assert innsyn(workspace, "classify-received", *classify).exit_code == 0
        document = _held(workspace)[FJALER].documents[0]
        assert (document.content_type, document.kind, document.answers) == (
            "image/jpeg",
            "other",
            None,
        )

    def test_a_linked_scan_is_held_as_tiff(self, workspace: Path, httpx_mock: HTTPXMock) -> None:
        url = "https://example.no/arkiv/07-5328/Kunngjering.TIF"
        httpx_mock.add_response(url=url, content=TIFF, headers={"content-type": "image/tiff"})
        args = ("--request", FJALER, "--url", url, "--at", ARRIVED)
        result = innsyn(workspace, "record-received-link", *args)
        assert result.exit_code == 0, result.output
        document = _held(workspace)[FJALER].documents[0]
        assert (document.content_type, document.source_url) == ("image/tiff", url)


class TestNoTextIsRead:
    @pytest.mark.parametrize("media_type", ["image/tiff", "image/jpeg", "image/png"])
    def test_the_promotion_extractor_holds_an_image_as_an_unsupported_format(
        self, media_type: str
    ) -> None:
        result = extract_received(TIFF, media_type)
        assert isinstance(result, HeldExtraction)
        assert result.reason is ExtractionHoldReason.UNSUPPORTED_FORMAT
        assert media_type in result.detail


@pytest.mark.parametrize(
    ("suffix", "header", "expected"),
    [
        (".pdf", " Image/JPEG; charset=binary", "image/jpeg"),
        ("", "image/png", "image/png"),
        (".TIFF", "application/octet-stream", "image/tiff"),
        (".JPEG", "", "image/jpeg"),
        (".PNG", "application/octet-stream", "image/png"),
    ],
)
def test_linked_images_preserve_bytes_and_round_trip_the_media_type(
    workspace: Path, httpx_mock: HTTPXMock, suffix: str, header: str, expected: str
) -> None:
    """received.py: images are held by content; the declared type precedes the suffix."""
    url = f"https://example.no/archive/scan{suffix}?download=1"
    payload = b"\x89PNG\r\n\x1a\n\x00\xff synthetic scan\r\n"
    headers = {"content-type": header} if header else {}
    httpx_mock.add_response(url=url, content=payload, headers=headers)
    result = innsyn(
        workspace, "record-received-link", "--request", FJALER, "--url", url, "--at", ARRIVED
    )
    assert result.exit_code == 0, result.output
    (document,) = _held(workspace)[FJALER].documents
    assert document.content_type == expected
    assert document.source_url == url
    assert document.sha256 == hashlib.sha256(payload).hexdigest()
    assert blob_path(InnsynWorkspace(workspace, []), document.sha256).read_bytes() == payload
    assert len(httpx_mock.get_requests()) == 1


@pytest.mark.parametrize(
    ("status", "header", "payload", "reason"),
    [
        (200, "image/png", b"", "the document is empty"),
        (206, "image/png", b"partial scan", "HTTP 206"),
        (200, "image/gif", b"GIF89a", "image/gif is not a format the ledger holds"),
    ],
)
def test_refused_image_links_leave_no_receipt_or_blob(
    workspace: Path,
    httpx_mock: HTTPXMock,
    status: int,
    header: str,
    payload: bytes,
    reason: str,
) -> None:
    """fetch_document requires a nonempty 200 response and believes an unsupported type."""
    url = "https://example.no/archive/scan.png"
    journal = workspace / "requests.jsonl"
    before = journal.read_bytes()
    httpx_mock.add_response(
        url=url, status_code=status, content=payload, headers={"content-type": header}
    )
    result = innsyn(
        workspace, "record-received-link", "--request", FJALER, "--url", url, "--at", ARRIVED
    )
    assert result.exit_code == 1
    assert reason in result.output
    assert journal.read_bytes() == before
    assert _held(workspace)[FJALER].documents == ()
    assert not (workspace / "received").exists()
