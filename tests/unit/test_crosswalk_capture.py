"""Crosswalk capture into a write-once cache (#610, P2). HTTP is mocked with pytest-httpx only.

Every Lovdata and Stortinget body here is SYNTHETIC, in the real shapes.
"""

import json
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk import capture as cap
from lovspor.crosswalk.cache import MANIFEST, load_manifest
from lovspor.crosswalk.commands import capture_impl, read_law_ids
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.errors import NetworkError, StortingetStatusError
from lovspor.sources.lovdata import LovdataArchive, LovdataClient
from tests.unit.crosswalk_support import document, pubs, sak, saker, tar_bytes

API = "https://api.lovdata.no/v1/publicData"
EKSPORT = "https://data.stortinget.no/eksport"
LAW = "LOV-2022-06-17-63"
COMMIT = "a" * 40


class FakeClock:
    def __init__(self) -> None:
        self.now, self.slept = 100.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _pacer(tmp_path: Path, clock: FakeClock | None = None) -> cap.Pacer:
    clock = clock or FakeClock()
    return cap.Pacer(tmp_path / "progress.log", gap=0.7, clock=clock, sleep=clock.sleep)


def test_pacer_spaces_requests_but_never_the_first(tmp_path: Path) -> None:
    clock = FakeClock()
    pacer = _pacer(tmp_path, clock)
    pacer.wait()
    assert clock.slept == []
    clock.now += 0.2
    pacer.wait()
    assert clock.slept == [pytest.approx(0.5)]
    clock.now += 5
    pacer.wait()
    assert clock.slept == [pytest.approx(0.5)]


def test_pacer_notes_lines(tmp_path: Path) -> None:
    pacer = _pacer(tmp_path)
    pacer.note("one")
    pacer.note("two")
    lines = (tmp_path / "progress.log").read_text().splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == ["one", "two"]


@pytest.mark.parametrize(
    ("error", "pause"),
    [
        (StortingetStatusError(429, "u"), cap.RATE_LIMIT_PAUSE_SECONDS),
        (StortingetStatusError(503, "u"), cap.REQUEST_GAP_SECONDS * 4),
        (StortingetStatusError(500, "u"), cap.REQUEST_GAP_SECONDS * 4),
        (NetworkError("reset"), cap.REQUEST_GAP_SECONDS * 4),
        (StortingetStatusError(404, "u"), None),
        (StortingetStatusError(499, "u"), None),
        (CrosswalkError("parse"), None),
    ],
)
def test_only_rate_limits_server_errors_and_transport_failures_are_retried(
    error: Exception, pause: float | None
) -> None:
    assert cap._retryable(error) == pause  # type: ignore[arg-type]


def test_attempt_retries_then_returns(tmp_path: Path) -> None:
    clock = FakeClock()
    calls = iter([StortingetStatusError(429, "u"), "ok"])

    def request() -> str:
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    assert cap.attempt(_pacer(tmp_path, clock), request) == "ok"
    assert cap.RATE_LIMIT_PAUSE_SECONDS in clock.slept


def test_attempt_is_bounded(tmp_path: Path) -> None:
    calls = []

    def request() -> str:
        calls.append(1)
        raise StortingetStatusError(503, "u")

    with pytest.raises(StortingetStatusError):
        cap.attempt(_pacer(tmp_path), request)
    assert len(calls) == cap.ATTEMPTS == 3


def test_attempt_does_not_retry_a_final_refusal(tmp_path: Path) -> None:
    calls = []

    def request() -> str:
        calls.append(1)
        raise StortingetStatusError(404, "u")

    with pytest.raises(StortingetStatusError):
        cap.attempt(_pacer(tmp_path), request)
    assert calls == [1]


def test_store_once_never_rewrites(tmp_path: Path) -> None:
    path = cap.store_once(tmp_path / "d", "x", b"one")
    assert cap.store_once(tmp_path / "d", "x", b"one") == path
    with pytest.raises(CrosswalkError, match="differs"):
        cap.store_once(tmp_path / "d", "x", b"two")
    assert path.read_bytes() == b"one"


