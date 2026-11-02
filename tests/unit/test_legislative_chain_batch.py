"""One-pass Lovtidend reader and the Besl. O. header reader (#610 study of the 52).

Every Lovdata document here is synthetic in the real header shape: raw Lovdata
XML is never committed.
"""

import io
import tarfile
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.errors import ExtractionError, ParseError
from lovspor.legislative_chain import (
    LovtidendMember,
    cited_odelsting_decisions,
    parse_law_publication,
    read_lovtidend_member,
    read_lovtidend_members,
)
from lovspor.stortinget_case_source import LawDecision


def _document(law_id: str, misc: str) -> bytes:
    slug = law_id.removeprefix("LOV-")
    return (
        '<html lang="no"><head><title>T</title></head><body>'
        '<header class="documentHeader"><dl>'
        f'<dd class="legacyID">{law_id}</dd><dd class="dokid">LTI/lov/{slug}</dd>'
        f'<dd class="miscInformation">{misc}</dd><dd class="refid">lov/{slug}</dd>'
        "</dl></header><main>Lovvedtak 99 (2021-2022)</main></body></html>"
    ).encode()


def _archive(path: Path, members: dict[str, bytes], *, directories: bool = False) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        if directories:
            info = tarfile.TarInfo("lti/2026/nl-20260206-003.xml")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


def _member(law_id: str, misc: str) -> LovtidendMember:
    body = _document(law_id, misc)
    return LovtidendMember(law_id, "lti/x.xml", body, sha256(body).hexdigest())


def test_one_pass_returns_every_requested_act_and_only_those(tmp_path: Path) -> None:
    first = _document("LOV-2026-02-06-3", "Lovvedtak 22 (2025\u20132026).")
    second = _document("LOV-2026-06-23-74", "Lovvedtak 81 (2025\u20132026).")
    archive = _archive(
        tmp_path / "a.tar.bz2",
        {
            "lti/2026/nl-20260206-003.xml": first,
            "lti/2026/nl-20260623-074.xml": second,
            "lti/2026/nl-20260206-030.xml": b"other act",
            "lti/2026/sf-20260206-0003.xml": first,
        },
    )
    found = read_lovtidend_members(
        archive, ["LOV-2026-02-06-3", "LOV-2026-06-23-74", "LOV-2026-01-01-9"]
    )
    assert list(found) == ["LOV-2026-02-06-3", "LOV-2026-06-23-74", "LOV-2026-01-01-9"]
    assert [m.name for m in found["LOV-2026-02-06-3"]] == [
        "lti/2026/nl-20260206-003.xml",
        "lti/2026/sf-20260206-0003.xml",
    ]
    assert found["LOV-2026-06-23-74"] == (
        LovtidendMember(
            "LOV-2026-06-23-74",
            "lti/2026/nl-20260623-074.xml",
            second,
            sha256(second).hexdigest(),
        ),
    )
    assert found["LOV-2026-01-01-9"] == ()
    assert list(tmp_path.iterdir()) == [archive]


def test_batch_agrees_with_the_single_reader(tmp_path: Path) -> None:
    body = _document("LOV-2012-01-20-7", "Lovvedtak 28 (2011\u20132012).")
    archive = _archive(
        tmp_path / "a.tar.bz2", {"lti/2012/nl-20120120-007.xml": body}, directories=True
    )
    (batch,) = read_lovtidend_members(archive, ["LOV-2012-01-20-7"])["LOV-2012-01-20-7"]
    assert batch == read_lovtidend_member(archive, "LOV-2012-01-20-7")


def test_directory_entry_is_never_a_member(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "a.tar.bz2", {}, directories=True)
    assert read_lovtidend_members(archive, ["LOV-2026-02-06-3"]) == {"LOV-2026-02-06-3": ()}


def test_batch_refuses_unreadable_archive(tmp_path: Path) -> None:
    archive = tmp_path / "broken.tar.bz2"
    archive.write_bytes(b"not a bzip2 tar")
    with pytest.raises(ExtractionError, match="^Unreadable Lovtidend archive: broken.tar.bz2$"):
        read_lovtidend_members(archive, ["LOV-2026-02-06-3"])


def test_batch_keeps_the_member_byte_limit(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "a.tar.bz2", {"lti/2026/nl-20260206-003.xml": b"x" * 2_000_001})
    with pytest.raises(ExtractionError, match="exceeds byte limit"):
        read_lovtidend_members(archive, ["LOV-2026-02-06-3"])


def test_batch_refuses_a_malformed_law_id(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "a.tar.bz2", {})
    with pytest.raises(ParseError, match="Not a Lovdata law identity"):
        read_lovtidend_members(archive, ["LOV-2026-02-06-03"])


def test_besl_o_header_is_read_and_lovvedtak_reader_stays_empty() -> None:
    member = _member(
        "LOV-2009-06-19-107",
        "Ot.prp.nr.63 (2008\u20132009), Innst.O.nr.71 (2008\u20132009) og "
        "Besl.O.nr.87 (2008\u20132009). Odels- og lagtingsvedtak hhv. 26. mai og 4. juni 2009.",
    )
    assert cited_odelsting_decisions(member) == (LawDecision("2008-2009", 87),)
    assert parse_law_publication(member).lovvedtak == ()


@pytest.mark.parametrize(
    ("misc", "expected"),
    [
        ("Besl. O. nr. 5 (2005-2006)", (LawDecision("2005-2006", 5),)),
        ("besl.o. nr 12 ( 2006 - 2007 )", (LawDecision("2006-2007", 12),)),
        ("Besl.O.nr.87 (2008-2010)", ()),
        ("Besl.L.nr.4 (2008-2009)", ()),
        ("Lovvedtak 28 (2011-2012)", ()),
        (
            "Besl.O.nr.9 (2008-2009) og Besl.O.nr.3 (2008-2009)",
            (LawDecision("2008-2009", 3), LawDecision("2008-2009", 9)),
        ),
    ],
)
def test_besl_o_citation_shapes(misc: str, expected: tuple[LawDecision, ...]) -> None:
    assert cited_odelsting_decisions(_member("LOV-2009-06-19-107", misc)) == expected


def test_besl_o_reader_keeps_the_identity_check() -> None:
    member = _member("LOV-2009-06-19-107", "Besl.O.nr.87 (2008-2009)")
    wrong = LovtidendMember("LOV-2009-06-19-108", member.name, member.body, member.sha256)
    with pytest.raises(ParseError, match="identity mismatch"):
        cited_odelsting_decisions(wrong)
