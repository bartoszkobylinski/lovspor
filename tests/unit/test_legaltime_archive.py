"""Evidence collection over a synthetic archive: indexing, member selection, pins."""

import io
import json
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.legaltime.archive import (
    INDEX_VERSION,
    collect,
    dump_json,
    file_sha256,
    index_archive,
    load_oracle,
    pins,
    read_members,
)
from lovspor.legaltime.errors import LegalTimeInputError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
LAYOUT = {
    "lti/2019/nl-20190614-099.xml": "enacting.xml",
    "lti/2020/nl-20201218-999.xml": "amending.xml",
    "lti/2020/sf-20200228-9999.xml": "resolution-full.xml",
    "lti/2021/sf-20210528-9998.xml": "resolution-partial.xml",
}


def make_archive(
    path: Path, layout: dict[str, str] | None = None, extra: dict[str, bytes] | None = None
) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        members = {
            name: (FIXTURES / fixture).read_bytes() for name, fixture in (layout or LAYOUT).items()
        }
        members.update(extra or {})
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    return make_archive(tmp_path / "lovtidend-avd1-2001-2025.tar.bz2")


def test_index_lists_every_member_header_and_is_cached_by_digest(archive: Path) -> None:
    index = index_archive(archive)
    assert index.archive == archive.name
    assert index.sha256 == file_sha256(archive)
    assert index.index_version == INDEX_VERSION
    assert {m.name: m.refid for m in index.members} == {
        "lti/2019/nl-20190614-099.xml": "lov/2019-06-14-99",
        "lti/2020/nl-20201218-999.xml": "lov/2020-12-18-999",
        "lti/2020/sf-20200228-9999.xml": "forskrift/2020-02-28-9999",
        "lti/2021/sf-20210528-9998.xml": "forskrift/2021-05-28-9998",
    }
    amending = next(m for m in index.members if m.refid == "lov/2020-12-18-999")
    assert amending.changes_to_documents == ("lov/2019-06-14-99", "lov/1967-02-10")
    assert amending.published_at == "2020-12-18T12:10:00"
    assert index.horizon == "2021-05-31T16:10:00"
    cache = archive.parent / f"{archive.name}.{INDEX_VERSION}.json"
    assert cache.exists()
    assert index_archive(archive) == index


def test_stale_or_foreign_index_cache_is_rebuilt(archive: Path, tmp_path: Path) -> None:
    cache_dir = tmp_path / "idx"
    first = index_archive(archive, cache_dir)
    cache = cache_dir / f"{archive.name}.{INDEX_VERSION}.json"
    stale = json.loads(cache.read_text())
    stale["sha256"] = "0" * 64
    cache.write_text(json.dumps(stale))
    assert index_archive(archive, cache_dir) == first
    cache.write_text("not json")
    assert index_archive(archive, cache_dir) == first


def test_members_without_a_header_are_skipped_not_fatal(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "a.tar.bz2", extra={"lti/2020/junk.xml": b"<html><body/></html>"}
    )
    assert len(index_archive(archive).members) == 4


def test_missing_archive_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(LegalTimeInputError):
        index_archive(tmp_path / "nope.tar.bz2")


def test_collect_finds_baseline_amenders_and_decisions(archive: Path) -> None:
    evidence = collect("lov/2019-06-14-99", [archive])
    assert evidence.baseline.member == "lti/2019/nl-20190614-099.xml"
    assert [a.member for a in evidence.amenders] == ["lti/2020/nl-20201218-999.xml"]
    assert [d.member for d in evidence.decisions] == [
        "lti/2020/sf-20200228-9999.xml",
        "lti/2021/sf-20210528-9998.xml",
    ]
    assert evidence.archives[0].sha256 == file_sha256(archive)


def test_collect_refuses_an_act_without_an_enacting_member(archive: Path) -> None:
    with pytest.raises(LegalTimeInputError, match="no enacting member"):
        collect("lov/1999-01-01-1", [archive])


def test_read_members_refuses_a_missing_name(archive: Path) -> None:
    found = read_members(archive, ["lti/2019/nl-20190614-099.xml"])
    assert found["lti/2019/nl-20190614-099.xml"].header.refid == "lov/2019-06-14-99"
    with pytest.raises(LegalTimeInputError, match="members missing"):
        read_members(archive, ["lti/2019/nl-20190614-099.xml", "lti/2019/nl-00000000-000.xml"])


def test_load_oracle_reads_the_consolidated_member(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "gjeldende-lover.tar.bz2", {"nl/nl-20190614-099.xml": "consolidated.xml"}
    )
    oracle = load_oracle(archive, "lov/2019-06-14-99")
    assert oracle.header.dokid == "NL/lov/2019-06-14-99"
    with pytest.raises(LegalTimeInputError, match="no consolidated member"):
        load_oracle(archive, "lov/2019-06-14-98")


def test_pins_and_json_are_deterministic(archive: Path) -> None:
    pinned = pins([archive])
    assert pinned == [
        {
            "filename": archive.name,
            "size_bytes": archive.stat().st_size,
            "sha256": sha256(archive.read_bytes()).hexdigest(),
        }
    ]
    assert dump_json({"b": 1, "a": [2]}) == '{\n "a": [\n  2\n ],\n "b": 1\n}'


def test_two_enacting_members_for_one_act_are_refused(tmp_path: Path, archive: Path) -> None:
    twin = make_archive(tmp_path / "twin.tar.bz2", {"lti/2019/nl-20190614-099.xml": "enacting.xml"})
    with pytest.raises(LegalTimeInputError, match="several enacting members"):
        collect("lov/2019-06-14-99", [archive, twin])
