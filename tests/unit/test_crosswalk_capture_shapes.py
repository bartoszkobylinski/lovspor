"""Exact shapes of what capture records and the cache reader returns (#610, P2).

The study and the build read these dicts by key, so each test pins a whole value,
not one field of it. Bodies are SYNTHETIC; HTTP is mocked with pytest-httpx only.
"""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.crosswalk import cache
from lovspor.crosswalk import capture as cap
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.errors import NetworkError, StortingetStatusError
from lovspor.legislative_chain import LovtidendMember
from lovspor.sources.lovdata import LovdataArchive, LovdataClient
from lovspor.stortinget_case_source import LawDecision
from tests.unit.crosswalk_support import document, sak, saker, tar_bytes

LAW = "LOV-2022-06-17-63"
MEMBER = "lti/2022/nl-20220617-063.xml"
API = "https://api.lovdata.no/v1/publicData"
EKSPORT = "https://data.stortinget.no/eksport"
STAMP = "2026-11-02T10:00:00+00:00"


class _Clock:
    def __init__(self) -> None:
        self.now, self.slept = 0.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _pacer(tmp_path: Path, clock: _Clock | None = None) -> cap.Pacer:
    clock = clock or _Clock()
    return cap.Pacer(tmp_path / "log", gap=0.7, clock=clock, sleep=clock.sleep)


def _entry(kind: str, key: object, path: str, body: bytes) -> dict:
    return {
        "stage": "discovery",
        "kind": kind,
        "key": key,
        "path": path,
        "sha256": sha256(body).hexdigest(),
        "error": None,
        "source_url": f"{EKSPORT}/{kind}?k={key}",
        "retrieved_at": STAMP,
        "content_type": "text/xml",
    }


# --- law edge --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "edge"),
    [
        (
            {"lovvedtak": [{"session_id": "2021-2022", "number": 85}], "besl_o": []},
            {
                "status": "attested",
                "kind": "lovvedtak",
                "decision": {"session_id": "2021-2022", "number": 85},
            },
        ),
        (
            {"lovvedtak": [{"session_id": "a", "number": 1}, {"session_id": "a", "number": 2}]},
            {"status": "ambiguous", "kind": "several", "reason": "header cites several"},
        ),
        (
            {"lovvedtak": [], "besl_o": [{"session_id": "2008-2009", "number": 98}]},
            {
                "status": "unsupported",
                "kind": "besl_o",
                "reason": "header cites Besl. O.; the reader types Lovvedtak only",
            },
        ),
        (
            {"lovvedtak": [], "besl_o": []},
            {"status": "unresolved", "kind": "none", "reason": "header cites no decision"},
        ),
        ({}, {"status": "unresolved", "kind": "none", "reason": "header cites no decision"}),
        (
            {"error": "ParseError: x", "lovvedtak": [{"session_id": "a", "number": 1}]},
            {"status": "unresolved", "kind": "unreadable", "reason": "ParseError: x"},
        ),
    ],
)
def test_law_edge_is_exact(header: dict, edge: dict) -> None:
    assert cache.law_edge(header) == edge


def _member(misc: str = "Lovvedtak 85 (2021-2022)") -> LovtidendMember:
    body = document(LAW, misc)
    return LovtidendMember(LAW, MEMBER, body, sha256(body).hexdigest())


def test_law_header_is_exact() -> None:
    assert cache.law_header(_member()) == {
        "lovvedtak": [{"session_id": "2021-2022", "number": 85}],
        "besl_o": [],
    }
    odelsting = cache.law_header(_member("Besl. O. nr. 98 (2008-2009)"))
    assert odelsting == {"lovvedtak": [], "besl_o": [{"session_id": "2008-2009", "number": 98}]}
    bad = LovtidendMember(LAW, MEMBER, b"<not xml", "0" * 64)
    assert cache.law_header(bad)["error"].startswith("ParseError: ")


_ARCHIVE = {
    "filename": "lovtidend-avd1-2001-2025.tar.bz2",
    "source_url": f"{API}/get/lovtidend-avd1-2001-2025.tar.bz2",
    "sha256": "a" * 64,
    "retrieved_at": STAMP,
    "last_modified": "2026-10-01T00:00:00+00:00",
}


