"""Failed downloads cannot become the crosswalk's digest-addressed evidence.

Enforces capture_archive's stated contract: download into staging and only
then retain verified bytes under their digest, without overwriting a pin.
"""

from collections.abc import Iterator
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk.capture import ARCHIVE_URL, capture_archive
from lovspor.errors import NetworkError
from lovspor.sources import lovdata
from tests.unit.crosswalk_support import tar_bytes


class InterruptedBody(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield b"partial archive"
        raise httpx.ReadTimeout("download interrupted")


@pytest.mark.parametrize("failure", ["truncated", "oversized", "timeout"])
def test_failed_archive_download_leaves_no_pin_and_can_be_retried(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    monkeypatch.setattr(lovdata, "_RETRY_BASE_DELAY_SECONDS", 0)
    body = tar_bytes({})
    name = "lovtidend-avd1-2026.tar.bz2"
    entry = lovdata.LovdataArchive.model_validate(
        {
            "filename": name,
            "description": "synthetic empty archive",
            "sizeBytes": len(body),
            "lastModified": "2026-10-01T00:00:00Z",
        }
    )
    url = ARCHIVE_URL.format(name)
    for _ in range(3):
        if failure == "timeout":
            httpx_mock.add_response(url=url, stream=InterruptedBody())
        else:
            broken = body[:-1] if failure == "truncated" else body + b"x"
            httpx_mock.add_response(url=url, content=broken)

    with lovdata.LovdataClient() as client:
        with pytest.raises(NetworkError, match="ReadTimeout|size mismatch"):
            capture_archive(client, entry, tmp_path)

        assert len(httpx_mock.get_requests()) == 3
        assert list((tmp_path / "lovdata" / ".staging").iterdir()) == []
        assert list((tmp_path / "lovdata").iterdir()) == [tmp_path / "lovdata" / ".staging"]

        httpx_mock.add_response(url=url, content=body)
        record = capture_archive(client, entry, tmp_path)

    digest = sha256(body).hexdigest()
    assert record["path"] == f"lovdata/{digest}/{name}"
    assert record["sha256"] == digest
    assert record["size_bytes"] == record["catalogue_size_bytes"] == len(body)
    assert (tmp_path / record["path"]).read_bytes() == body
    assert list((tmp_path / "lovdata" / ".staging").iterdir()) == []
    assert len(httpx_mock.get_requests()) == 4