def test_refused_listing_is_recorded_with_its_status(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    url = f"{EKSPORT}/saker?sesjonid=2024-2025"
    for _ in range(cap.ATTEMPTS):
        httpx_mock.add_response(url=url, status_code=429)
    with httpx.Client() as client:
        record = cap.capture_listing(client, tmp_path, _pacer(tmp_path), ("saker", "2024-2025"))
    assert (record["http_status"], record["key"], record["stage"]) == (
        429,
        "2024-2025",
        "discovery",
    )
    assert record["error"] == f"StortingetStatusError: Stortinget HTTP 429: {url}"
    assert "path" not in record
    assert "FAIL saker 2024-2025" in (tmp_path / "progress.log").read_text()


def test_transport_failure_is_recorded_without_status(tmp_path: Path) -> None:
    record = cap._failure({"stage": "discovery"}, NetworkError("down"))
    assert (record["http_status"], record["error"]) == (None, "NetworkError: down")


def test_listing_is_stored_under_its_digest(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = saker((89016, "Lovvedtak 85 (2021-2022)"))
    url = f"{EKSPORT}/saker?sesjonid=2021-2022"
    httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})
    with httpx.Client() as client:
        record = cap.capture_listing(client, tmp_path, _pacer(tmp_path), ("saker", "2021-2022"))
    digest = sha256(body).hexdigest()
    assert record["path"] == f"stortinget/listings/saker-2021-2022-{digest}.xml"
    assert (tmp_path / record["path"]).read_bytes() == body
    assert (record["sha256"], record["size_bytes"], record["error"]) == (digest, len(body), None)
    assert record["source_url"] == url


def test_unknown_listing_kind_is_refused() -> None:
    with pytest.raises(CrosswalkError):
        cap._kind("sak")
    assert cap._kind("saker") == "saker"
    assert cap._kind("publikasjoner") == "publikasjoner"


def test_case_retries_one_429_then_stores_once(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    url = f"{EKSPORT}/sak?sakid=103533"
    body = sak(103533, "2024-2025", ("2025-2026", 26))
    httpx_mock.add_response(url=url, status_code=429)
    httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 103533))
    assert (record["error"], record["sha256"]) == (None, sha256(body).hexdigest())
    assert (tmp_path / record["path"]).read_bytes() == body
    assert record["path"].startswith("stortinget/discovery/sak-103533-")


