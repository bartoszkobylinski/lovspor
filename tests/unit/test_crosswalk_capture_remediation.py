"""Observable capture metadata, diagnostics and boundary regressions."""

import os
import re
import subprocess
import sys
import tracemalloc
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk import cache
from lovspor.crosswalk import capture as cap
from lovspor.crosswalk.commands import capture_impl, read_law_ids
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.legislative_chain import LovtidendMember
from lovspor.stortinget_case_source import CaseSnapshot, LawDecision, archive_sak
from tests.unit.crosswalk_support import document, sak, saker, tar_bytes
from tests.unit.test_crosswalk_cache import _entry as cached_entry
from tests.unit.test_crosswalk_capture import (
    API,
    COMMIT,
    EKSPORT,
    LAW,
    FakeClock,
    _catalogue,
    _entry,
    _full_capture,
    _pacer,
)


def test_snapshot_metadata_survives_cache_round_trip(tmp_path: Path) -> None:
    for kind, key, body in [
        ("saker", "2021-2022", saker()),
        ("sak", 89016, sak(89016, "2021-2022")),
    ]:
        (tmp_path / "body.xml").write_bytes(body)
        entry = cached_entry("discovery", kind, key, "body.xml", body)
        manifest = {"stortinget": [entry]}
        if kind == "saker":
            snapshot = cache.listing_snapshot(tmp_path, manifest, kind, str(key))
            assert snapshot is not None
            assert snapshot.kind == kind
            assert snapshot.session_id == key
        else:
            snapshot, record = cache.case_snapshot(tmp_path, manifest, "discovery", int(key))
            assert record == entry
        assert snapshot is not None
        assert snapshot.source_url == entry["source_url"]
        assert snapshot.observed_at == datetime.fromisoformat(entry["retrieved_at"])
        assert snapshot.content_type == "text/xml"
        assert snapshot.source_sha256 == sha256(body).hexdigest()
        assert snapshot.body == body


@pytest.mark.parametrize(
    "header,expected",
    [
        ({}, {"status": "unresolved", "kind": "none", "reason": "header cites no decision"}),
        (
            {"error": "ParseError: broken"},
            {"status": "unresolved", "kind": "unreadable", "reason": "ParseError: broken"},
        ),
        (
            {"lovvedtak": [{"number": 1}, {"number": 2}]},
            {"status": "ambiguous", "kind": "several", "reason": "header cites several"},
        ),
        (
            {"lovvedtak": [{"number": 1}]},
            {"status": "attested", "kind": "lovvedtak", "decision": {"number": 1}},
        ),
        (
            {"besl_o": [{"number": 1}]},
            {
                "status": "unsupported",
                "kind": "besl_o",
                "reason": "header cites Besl. O.; the reader types Lovvedtak only",
            },
        ),
    ],
)
def test_law_edge_retains_kind_and_explanation(header: dict, expected: dict) -> None:
    assert cache.law_edge(header) == expected


@pytest.mark.parametrize("count", [0, 1, 2])
def test_law_record_retains_provenance_and_identity(tmp_path: Path, count: int) -> None:
    body = document(LAW, "Lovvedtak 85 (2021-2022)")
    member = LovtidendMember(LAW, "member.xml", body, sha256(body).hexdigest())
    archive = {
        "filename": "archive.tar.bz2",
        "source_url": "https://example.test/archive",
        "sha256": "a" * 64,
        "retrieved_at": "2026-11-02T10:00:00+00:00",
        "last_modified": "2026-10-01T00:00:00+00:00",
    }
    record = cache.law_record(LAW, [(archive, member)] * count)
    assert record["law_year"] == 2022
    assert (
        record["lovdata_matches"]
        == [
            {
                "archive": archive["filename"],
                "member": member.name,
                "sha256": member.sha256,
                **{
                    f"archive_{key}": archive[key]
                    for key in ("source_url", "sha256", "retrieved_at", "last_modified")
                },
            }
        ]
        * count
    )
    if count == 1:
        assert record["law_identity"] == {"status": "attested", "reason": None}
    else:
        status = "ambiguous" if count else "unresolved"
        reason = "several members" if count else "no member in either archive"
        assert record["law_decision"] == {"status": status, "kind": "not_read", "reason": reason}


