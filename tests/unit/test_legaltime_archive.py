"""Evidence collection over a synthetic archive: indexing, member selection, pins."""

import bz2
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


@pytest.mark.parametrize(
    "listed_law,selected",
    [
        ("lov/2019-06-14-99", True),
        ("lov/2020-12-18-999", True),
        ("lov/2019-06-14-98", False),
    ],
)
def test_collect_selects_multi_law_decisions_by_listed_baseline_or_amender(
    tmp_path: Path, listed_law: str, selected: bool
) -> None:
    """Phase 2: a decision's title can name a different law from its other blocks."""
    foreign = "lov/2010-05-05-7"
    title = "Ikraftsetting av lov 5. mai 2010 nr. 7 om annet"
    body = (
        '<html><body><header><dl><dd class="refid">forskrift/2022-01-01-8</dd>'
        f'<dd class="title">{title}</dd><dd class="changesToDocuments"><ul>'
        f"<li>{foreign}</li><li>{listed_law}</li></ul></dd></dl></header>"
        f'<main><article class="legalP">{title}.</article>'
        '<article class="legalP">Loven trer i kraft 1. januar 2022.</article>'
        "</main></body></html>"
    ).encode()
    name = "lti/2022/sf-20220101-008.xml"
    archive = make_archive(tmp_path / "multi-law.tar.bz2", extra={name: body})

    fresh = collect("lov/2019-06-14-99", [archive])
    decisions = {doc.member: doc for doc in fresh.decisions}
    assert (name in decisions) is selected
    assert set(decisions) == {
        "lti/2020/sf-20200228-9999.xml",
        "lti/2021/sf-20210528-9998.xml",
    } | ({name} if selected else set())
    if selected:
        assert decisions[name].header.changes_to_documents == (foreign, listed_law)
        assert decisions[name].sha256 == sha256(body).hexdigest()
    assert collect("lov/2019-06-14-99", [archive]) == fresh


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


def test_duplicate_member_names_are_refused_without_writing_an_index(tmp_path: Path) -> None:
    archive = tmp_path / "duplicate.tar.bz2"
    body = (FIXTURES / "enacting.xml").read_bytes()
    with tarfile.open(archive, "w:bz2") as tar:
        for _ in range(2):
            member = tarfile.TarInfo("lti/2019/nl-20190614-099.xml")
            member.size = len(body)
            tar.addfile(member, io.BytesIO(body))
    with pytest.raises(LegalTimeInputError, match="duplicate member names"):
        index_archive(archive)
    assert not (tmp_path / f"{archive.name}.{INDEX_VERSION}.json").exists()


def test_skipped_members_survive_the_index_cache_round_trip(tmp_path: Path) -> None:
    archive = make_archive(
        tmp_path / "skipped.tar.bz2",
        extra={
            "lti/2020/z.xml": b"<html>",
            "lti/2020/a.xml": b"<html><body/></html>",
        },
    )
    fresh = index_archive(archive)
    assert fresh.skipped == ("lti/2020/a.xml", "lti/2020/z.xml")
    assert len(fresh.members) == 4
    cache = tmp_path / f"{archive.name}.{INDEX_VERSION}.json"
    assert json.loads(cache.read_text(encoding="utf-8"))["skipped"] == list(fresh.skipped)
    assert index_archive(archive) == fresh


def test_matching_digest_does_not_reuse_an_obsolete_index_version(archive: Path) -> None:
    original = index_archive(archive)
    cache = archive.parent / f"{archive.name}.{INDEX_VERSION}.json"
    obsolete = original.model_dump(mode="json")
    obsolete.update(index_version="legaltime-index-1", members=[], skipped=["stale.xml"])
    cache.write_text(json.dumps(obsolete), encoding="utf-8")
    rebuilt = index_archive(archive)
    assert rebuilt == original
    assert json.loads(cache.read_text(encoding="utf-8"))["index_version"] == INDEX_VERSION


@pytest.mark.parametrize("reverse", [False, True])
def test_collect_keeps_the_baseline_when_another_archive_reuses_its_member_path(
    tmp_path: Path, reverse: bool
) -> None:
    """Member paths are archive-local: an amender under the same path is never the act."""
    name = "lti/2019/nl-20190614-099.xml"
    enacting = make_archive(tmp_path / "enacting.tar.bz2", {name: "enacting.xml"})
    amending = make_archive(tmp_path / "amending.tar.bz2", {name: "amending.xml"})
    paths = [amending, enacting] if reverse else [enacting, amending]
    evidence = collect("lov/2019-06-14-99", paths)
    assert evidence.baseline.header.refid == "lov/2019-06-14-99"
    assert evidence.baseline.sha256 == sha256((FIXTURES / "enacting.xml").read_bytes()).hexdigest()
    assert [doc.header.refid for doc in evidence.amenders] == ["lov/2020-12-18-999"]