def _match(member: LovtidendMember) -> dict:
    return {
        "archive": "lovtidend-avd1-2001-2025.tar.bz2",
        "member": MEMBER,
        "sha256": member.sha256,
        "archive_source_url": f"{API}/get/lovtidend-avd1-2001-2025.tar.bz2",
        "archive_sha256": "a" * 64,
        "archive_retrieved_at": STAMP,
        "archive_last_modified": "2026-10-01T00:00:00+00:00",
    }


def test_law_record_of_one_member_is_exact() -> None:
    member = _member()
    assert cache.law_record(LAW, [(_ARCHIVE, member)]) == {
        "amending_lov_id": LAW,
        "law_year": 2022,
        "lovdata_matches": [_match(member)],
        "parser_versions": {
            "lovtidend_identity": "lovtidend-identity-1",
            "lovtidend_besl_o": "besl-o-header-1",
        },
        "law_identity": {"status": "attested", "reason": None},
        "header": {"lovvedtak": [{"session_id": "2021-2022", "number": 85}], "besl_o": []},
        "law_decision": {
            "status": "attested",
            "kind": "lovvedtak",
            "decision": {"session_id": "2021-2022", "number": 85},
        },
    }


@pytest.mark.parametrize(
    ("count", "status", "reason"),
    [(0, "unresolved", "no member in either archive"), (2, "ambiguous", "several members")],
)
def test_law_record_without_exactly_one_member_is_exact(
    count: int, status: str, reason: str
) -> None:
    member = _member()
    record = cache.law_record(LAW, [(_ARCHIVE, member)] * count)
    assert record["law_identity"] == {"status": status, "reason": reason}
    assert record["law_decision"] == {"status": status, "kind": "not_read", "reason": reason}
    assert record["lovdata_matches"] == [_match(member)] * count
    assert "header" not in record


def test_unreadable_member_leaves_identity_unresolved() -> None:
    bad = LovtidendMember(LAW, MEMBER, b"<not xml", "0" * 64)
    record = cache.law_record(LAW, [(_ARCHIVE, bad)])
    assert record["law_identity"]["status"] == "unresolved"
    assert record["law_identity"]["reason"].startswith("ParseError: ")
    assert record["law_decision"]["kind"] == "unreadable"


def test_cited_decisions_are_unique_sorted_and_include_ambiguous_headers() -> None:
    laws = [
        {"header": {"lovvedtak": [{"session_id": "2024-2025", "number": 41}]}},
        {"header": {"lovvedtak": [{"session_id": "2021-2022", "number": 9}]}},
        {"header": {"lovvedtak": [{"session_id": "2021-2022", "number": 85}]}},
        {
            "header": {
                "lovvedtak": [
                    {"session_id": "2024-2025", "number": 41},
                    {"session_id": "2021-2022", "number": 10},
                ]
            }
        },
        {},
    ]
    assert cache.cited_decisions(laws) == [
        LawDecision("2021-2022", 9),
        LawDecision("2021-2022", 10),
        LawDecision("2021-2022", 85),
        LawDecision("2024-2025", 41),
    ]


# --- snapshots from the cache -----------------------------------------------------------


def test_listing_snapshot_is_exact(tmp_path: Path) -> None:
    body = saker((89016, "Lovvedtak 85 (2021-2022)"))
    (tmp_path / "s.xml").write_bytes(body)
    entry = _entry("saker", "2021-2022", "s.xml", body)
    snapshot = cache.listing_snapshot(tmp_path, {"stortinget": [entry]}, "saker", "2021-2022")
    assert snapshot is not None
    assert (snapshot.kind, snapshot.session_id, snapshot.source_url) == (
        "saker",
        "2021-2022",
        entry["source_url"],
    )
    assert snapshot.observed_at == datetime(2026, 11, 2, 10, tzinfo=UTC)
    assert (snapshot.content_type, snapshot.source_sha256, snapshot.body) == (
        "text/xml",
        sha256(body).hexdigest(),
        body,
    )