def test_malformed_law_header_preserves_exception_type() -> None:
    member = LovtidendMember(LAW, "broken.xml", b"<broken", sha256(b"<broken").hexdigest())
    error = cache.law_header(member)["error"]
    assert error.startswith("ParseError:")


def test_cited_decisions_are_sorted_across_sessions_and_numbers() -> None:
    decisions = [
        {"session_id": session, "number": number}
        for session in ("2022-2023", "2020-2021")
        for number in (12, 2)
    ]
    assert cache.cited_decisions([{"header": {"lovvedtak": decisions}}]) == [
        LawDecision("2020-2021", 2),
        LawDecision("2020-2021", 12),
        LawDecision("2022-2023", 2),
        LawDecision("2022-2023", 12),
    ]


def test_pacer_equal_boundary_and_utc_log(tmp_path: Path) -> None:
    clock = FakeClock()
    pacer = cap.Pacer(tmp_path / "log", gap=1, clock=clock, sleep=clock.sleep)
    pacer.wait()
    clock.now += 1
    pacer.wait()
    assert clock.slept == []
    pacer.note("blåbær")
    stamp, message = (tmp_path / "log").read_bytes().decode("utf-8").strip().split(" ", 1)
    assert message == "blåbær"
    assert datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0


def test_retry_log_explains_exception_and_pause(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    url = f"{EKSPORT}/sak?sakid=7"
    httpx_mock.add_response(url=url, status_code=503)
    httpx_mock.add_response(url=url, status_code=403)
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
    lines = [line.split(" ", 1)[1] for line in (tmp_path / "progress.log").read_text().splitlines()]
    assert lines == [
        f"retry after StortingetStatusError: Stortinget HTTP 503: {url}; pausing 2.8s",
        f"FAIL discovery sak 7: Stortinget HTTP 403: {url}",
    ]
    assert datetime.fromisoformat(record["retrieved_at"]).tzinfo == UTC


@pytest.mark.parametrize("kind", ["saker", "sak"])
def test_success_log_and_http_status(tmp_path: Path, httpx_mock: HTTPXMock, kind: str) -> None:
    key = "2021-2022" if kind == "saker" else 7
    body = saker() if kind == "saker" else sak(7, "2021-2022")
    url = f"{EKSPORT}/{kind}?" + (f"sesjonid={key}" if kind == "saker" else f"sakid={key}")
    httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})
    with httpx.Client() as client:
        record = (
            cap.capture_listing(client, tmp_path, _pacer(tmp_path), (kind, str(key)))
            if kind == "saker"
            else cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
        )
    assert record["http_status"] == 200
    prefix = "ok saker" if kind == "saker" else "ok discovery sak"
    assert (tmp_path / "progress.log").read_text().split(" ", 1)[
        1
    ] == f"{prefix} {key} {sha256(body).hexdigest()[:12]}\n"


def test_diagnostic_messages_are_complete() -> None:
    with pytest.raises(CrosswalkError) as exc:
        cap._kind("invalid")
    assert str(exc.value) == "not a Stortinget listing: 'invalid'"
    with pytest.raises(CrosswalkError) as exc:
        cap.avd1_archives([])
    assert str(exc.value) == "the publicData catalogue lists no Lovtidend avd. I archive"
    for laws, commit, message in [
        ([], COMMIT, "not a list of Lovdata law ids: empty"),
        (["a", "b", "c", "d"], COMMIT, "not a list of Lovdata law ids: ['a', 'b', 'c']"),
        ([LAW], "bad", "lovverk_commit must be a 40-hex commit id"),
    ]:
        with pytest.raises(CrosswalkError) as exc:
            cap.checked_scope(laws, commit)
        assert str(exc.value) == message
    assert cap.avd1_archives([_entry("lovtidend-avd1-2026.tar.bz2", 1)])


