"""One LOV → Lovvedtak → sak chain from two independent sources (ADR-0017, E4).

The Lovdata half reads an act from a Lovtidend avd. I publicData archive; the
Stortinget half is `parse_sak`. The two meet only on `(session, number)`. Nothing
here scrapes lovdata.no, publishes, or says anything about legal validity.
"""

from __future__ import annotations

import re
import tarfile
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from lxml import etree

from lovspor.errors import ExtractionError, ParseError
from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.stortinget_case_source import CaseMetadata, LawDecision, Status, resolve_publication

CHAIN_PARSER_VERSION = "lovtidend-identity-1"
_MEMBER_LIMIT = 2_000_000
_LAW_ID = re.compile(r"LOV-(\d{4})-(\d{2})-(\d{2})-([1-9]\d*)")
_CITATION = re.compile(r"\bLovvedtak\s+(\d{1,4})\s*\(\s*(\d{4})\s*[-\u2013]\s*(\d{4})\s*\)", re.I)
_BESL_O = re.compile(
    r"\bBesl\.\s*O\.\s*nr\.?\s*(\d{1,4})\s*\(\s*(\d{4})\s*[-\u2013]\s*(\d{4})\s*\)", re.I
)
ODELSTING_PARSER_VERSION = "besl-o-header-1"
_WORST_FIRST: tuple[Status, ...] = ("contradicted", "ambiguous", "unresolved", "attested")


@dataclass(frozen=True)
class LovtidendMember:
    law_id: str
    name: str
    body: bytes
    sha256: str


@dataclass(frozen=True)
class LawPublication:
    law_id: str
    lovvedtak: tuple[LawDecision, ...]
    member_sha256: str


@dataclass(frozen=True)
class ChainVerdict:
    """One act's edge. Other decisions of the case stay visible, never collapsed."""

    status: Status
    law_edge: Status
    case_edge: Status
    law_id: str
    decision: LawDecision
    sak_id: int
    other_case_decisions: tuple[LawDecision, ...]


def _law_parts(law_id: str) -> tuple[str, str, str, str]:
    match = _LAW_ID.fullmatch(law_id)
    if match is None:
        raise ParseError(f"Not a Lovdata law identity: {law_id!r}")
    year, month, day, number = match.groups()
    return year, month, day, number


def _member_pattern(law_id: str) -> re.Pattern[str]:
    # Number padding differs by prefix: nl-20220617-063 but sf-20140404-0634.
    year, month, day, number = _law_parts(law_id)
    return re.compile(rf"lti/\d{{4}}/(?:nl|sf)-{year}{month}{day}-0*{number}\.xml")


def member_names_law(member_name: str, law_id: str) -> bool:
    """True when an archive member path is the Lovtidend document of this act."""
    return _member_pattern(law_id).fullmatch(member_name) is not None