def test_case_snapshot_is_exact(tmp_path: Path) -> None:
    body = sak(89016, "2021-2022", ("2021-2022", 85))
    (tmp_path / "c.xml").write_bytes(body)
    entry = _entry("sak", 89016, "c.xml", body)
    snapshot, record = cache.case_snapshot(tmp_path, {"stortinget": [entry]}, "discovery", 89016)
    assert record == entry
    assert snapshot is not None
    assert (snapshot.sak_id, snapshot.source_url, snapshot.content_type) == (
        89016,
        entry["source_url"],
        "text/xml",
    )
    assert (snapshot.source_sha256, snapshot.body) == (sha256(body).hexdigest(), body)
    assert snapshot.observed_at == datetime(2026, 11, 2, 10, tzinfo=UTC)


def test_manifest_bytes_are_utf8_pretty_sorted(tmp_path: Path) -> None:
    cache.write_manifest(tmp_path, {"b": "ø", "a": 1})
    assert (tmp_path / cache.MANIFEST).read_bytes() == '{\n  "a": 1,\n  "b": "ø"\n}\n'.encode()
    assert cache.load_manifest(tmp_path) == {"a": 1, "b": "ø"}


def test_archive_digest_covers_every_byte(tmp_path: Path) -> None:
    body = b"x" * (3 << 20) + b"tail"
    (tmp_path / "a").write_bytes(body)
    pin = {"path": "a", "sha256": sha256(body).hexdigest()}
    assert cache.verified_archive(tmp_path, pin) == (tmp_path / "a").resolve()
    (tmp_path / "a").write_bytes(body[:-1] + b"X")
    with pytest.raises(CrosswalkError, match="do not match the manifest: a"):
        cache.verified_archive(tmp_path, pin)


# --- capture records ----------------------------------------------------------------


def test_pacer_line_format_and_first_wait(tmp_path: Path) -> None:
    clock = _Clock()
    clock.now = 50.0
    pacer = _pacer(tmp_path, clock)
    pacer.wait()
    pacer.wait()
    assert clock.slept == [pytest.approx(0.7)]
    pacer.note("hello")
    stamp, line = (tmp_path / "log").read_text(encoding="utf-8").rstrip("\n").split(" ", 1)
    assert line == "hello"
    assert datetime.fromisoformat(stamp).tzinfo is not None


def test_attempt_notes_each_retry_and_pauses(tmp_path: Path) -> None:
    clock = _Clock()
    calls = iter([NetworkError("reset"), StortingetStatusError(429, "u"), "ok"])

    def request() -> str:
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    assert cap.attempt(_pacer(tmp_path, clock), request) == "ok"
    assert cap.REQUEST_GAP_SECONDS * 4 in clock.slept
    assert cap.RATE_LIMIT_PAUSE_SECONDS in clock.slept
    lines = (tmp_path / "log").read_text(encoding="utf-8").splitlines()
    assert [line.split(" ", 1)[1] for line in lines] == [
        f"retry after NetworkError: reset; pausing {cap.REQUEST_GAP_SECONDS * 4}s",
        f"retry after StortingetStatusError: Stortinget HTTP 429: u; pausing "
        f"{cap.RATE_LIMIT_PAUSE_SECONDS}s",
    ]


def test_failure_record_is_exact() -> None:
    record = cap._failure({"stage": "s", "kind": "k", "key": 1}, StortingetStatusError(404, "u"))
    assert set(record) == {"stage", "kind", "key", "error", "http_status", "retrieved_at"}
    assert (record["error"], record["http_status"]) == (
        "StortingetStatusError: Stortinget HTTP 404: u",
        404,
    )
    assert datetime.fromisoformat(record["retrieved_at"]).utcoffset() is not None


def test_listing_record_is_exact(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = saker()
    url = f"{EKSPORT}/saker?sesjonid=2020-2021"
    httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})
    with httpx.Client() as client:
        record = cap.capture_listing(client, tmp_path, _pacer(tmp_path), ("saker", "2020-2021"))
    digest = sha256(body).hexdigest()
    retrieved = record.pop("retrieved_at")
    assert record == {
        "stage": "discovery",
        "kind": "saker",
        "key": "2020-2021",
        "source_url": url,
        "content_type": "text/xml",
        "sha256": digest,
        "size_bytes": len(body),
        "path": f"stortinget/listings/saker-2020-2021-{digest}.xml",
        "error": None,
        "http_status": 200,
    }
    assert datetime.fromisoformat(retrieved).utcoffset() is not None
    assert f"ok saker 2020-2021 {digest[:12]}" in (tmp_path / "log").read_text(encoding="utf-8")