def test_redirect_is_a_refusal_not_followed(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    url = f"{EKSPORT}/sak?sakid=7"
    httpx_mock.add_response(url=url, status_code=301, headers={"Location": "https://evil.example/"})
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
    assert record["http_status"] == 301
    assert len(httpx_mock.get_requests()) == 1


def _entry(name: str, size: int = 10) -> LovdataArchive:
    return LovdataArchive.model_validate(
        {
            "filename": name,
            "description": "d",
            "sizeBytes": size,
            "lastModified": "2026-10-01T00:00:00Z",
        }
    )


def test_only_avd1_archives_are_captured() -> None:
    names = [
        "gjeldende-lover.tar.bz2",
        "lovtidend-avd1-2026.tar.bz2",
        "lovtidend-avd2-2026.tar.bz2",
        "lovtidend-avd1-2001-2025.tar.bz2",
    ]
    found = cap.avd1_archives([_entry(name) for name in names])
    assert [e.filename for e in found] == [
        "lovtidend-avd1-2001-2025.tar.bz2",
        "lovtidend-avd1-2026.tar.bz2",
    ]


@pytest.mark.parametrize("size", [0, cap.ARCHIVE_BYTE_LIMIT + 1])
def test_archive_size_outside_bounds_is_refused(size: int) -> None:
    with pytest.raises(CrosswalkError, match="catalogue size"):
        cap.avd1_archives([_entry("lovtidend-avd1-2026.tar.bz2", size)])


def test_archive_at_the_bound_is_accepted() -> None:
    assert cap.avd1_archives([_entry("lovtidend-avd1-2026.tar.bz2", cap.ARCHIVE_BYTE_LIMIT)])


def test_catalogue_without_avd1_is_refused() -> None:
    with pytest.raises(CrosswalkError, match="no Lovtidend"):
        cap.avd1_archives([_entry("gjeldende-lover.tar.bz2")])


def _catalogue(httpx_mock: HTTPXMock, archives: dict[str, bytes]) -> None:
    listing = [
        {
            "filename": n,
            "description": "d",
            "sizeBytes": len(b),
            "lastModified": "2026-10-01T00:00:00Z",
        }
        for n, b in archives.items()
    ]
    httpx_mock.add_response(url=f"{API}/list", json=listing)
    for name, body in archives.items():
        httpx_mock.add_response(url=f"{API}/get/{name}", content=body)


def test_archive_is_kept_once_under_its_digest(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = tar_bytes({"lti/2022/nl-20220617-063.xml": document(LAW, "Lovvedtak 85 (2021-2022)")})
    _catalogue(httpx_mock, {"lovtidend-avd1-2001-2025.tar.bz2": body})
    httpx_mock.add_response(url=f"{API}/get/lovtidend-avd1-2001-2025.tar.bz2", content=body)
    with LovdataClient() as client:
        first = cap.capture_lovdata(client, tmp_path)[0]
        entry = _entry("lovtidend-avd1-2001-2025.tar.bz2", len(body))
        second = cap.capture_archive(client, entry, tmp_path)
    digest = sha256(body).hexdigest()
    assert first["path"] == second["path"] == f"lovdata/{digest}/lovtidend-avd1-2001-2025.tar.bz2"
    assert first["source_url"] == f"{API}/get/lovtidend-avd1-2001-2025.tar.bz2"
    assert (first["sha256"], first["size_bytes"], first["catalogue_size_bytes"]) == (
        digest,
        len(body),
        len(body),
    )
    assert first["last_modified"] == "2026-10-01T00:00:00+00:00"
    assert not list((tmp_path / "lovdata/.staging").iterdir())


@pytest.mark.parametrize(
    ("law_ids", "commit", "message"),
    [
        ([], COMMIT, "law ids"),
        (["LOV-2022-6-17-63"], COMMIT, "law ids"),
        ([LAW], "abc", "40-hex"),
        ([LAW], "A" * 40, "40-hex"),
    ],
)
def test_scope_is_checked(law_ids: list[str], commit: str, message: str) -> None:
    with pytest.raises(CrosswalkError, match=message):
        cap.checked_scope(law_ids, commit)


def test_scope_is_sorted_and_deduplicated() -> None:
    assert cap.checked_scope([LAW, "LOV-2009-06-19-107", LAW], COMMIT) == [
        "LOV-2009-06-19-107",
        LAW,
    ]


def _full_capture(httpx_mock: HTTPXMock) -> None:
    member = {"lti/2022/nl-20220617-063.xml": document(LAW, "Lovvedtak 85 (2021-2022)")}
    _catalogue(
        httpx_mock,
        {
            "lovtidend-avd1-2001-2025.tar.bz2": tar_bytes(member),
            "lovtidend-avd1-2026.tar.bz2": tar_bytes({}),
        },
    )
    xml = {"Content-Type": "text/xml"}
    listings = {
        f"{EKSPORT}/publikasjoner?publikasjontype=lovvedtak&sesjonid=2021-2022": pubs(
            "vedtak-202122-085"
        ),
        f"{EKSPORT}/saker?sesjonid=2020-2021": saker(),
        f"{EKSPORT}/saker?sesjonid=2021-2022": saker((89016, "Lovvedtak 85 (2021-2022)")),
        f"{EKSPORT}/sak?sakid=89016": sak(89016, "2021-2022", ("2021-2022", 85)),
    }
    for url, body in listings.items():
        httpx_mock.add_response(url=url, content=body, headers=xml)


def test_full_capture_writes_a_complete_manifest(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    _full_capture(httpx_mock)
    cache = tmp_path / "run"
    sources = cap.Sources(pacer=_pacer(tmp_path))
    manifest = cap.capture(cache, [LAW], COMMIT, sources)
    assert manifest == load_manifest(cache)
    assert manifest["scope"] == {"law_ids": [LAW], "lovverk_commit": COMMIT}
    assert [a["filename"] for a in manifest["lovdata"]] == [
        "lovtidend-avd1-2001-2025.tar.bz2",
        "lovtidend-avd1-2026.tar.bz2",
    ]
    keys = [(r["stage"], r["kind"], r["key"]) for r in manifest["stortinget"]]
    assert keys == [
        ("discovery", "publikasjoner", "2021-2022"),
        ("discovery", "saker", "2020-2021"),
        ("discovery", "saker", "2021-2022"),
        ("discovery", "sak", 89016),
    ]
    assert all(r["error"] is None for r in manifest["stortinget"])
    assert "finished_at" in manifest
    hosts = {httpx.URL(str(r.url)).host for r in httpx_mock.get_requests()}
    assert hosts == {"api.lovdata.no", "data.stortinget.no"}


def test_capture_never_reuses_a_directory(tmp_path: Path) -> None:
    (tmp_path / MANIFEST).write_text("{}")
    with pytest.raises(CrosswalkError, match="already holds a capture"):
        cap.capture(tmp_path, [LAW], COMMIT, cap.Sources(pacer=_pacer(tmp_path)))


def test_law_list_file(tmp_path: Path) -> None:
    laws = tmp_path / "laws.txt"
    laws.write_text(f"# frame\n\n {LAW} \nLOV-2009-06-19-107\n", encoding="utf-8")
    assert read_law_ids(laws) == [LAW, "LOV-2009-06-19-107"]


@pytest.mark.parametrize(
    ("content", "message"),
    [(b"\xff\n", "not UTF-8"), (b"x" * 1_000_001, "too large")],
)
def test_law_list_file_refusals(tmp_path: Path, content: bytes, message: str) -> None:
    laws = tmp_path / "laws.txt"
    laws.write_bytes(content)
    with pytest.raises(CrosswalkError, match=message):
        read_law_ids(laws)


def test_missing_law_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CrosswalkError, match="unreadable"):
        read_law_ids(tmp_path / "absent.txt")


def test_capture_impl_reports_counts(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    _full_capture(httpx_mock)
    laws = tmp_path / "laws.txt"
    laws.write_text(LAW + "\n", encoding="utf-8")
    line = capture_impl(tmp_path / "run", laws, COMMIT, cap.Sources(pacer=_pacer(tmp_path)))
    assert line == "captured 2 archives and 4 Stortinget responses (0 failed)"
    assert json.loads((tmp_path / "run" / MANIFEST).read_text())["scope"]["law_ids"] == [LAW]


def test_cli_exposes_capture_and_refuses_with_exit_1(tmp_path: Path) -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["ops", "crosswalk", "capture", "--help"]).exit_code == 0
    laws = tmp_path / "laws.txt"
    laws.write_text(LAW + "\n", encoding="utf-8")
    args = ["ops", "crosswalk", "capture", "--cache", str(tmp_path / "run"), "--laws", str(laws)]
    result = runner.invoke(app, [*args, "--lovverk-commit", "nope"])
    assert result.exit_code == 1
    assert "refused:" in result.output
    assert not (tmp_path / "run").exists()


def test_an_existing_digest_object_with_other_bytes_is_refused(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    name = "lovtidend-avd1-2026.tar.bz2"
    body = tar_bytes({})
    target = tmp_path / "lovdata" / sha256(body).hexdigest() / name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt")
    httpx_mock.add_response(url=f"{API}/get/{name}", content=body)
    with LovdataClient() as client, pytest.raises(CrosswalkError, match="do not match"):
        cap.capture_archive(client, _entry(name, len(body)), tmp_path)
    assert target.read_bytes() == b"corrupt"
    assert not list((tmp_path / "lovdata/.staging").iterdir())
