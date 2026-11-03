"""Offline reading of a crosswalk capture (#610, P2)."""

from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk import cache
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.stortinget_case_source import LawDecision
from tests.unit.crosswalk_support import document, saker, tar_bytes

LAW = "LOV-2022-06-17-63"


def _entry(stage: str, kind: str, key: object, path: str, body: bytes) -> dict:
    return {
        "stage": stage,
        "kind": kind,
        "key": key,
        "path": path,
        "sha256": sha256(body).hexdigest(),
        "error": None,
        "source_url": f"https://data.stortinget.no/eksport/{kind}?x={key}",
        "retrieved_at": "2026-11-02T10:00:00+00:00",
        "content_type": "text/xml",
    }


def test_manifest_round_trip_and_unreadable(tmp_path: Path) -> None:
    manifest = {"lovdata": [], "stortinget": [], "scope": {"law_ids": [LAW]}}
    cache.write_manifest(tmp_path, manifest)
    assert cache.load_manifest(tmp_path) == manifest
    assert (tmp_path / cache.MANIFEST).read_text().endswith("}\n")
    with pytest.raises(CrosswalkError, match="unreadable"):
        cache.load_manifest(tmp_path / "absent")
    (tmp_path / cache.MANIFEST).write_text("{not json")
    with pytest.raises(CrosswalkError, match="unreadable"):
        cache.load_manifest(tmp_path)


def test_latest_is_the_last_attempt_of_that_object() -> None:
    first = {"stage": "discovery", "kind": "sak", "key": 1, "n": 1}
    second = {"stage": "discovery", "kind": "sak", "key": 1, "n": 2}
    other = {"stage": "verification", "kind": "sak", "key": 1, "n": 3}
    manifest = {"stortinget": [first, second, other]}
    assert cache.latest(manifest, "discovery", "sak", 1) == second
    assert cache.latest(manifest, "discovery", "sak", 2) is None


def test_cached_body_verifies_digest_and_stays_inside_the_cache(tmp_path: Path) -> None:
    (tmp_path / "a.xml").write_bytes(b"body")
    record = _entry("discovery", "sak", 1, "a.xml", b"body")
    assert cache.cached_body(tmp_path, record) == b"body"
    assert cache.cached_body(tmp_path, None) is None
    assert cache.cached_body(tmp_path, {**record, "error": "refused"}) is None
    with pytest.raises(CrosswalkError, match="do not match"):
        cache.cached_body(tmp_path, {**record, "sha256": "0" * 64})
    with pytest.raises(CrosswalkError, match="leaves the cache"):
        cache.cached_body(tmp_path / "sub", {**record, "path": "../a.xml"})


def test_unparseable_listing_is_not_read_and_keeps_its_error(tmp_path: Path) -> None:
    body = b"<not-a-listing/>"
    (tmp_path / "s.xml").write_bytes(body)
    manifest = {"stortinget": [_entry("discovery", "saker", "2021-2022", "s.xml", body)]}
    assert cache.parsed_listing(tmp_path, manifest, "saker", "2021-2022") is None
    error = cache.listing_parse_error(tmp_path, manifest, "saker", "2021-2022")
    assert error is not None and error.startswith("ParseError")
    assert cache.listing_parse_error(tmp_path, manifest, "saker", "2020-2021") is None


def test_hints_need_both_sessions(tmp_path: Path) -> None:
    body = saker((89016, "Lovvedtak 85 (2021-2022)"))
    (tmp_path / "s.xml").write_bytes(body)
    one = _entry("discovery", "saker", "2021-2022", "s.xml", body)
    decision = LawDecision("2021-2022", 85)
    assert cache.hints(tmp_path, {"stortinget": [one]}, decision) is None
    two = {**one, "key": "2020-2021"}
    found = cache.hints(tmp_path, {"stortinget": [one, two]}, decision)
    assert found is not None and len(found) == 2
    assert cache.discovery_candidates(tmp_path, {"stortinget": [one, two]}, [decision]) == [89016]


def test_law_records_read_each_pinned_archive_once(tmp_path: Path) -> None:
    member = "lti/2022/nl-20220617-063.xml"
    archive = tmp_path / "lovdata/x/lovtidend-avd1-2001-2025.tar.bz2"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(tar_bytes({member: document(LAW, "Lovvedtak 85 (2021-2022)")}))
    pins = [
        {
            "filename": archive.name,
            "path": "lovdata/x/lovtidend-avd1-2001-2025.tar.bz2",
            "sha256": sha256(archive.read_bytes()).hexdigest(),
        }
    ]
    records = cache.law_records(tmp_path, pins, [LAW, "LOV-2022-06-17-64", LAW])
    assert [r["amending_lov_id"] for r in records] == [LAW, "LOV-2022-06-17-64"]
    assert records[0]["law_decision"]["decision"] == {"session_id": "2021-2022", "number": 85}
    assert records[0]["parser_versions"] == cache.LAW_PARSER_VERSIONS
    assert records[1]["law_identity"] == {
        "status": "unresolved",
        "reason": "no member in either archive",
    }
    with pytest.raises(CrosswalkError, match="leaves the cache"):
        cache.law_records(tmp_path, [{**pins[0], "path": "../../etc/x"}], [LAW])


def test_an_archive_that_no_longer_matches_its_pin_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "a.tar.bz2"
    archive.write_bytes(tar_bytes({}))
    pin = {"filename": "a", "path": "a.tar.bz2", "sha256": sha256(archive.read_bytes()).hexdigest()}
    assert cache.verified_archive(tmp_path, pin) == archive.resolve()
    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        cache.verified_archive(tmp_path, {**pin, "sha256": "0" * 64})
    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        cache.verified_archive(tmp_path, {"filename": "a", "path": "a.tar.bz2"})


@pytest.mark.parametrize("reader", [cache.cached_body, cache.verified_archive])
def test_cache_readers_refuse_symlinks_leaving_capture(tmp_path: Path, reader) -> None:
    outside = tmp_path / "outside.xml"
    outside.write_bytes(b"pinned bytes")
    capture = tmp_path / "capture"
    capture.mkdir()
    (capture / "linked.xml").symlink_to(outside)
    record = _entry("discovery", "sak", 1, "linked.xml", outside.read_bytes())

    with pytest.raises(CrosswalkError, match="leaves the cache"):
        reader(capture, record)
    assert outside.read_bytes() == b"pinned bytes"


def test_archive_digest_includes_bytes_after_first_chunk(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar.bz2"
    body = b"a" * (1 << 20) + b"tail"
    archive.write_bytes(body)
    record = {"path": archive.name, "sha256": sha256(body).hexdigest()}
    assert cache.verified_archive(tmp_path, record) == archive.resolve()

    with archive.open("r+b") as stream:
        stream.seek(1 << 20)
        stream.write(b"fail")
    with pytest.raises(CrosswalkError, match="do not match the manifest"):
        cache.verified_archive(tmp_path, record)