@pytest.mark.parametrize("name", ["../escaped.xml", "/escaped.xml", r"lti\..\escaped.xml"])
def test_index_refuses_unsafe_member_names_without_caching(tmp_path: Path, name: str) -> None:
    archive = make_archive(tmp_path / "unsafe.tar.bz2", extra={name: b"<html/>"})
    with pytest.raises(LegalTimeInputError, match="unreadable archive unsafe.tar.bz2"):
        index_archive(archive, tmp_path / "index")
    assert not (tmp_path / "index").exists()
    assert not (tmp_path / "escaped.xml").exists()


def test_index_ignores_links_and_never_extracts_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("legal-time indexing must read members without extracting them")

    monkeypatch.setattr(tarfile.TarFile, "extract", forbidden)
    monkeypatch.setattr(tarfile.TarFile, "extractall", forbidden)
    archive = tmp_path / "links.tar.bz2"
    body = (FIXTURES / "enacting.xml").read_bytes()
    with tarfile.open(archive, "w:bz2") as tar:
        regular = tarfile.TarInfo("lti/enacting.xml")
        regular.size = len(body)
        tar.addfile(regular, io.BytesIO(body))
        for name, kind in [
            ("lti/symlink.xml", tarfile.SYMTYPE),
            ("lti/hardlink.xml", tarfile.LNKTYPE),
        ]:
            link = tarfile.TarInfo(name)
            link.type = kind
            link.linkname = "lti/enacting.xml"
            tar.addfile(link)
    index = index_archive(archive)
    assert [member.name for member in index.members] == ["lti/enacting.xml"]
    assert index.skipped == ()
    assert not (tmp_path / "lti").exists()


def test_empty_archive_has_no_horizon_and_cannot_supply_a_baseline(tmp_path: Path) -> None:
    archive = tmp_path / "empty.tar.bz2"
    with tarfile.open(archive, "w:bz2"):
        pass
    index = index_archive(archive)
    assert index.members == ()
    assert index.skipped == ()
    assert index.horizon is None
    assert index_archive(archive) == index
    with pytest.raises(LegalTimeInputError, match="no enacting member"):
        collect("lov/2019-06-14-99", [archive])


@pytest.mark.parametrize("malformed", [False, True])
def test_replacing_archive_bytes_invalidates_cached_member_records(
    archive: Path, malformed: bool
) -> None:
    """archive.py promises an index cached under the archive's own SHA256."""
    original = index_archive(archive)
    name = "lti/2019/nl-20190614-099.xml"
    replacement = (
        b"<html>"
        if malformed
        else (FIXTURES / "enacting.xml")
        .read_bytes()
        .replace(b"</main>", b"<!-- updated announcement --> </main>")
    )
    make_archive(archive, extra={name: replacement})

    rebuilt = index_archive(archive)
    assert rebuilt.sha256 == sha256(archive.read_bytes()).hexdigest()
    assert rebuilt.sha256 != original.sha256
    assert tuple(m for m in rebuilt.members if m.name != name) == tuple(
        m for m in original.members if m.name != name
    )
    if malformed:
        assert name not in {m.name for m in rebuilt.members}
        assert rebuilt.skipped == (name,)
    else:
        member = next(m for m in rebuilt.members if m.name == name)
        assert member.sha256 == sha256(replacement).hexdigest()
        assert member.sha256 != next(m for m in original.members if m.name == name).sha256
        assert rebuilt.skipped == ()
    cache = archive.parent / f"{archive.name}.{INDEX_VERSION}.json"
    assert json.loads(cache.read_text(encoding="utf-8")) == rebuilt.model_dump(mode="json")
    assert index_archive(archive) == rebuilt


def test_incomplete_member_is_an_input_error_without_a_partial_index(tmp_path: Path) -> None:
    """An interrupted member must not become a digest-pinned evidence index."""
    body = (FIXTURES / "enacting.xml").read_bytes()
    member = tarfile.TarInfo("lti/enacting.xml")
    member.size = len(body)
    archive = tmp_path / "partial.tar.bz2"
    archive.write_bytes(bz2.compress(member.tobuf() + body[: len(body) // 2]))
    cache_dir = tmp_path / "index"

    with pytest.raises(LegalTimeInputError, match="unreadable archive partial.tar.bz2"):
        index_archive(archive, cache_dir)

    assert not cache_dir.exists()
    assert not (tmp_path / "lti").exists()


@pytest.mark.parametrize("declared_size", [64 * 1024 * 1024 + 1, 2**40])
def test_oversized_member_is_refused_before_opening_or_caching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, declared_size: int
) -> None:
    """The new indexer must preserve the extractor's decompression-bomb guard."""
    member = tarfile.TarInfo("lti/oversized.xml")
    member.size = declared_size
    archive = tmp_path / "oversized.tar.bz2"
    # Only a header is needed: rejection must precede any attempt to read the body.
    archive.write_bytes(bz2.compress(member.tobuf()))
    cache_dir = tmp_path / "index"

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("oversized member body must never be opened")

    monkeypatch.setattr(tarfile.TarFile, "extractfile", forbidden)
    with pytest.raises(LegalTimeInputError, match="unreadable archive oversized.tar.bz2") as error:
        index_archive(archive, cache_dir)

    assert "exceeding the 67108864-byte per-member cap" in str(error.value)
    assert str(declared_size) in str(error.value)
    assert not cache_dir.exists()