def test_law_list_accepts_exact_size_limit(tmp_path: Path) -> None:
    path = tmp_path / "laws"
    path.write_bytes((LAW + "\n#").encode() + b"x" * (1_000_000 - len(LAW) - 2))
    assert read_law_ids(path) == [LAW]


@pytest.mark.parametrize("nested", [False, True])
def test_initial_manifest_creation_and_timestamps(tmp_path: Path, nested: bool) -> None:
    path = tmp_path / "new" / "nested" if nested else tmp_path
    manifest = cap._new_manifest(path, [LAW], COMMIT)
    assert manifest["lovdata"] == []
    assert datetime.fromisoformat(manifest["started_at"]).tzinfo == UTC
    assert path.is_dir()


def test_capture_default_clients_and_pacer_leave_complete_manifest(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    _full_capture(httpx_mock)
    manifest = cap.capture(tmp_path, [LAW], COMMIT)
    assert cache.load_manifest(tmp_path) == manifest
    assert datetime.fromisoformat(manifest["finished_at"]).tzinfo == UTC
    assert datetime.fromisoformat(manifest["started_at"]) <= datetime.fromisoformat(
        manifest["finished_at"]
    )
    assert (tmp_path / "progress.log").read_text().count("ok ") == 4
    assert "progress.log" in {path.name for path in tmp_path.iterdir()}
    for archive in manifest["lovdata"]:
        assert datetime.fromisoformat(archive["retrieved_at"]).tzinfo == UTC


def test_archive_existing_parent_and_missing_ancestors(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    body = tar_bytes({})
    name = "lovtidend-avd1-2026.tar.bz2"
    parent = tmp_path / "lovdata" / sha256(body).hexdigest()
    parent.mkdir(parents=True)
    httpx_mock.add_response(url=f"{API}/get/{name}", content=body)
    with cap.LovdataClient() as client:
        record = cap.capture_archive(client, _entry(name, len(body)), tmp_path)
    assert (tmp_path / record["path"]).read_bytes() == body
    assert datetime.fromisoformat(record["retrieved_at"]).tzinfo == UTC


def test_capture_impl_uses_supplied_clients(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = tar_bytes({})
    _catalogue(httpx_mock, {"lovtidend-avd1-2026.tar.bz2": body})
    laws = tmp_path / "laws"
    laws.write_text(LAW)
    with cap.LovdataClient(user_agent="supplied-capture-test") as client:
        assert (
            capture_impl(tmp_path / "run", laws, COMMIT, cap.Sources(lovdata=client))
            == "captured 1 archives and 0 Stortinget responses (0 failed)"
        )
    assert all(
        request.headers["User-Agent"] == "supplied-capture-test"
        for request in httpx_mock.get_requests()
    )


def test_utf8_files_ignore_process_locale(tmp_path: Path) -> None:

    script = """
import locale
from pathlib import Path
from lovspor.crosswalk.cache import write_manifest, load_manifest, MANIFEST
from lovspor.crosswalk.capture import Pacer
locale.setlocale(locale.LC_CTYPE, "C")
path = Path(__import__('sys').argv[1])
manifest = {"title": "bl\\u00e5b\\u00e6r"}
write_manifest(path, manifest)
assert (path / MANIFEST).read_bytes().decode("utf-8").find(manifest["title"]) >= 0
assert load_manifest(path) == manifest
Pacer(path / "log").note(manifest["title"])
Pacer(path / "log").note(manifest["title"])
assert (path / "log").read_bytes().decode("utf-8").endswith(manifest["title"] + "\\n")
lines = (path / "log").read_bytes().decode("utf-8").splitlines()
assert [line.split(" ", 1)[1] for line in lines] == [manifest["title"]] * 2
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", script, str(tmp_path)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("stop", ["publikasjoner", "sak"])
def test_interrupted_capture_keeps_last_completed_stage(
    tmp_path: Path, httpx_mock: HTTPXMock, stop: str
) -> None:

    body = tar_bytes({"lti/2022/nl-20220617-063.xml": document(LAW, "Lovvedtak 85 (2021-2022)")})
    _catalogue(httpx_mock, {"lovtidend-avd1-2001-2025.tar.bz2": body})

    def respond(request: httpx.Request) -> httpx.Response:
        manifest = cache.load_manifest(tmp_path)
        assert len(manifest["lovdata"]) == 1
        if request.url.path.endswith("/" + stop) and (
            stop != "sak" or request.url.params["sakid"] == "89017"
        ):
            if stop == "sak":
                assert len(manifest["stortinget"]) == 4
            raise RuntimeError("interrupted")
        if request.url.path.endswith("/sak"):
            return httpx.Response(
                200, content=sak(89016, "2021-2022"), headers={"Content-Type": "text/xml"}
            )
        content = (
            saker((89016, "Lovvedtak 85 (2021-2022)"), (89017, "Lovvedtak 85 (2021-2022)"))
            if request.url.path.endswith("/saker")
            else b"<unused/>"
        )
        return httpx.Response(200, content=content, headers={"Content-Type": "text/xml"})

    httpx_mock.add_callback(respond, url=re.compile(EKSPORT + "/.*"), is_reusable=True)
    with pytest.raises(RuntimeError, match="interrupted"):
        cap.capture(tmp_path, [LAW], COMMIT, cap.Sources(pacer=_pacer(tmp_path)))
    manifest = cache.load_manifest(tmp_path)
    assert "finished_at" not in manifest


def test_case_storage_conflict_has_useful_error(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = sak(7, "2021-2022")
    directory = tmp_path / "stortinget" / "discovery"
    directory.mkdir(parents=True)
    # archive_sak's digest filename must never overwrite a torn object.

    snapshot = CaseSnapshot(
        7, f"{EKSPORT}/sak?sakid=7", datetime.now(UTC), "text/xml", sha256(body).hexdigest(), body
    )
    path = archive_sak(snapshot, directory)
    path.write_bytes(b"torn")
    httpx_mock.add_response(
        url=snapshot.source_url, content=body, headers={"Content-Type": "text/xml"}
    )
    with (
        httpx.Client() as client,
        pytest.raises(CrosswalkError, match="sak 7 could not be stored once:"),
    ):
        cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
    assert path.read_bytes() == b"torn"


@pytest.mark.parametrize("reader", ["archive", "laws"])
def test_oversized_files_are_not_loaded_whole(tmp_path: Path, reader: str) -> None:
    path = tmp_path / "large"
    body = b"x" * (8 << 20)
    path.write_bytes(body)
    pin = {"path": path.name, "sha256": sha256(body).hexdigest()}
    tracemalloc.start()
    try:
        if reader == "archive":
            assert cache.verified_archive(tmp_path, pin) == path.resolve()
        else:
            with pytest.raises(CrosswalkError, match="law list too large"):
                read_law_ids(path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # Generous headroom for buffers; loading the entire rejected input exceeds it.
    assert peak < 5 << 20


def test_default_capture_client_refuses_redirects(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = tar_bytes({"lti/2022/nl-20220617-063.xml": document(LAW, "Lovvedtak 85 (2021-2022)")})
    _catalogue(httpx_mock, {"lovtidend-avd1-2001-2025.tar.bz2": body})
    httpx_mock.add_response(
        url=re.compile(EKSPORT + "/.*"),
        status_code=301,
        headers={"Location": "https://unexpected.example/"},
        is_reusable=True,
    )
    manifest = cap.capture(tmp_path, [LAW], COMMIT, cap.Sources(pacer=_pacer(tmp_path)))
    assert [entry["http_status"] for entry in manifest["stortinget"]] == [301, 301, 301]
    assert all("path" not in entry for entry in manifest["stortinget"])
    assert cache.load_manifest(tmp_path) == manifest
    assert all(request.url.host != "unexpected.example" for request in httpx_mock.get_requests())


@pytest.mark.parametrize("response_delay,expected_status", [(6.0, 200), (60.5, None)])
def test_default_capture_client_network_timeout(
    tmp_path: Path, httpx_mock: HTTPXMock, response_delay: float, expected_status: int | None
) -> None:
    body = tar_bytes({"lti/2022/nl-20220617-063.xml": document(LAW, "Lovvedtak 85 (2021-2022)")})
    _catalogue(httpx_mock, {"lovtidend-avd1-2001-2025.tar.bz2": body})

    def delayed_response(request: httpx.Request) -> httpx.Response:
        # pytest-httpx replaces HTTP transport: model a server whose first byte
        # arrives after the request's read deadline, without a wall-clock wait.
        deadline = request.extensions["timeout"]["read"]
        if deadline is not None and response_delay > deadline:
            raise httpx.ReadTimeout("server did not respond before read deadline", request=request)
        content = (
            saker()
            if request.url.path.endswith("/saker")
            else b'<publikasjoner_oversikt xmlns="http://data.stortinget.no"><publikasjoner_liste/></publikasjoner_oversikt>'
        )
        return httpx.Response(200, content=content, headers={"Content-Type": "text/xml"})

    httpx_mock.add_callback(delayed_response, url=re.compile(EKSPORT + "/.*"), is_reusable=True)
    manifest = cap.capture(tmp_path, [LAW], COMMIT, cap.Sources(pacer=_pacer(tmp_path)))
    assert [entry["http_status"] for entry in manifest["stortinget"]] == [expected_status] * 3
    assert all(
        (entry["error"] is None) == (expected_status == 200) for entry in manifest["stortinget"]
    )


@pytest.mark.parametrize("size", [999_999, 1_000_000, 1_000_001, 1_000_002])
def test_law_list_read_bound_preserves_acceptance(tmp_path: Path, size: int) -> None:
    path = tmp_path / "laws-boundary"
    path.write_bytes((LAW + "\n#").encode() + b"x" * (size - len(LAW) - 2))
    if size > 1_000_000:
        with pytest.raises(CrosswalkError, match="law list too large"):
            read_law_ids(path)
    else:
        assert read_law_ids(path) == [LAW]


@pytest.mark.parametrize("header", [{}, {"lovvedtak": []}, {"besl_o": []}])
def test_missing_optional_decision_lists_mean_no_decision(header: dict) -> None:
    assert cache.law_edge(header) == {
        "status": "unresolved",
        "kind": "none",
        "reason": "header cites no decision",
    }


def test_archive_capture_creates_fresh_nested_cache(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    root = tmp_path / "missing" / "nested" / "cache"
    body = tar_bytes({})
    name = "lovtidend-avd1-2026.tar.bz2"
    httpx_mock.add_response(url=f"{API}/get/{name}", content=body)
    with cap.LovdataClient() as client:
        record = cap.capture_archive(client, _entry(name, len(body)), root)
    assert (root / record["path"]).read_bytes() == body
    assert list((root / "lovdata" / ".staging").iterdir()) == []


@pytest.mark.parametrize("failures", [0, 1, 2, 3])
def test_retry_budget_on_real_http_responses(
    tmp_path: Path, httpx_mock: HTTPXMock, failures: int
) -> None:
    url = f"{EKSPORT}/sak?sakid=7"
    for _ in range(failures):
        httpx_mock.add_response(url=url, status_code=503)
    if failures < 3:
        httpx_mock.add_response(
            url=url, content=sak(7, "2021-2022"), headers={"Content-Type": "text/xml"}
        )
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
    assert record["http_status"] == (503 if failures == 3 else 200)
    assert len(httpx_mock.get_requests()) == min(failures + 1, 3)
    if failures == 3:
        assert "path" not in record
    else:
        assert (tmp_path / record["path"]).read_bytes() == sak(7, "2021-2022")