def test_case_record_is_exact(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = sak(89016, "2021-2022", ("2021-2022", 85))
    url = f"{EKSPORT}/sak?sakid=89016"
    httpx_mock.add_response(url=url, content=body, headers={"Content-Type": "text/xml"})
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("verification", 89016))
    digest = sha256(body).hexdigest()
    record.pop("retrieved_at")
    assert record == {
        "stage": "verification",
        "kind": "sak",
        "key": 89016,
        "source_url": url,
        "content_type": "text/xml",
        "sha256": digest,
        "size_bytes": len(body),
        "path": f"stortinget/verification/sak-89016-{digest}.xml",
        "error": None,
        "http_status": 200,
    }
    assert f"ok verification sak 89016 {digest[:12]}" in (tmp_path / "log").read_text(
        encoding="utf-8"
    )


def test_refused_case_is_recorded_and_logged(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{EKSPORT}/sak?sakid=7", status_code=404)
    with httpx.Client() as client:
        record = cap.capture_sak(client, tmp_path, _pacer(tmp_path), ("discovery", 7))
    assert (record["stage"], record["kind"], record["key"], record["http_status"]) == (
        "discovery",
        "sak",
        7,
        404,
    )
    assert "FAIL discovery sak 7: " in (tmp_path / "log").read_text(encoding="utf-8")


def test_archive_record_is_exact(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    name = "lovtidend-avd1-2026.tar.bz2"
    body = tar_bytes({MEMBER: document(LAW, "")})
    httpx_mock.add_response(url=f"{API}/get/{name}", content=body)
    entry = LovdataArchive.model_validate(
        {
            "filename": name,
            "description": "d",
            "sizeBytes": len(body),
            "lastModified": "2026-10-31T02:31:00Z",
        }
    )
    with LovdataClient() as client:
        record = cap.capture_archive(client, entry, tmp_path)
    digest = sha256(body).hexdigest()
    retrieved = record.pop("retrieved_at")
    assert record == {
        "filename": name,
        "source_url": f"{API}/get/{name}",
        "last_modified": "2026-10-31T02:31:00+00:00",
        "catalogue_size_bytes": len(body),
        "path": f"lovdata/{digest}/{name}",
        "sha256": digest,
        "size_bytes": len(body),
    }
    assert datetime.fromisoformat(retrieved).utcoffset() is not None


def test_new_manifest_is_exact(tmp_path: Path) -> None:
    manifest = cap._new_manifest(tmp_path / "run", [LAW], "a" * 40)
    started = manifest.pop("started_at")
    assert manifest == {
        "scope": {"law_ids": [LAW], "lovverk_commit": "a" * 40},
        "lovdata": [],
        "stortinget": [],
    }
    assert datetime.fromisoformat(started).utcoffset() is not None
    assert (tmp_path / "run").is_dir()


@pytest.mark.parametrize(
    ("laws", "commit", "message"),
    [
        ([], "a" * 40, "not a list of Lovdata law ids: empty"),
        (["x", "y", "z", "w"], "a" * 40, "not a list of Lovdata law ids: ['w', 'x', 'y']"),
        ([LAW], "a" * 39, "lovverk_commit must be a 40-hex commit id"),
    ],
)
def test_scope_refusals_are_exact(laws: list[str], commit: str, message: str) -> None:
    with pytest.raises(CrosswalkError) as caught:
        cap.checked_scope(laws, commit)
    assert str(caught.value) == message


def test_avd1_refusal_messages_are_exact() -> None:
    def entry(name: str, size: int) -> LovdataArchive:
        return LovdataArchive.model_validate(
            {"filename": name, "description": "d", "sizeBytes": size, "lastModified": STAMP}
        )

    with pytest.raises(CrosswalkError) as caught:
        cap.avd1_archives([entry("lovtidend-avd1-2026.tar.bz2", 0)])
    assert str(caught.value) == "lovtidend-avd1-2026.tar.bz2: catalogue size 0 refused"
    with pytest.raises(CrosswalkError) as caught:
        cap.avd1_archives([])
    assert str(caught.value) == "the publicData catalogue lists no Lovtidend avd. I archive"