def _read_member(tar: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    if member.size > _MEMBER_LIMIT:
        raise ExtractionError(f"Lovtidend member exceeds byte limit: {member.name}")
    handle = tar.extractfile(member)
    if handle is None:
        raise ExtractionError(f"Unreadable Lovtidend member: {member.name}")
    return handle.read()


def read_lovtidend_member(archive: Path, law_id: str) -> LovtidendMember | None:
    """Stream one act from the archive; nothing is extracted to disk."""
    pattern = _member_pattern(law_id)
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            found = [m for m in tar if m.isfile() and pattern.fullmatch(m.name)]
            if len(found) > 1:
                raise ExtractionError(f"Several Lovtidend members claim {law_id}")
            if not found:
                return None
            body = _read_member(tar, found[0])
    except tarfile.TarError as exc:
        raise ExtractionError(f"Unreadable Lovtidend archive: {archive.name}") from exc
    return LovtidendMember(law_id, found[0].name, body, sha256(body).hexdigest())


def _claimants(member: tarfile.TarInfo, patterns: dict[str, re.Pattern[str]]) -> list[str]:
    if not member.isfile():
        return []
    return [law_id for law_id, pattern in patterns.items() if pattern.fullmatch(member.name)]


def _scan_members(
    archive: Path, patterns: dict[str, re.Pattern[str]]
) -> dict[str, list[tuple[str, bytes]]]:
    found: dict[str, list[tuple[str, bytes]]] = {law_id: [] for law_id in patterns}
    with tarfile.open(archive, "r:bz2") as tar:
        for member in tar:
            for law_id in _claimants(member, patterns):
                found[law_id].append((member.name, _read_member(tar, member)))
    return found


def read_lovtidend_members(
    archive: Path, law_ids: Iterable[str]
) -> dict[str, tuple[LovtidendMember, ...]]:
    """One pass over the archive for many acts; every match is kept and none is chosen."""
    patterns = {law_id: _member_pattern(law_id) for law_id in law_ids}
    try:
        found = _scan_members(archive, patterns)
    except tarfile.TarError as exc:
        raise ExtractionError(f"Unreadable Lovtidend archive: {archive.name}") from exc
    return {
        law_id: tuple(
            LovtidendMember(law_id, name, body, sha256(body).hexdigest())
            for name, body in found[law_id]
        )
        for law_id in patterns
    }


def _header_value(root: etree._Element, name: str) -> str:
    """Only the document's key-info header counts; statute text can cite anything."""
    found = root.findall(f"./body/header/dl/dd[@class='{name}']")
    return "".join(str(text) for text in found[0].itertext()).strip() if len(found) == 1 else ""


def _cited_decisions(text: str, citation: re.Pattern[str] = _CITATION) -> tuple[LawDecision, ...]:
    cited = {
        LawDecision(f"{int(first):04d}-{int(last):04d}", int(number))
        for number, first, last in citation.findall(text)
        if int(last) == int(first) + 1
    }
    return tuple(sorted(cited, key=lambda decision: (decision.session_id, decision.number)))


def parse_law_publication(member: LovtidendMember) -> LawPublication:
    """Refuse a document whose three header identities are not the requested act."""
    decisions = _cited_decisions(_checked_misc_information(member))
    return LawPublication(member.law_id, decisions, member.sha256)


def cited_odelsting_decisions(member: LovtidendMember) -> tuple[LawDecision, ...]:
    """`Besl. O.` citations of the header, read for reporting; no chain resolves them."""
    return _cited_decisions(_checked_misc_information(member), _BESL_O)


def _checked_misc_information(member: LovtidendMember) -> str:
    try:
        root = etree.fromstring(member.body, parser=safe_parser())
    except etree.XMLSyntaxError as exc:
        raise ParseError(f"Invalid Lovtidend document: {member.name}") from exc
    year, month, day, number = _law_parts(member.law_id)
    ref_id = f"lov/{year}-{month}-{day}-{number}"
    identities = (
        _header_value(root, "legacyID"),
        _header_value(root, "dokid"),
        _header_value(root, "refid"),
    )
    if identities != (member.law_id, f"LTI/{ref_id}", ref_id):
        raise ParseError(f"Lovdata law identity mismatch: {member.name}")
    return _header_value(root, "miscInformation")


def resolve_law_decision(publication: LawPublication, expected: LawDecision) -> Status:
    if len(publication.lovvedtak) > 1:
        return "ambiguous"
    if not publication.lovvedtak:
        return "unresolved"
    return "attested" if publication.lovvedtak[0] == expected else "contradicted"


def resolve_case_decision(case: CaseMetadata, expected: LawDecision) -> Status:
    """A case with several decisions still attests each one it names exactly."""
    return "attested" if expected in case.lovvedtak else resolve_publication(case, expected)


def resolve_chain(
    publication: LawPublication, case: CaseMetadata, expected: LawDecision
) -> ChainVerdict:
    """The chain is as strong as its weakest edge; no edge can repair another."""
    law_edge = resolve_law_decision(publication, expected)
    case_edge = resolve_case_decision(case, expected)
    status = next(s for s in _WORST_FIRST if s in {law_edge, case_edge})
    others = tuple(decision for decision in case.lovvedtak if decision != expected)
    return ChainVerdict(
        status, law_edge, case_edge, publication.law_id, expected, case.sak_id, others
    )
