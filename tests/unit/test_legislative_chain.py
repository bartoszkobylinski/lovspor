"""E4 chain: real Stortinget bytes, synthetic Lovdata documents.

The sak XML under tests/fixtures/stortinget/ is the real API response. Lovdata
documents here are synthetic but copy the real header shape, because raw Lovdata
XML may never be committed; tests/integration/test_pilot_e4_replay.py runs the
same code over the real archive wherever the gitignored cache exists.
"""

import io
import json
import tarfile
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from lovspor.errors import ExtractionError, ParseError
from lovspor.legislative_chain import (
    CHAIN_PARSER_VERSION,
    LawPublication,
    LovtidendMember,
    _read_member,
    parse_law_publication,
    read_lovtidend_member,
    resolve_case_decision,
    resolve_chain,
    resolve_law_decision,
)
from lovspor.stortinget_case_source import (
    SAK_PARSER_VERSION,
    CaseMetadata,
    CaseSnapshot,
    LawDecision,
    parse_sak,
    resolve_publication,
)

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = json.loads(
    (ROOT / "docs/research/data/pilot-e4-evidence-2026-11-01.json").read_text(encoding="utf-8")
)
SCHEMA = json.loads(
    (ROOT / "docs/research/schemas/legislative-crosswalk-v0.schema.json").read_text("utf-8")
)
LAW = "LOV-2022-06-17-63"
DECISION = LawDecision("2021-2022", 85)
MISC = "Prop.81 L (2021\u20132022), Lovvedtak 85 (2021\u20132022). Fremmet av JD."


def _document(
    law_id: str = LAW, misc: str = MISC, *, body: str = "", refid: str | None = None
) -> bytes:
    slug = law_id.removeprefix("LOV-")
    return (
        '<!DOCTYPE html><html lang="no"><head><title>T</title></head><body>'
        '<header class="documentHeader" id="hode"><dl>'
        f'<dd class="legacyID">{law_id}</dd><dd class="dokid">LTI/lov/{slug}</dd>'
        f'<dd class="miscInformation">{misc}</dd>'
        f'<dd class="refid">{refid or "lov/" + slug}</dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()


def _member(body: bytes, law_id: str = LAW) -> LovtidendMember:
    return LovtidendMember(law_id, "lti/x.xml", body, sha256(body).hexdigest())


def _publication(*decisions: LawDecision) -> LawPublication:
    return LawPublication(LAW, decisions, "0" * 64)


def _real_case(sak_id: int):
    record = next(r for r in EVIDENCE["stortinget"] if r["sak_id"] == sak_id)
    body = (ROOT / "tests/fixtures/stortinget" / f"sak-{sak_id}.xml").read_bytes()
    observed = datetime.fromisoformat(record["retrieved_at"])
    return parse_sak(
        CaseSnapshot(
            sak_id,
            record["source_url"],
            observed,
            record["content_type"],
            sha256(body).hexdigest(),
            body,
        )
    )


def _archive(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return path


@pytest.mark.parametrize("record", EVIDENCE["stortinget"], ids=lambda r: str(r["sak_id"]))
def test_committed_fixture_is_the_captured_response(record: dict) -> None:
    body = (ROOT / "tests/fixtures/stortinget" / f"sak-{record['sak_id']}.xml").read_bytes()
    assert sha256(body).hexdigest() == record["sha256"]
    assert len(body) == record["size_bytes"]
    assert record["source_url"].startswith("https://data.stortinget.no/eksport/sak?sakid=")
    assert record["parser_version"] == SAK_PARSER_VERSION
    case = _real_case(record["sak_id"])
    assert case.session_id == record["sak_sesjon"]
    assert [{"session_id": d.session_id, "number": d.number} for d in case.lovvedtak] == record[
        "typed_lovvedtak"
    ]


def test_real_case_89016_names_exactly_lovvedtak_85() -> None:
    case = _real_case(89016)
    assert case.session_id == "2021-2022"
    assert case.lovvedtak == (DECISION,)
    assert resolve_publication(case, DECISION) == "attested"


def test_real_case_opened_a_session_before_its_decisions_keeps_both() -> None:
    case = _real_case(63056)
    assert case.session_id == "2014-2015"
    assert case.lovvedtak == (LawDecision("2015-2016", 13), LawDecision("2015-2016", 14))


def test_real_case_with_two_decisions_attests_each_act_and_hides_neither() -> None:
    first, second = LawDecision("2015-2016", 13), LawDecision("2015-2016", 14)
    case = _real_case(63056)
    verdict = resolve_chain(_publication(first), case, first)
    assert verdict.status == "attested"
    assert verdict.other_case_decisions == (second,)
    assert resolve_chain(_publication(second), case, second).other_case_decisions == (first,)
    assert resolve_publication(case, first) == "ambiguous"


def test_real_decision_with_two_acts_gives_two_edges_not_one_value() -> None:
    decision = LawDecision("2024-2025", 41)
    case = _real_case(101001)
    verdicts = [
        resolve_chain(LawPublication(law, (decision,), "0" * 64), case, decision)
        for law in ("LOV-2025-04-25-13", "LOV-2025-06-20-67")
    ]
    assert [v.law_id for v in verdicts] == ["LOV-2025-04-25-13", "LOV-2025-06-20-67"]
    assert {v.status for v in verdicts} == {"attested"}


def test_real_odelsting_case_is_unresolved_not_guessed() -> None:
    case = _real_case(43206)
    decision = LawDecision("2008-2009", 87)
    assert case.lovvedtak == ()
    verdict = resolve_chain(_publication(), case, decision)
    assert (verdict.status, verdict.law_edge, verdict.case_edge) == (
        "unresolved",
        "unresolved",
        "unresolved",
    )


def test_real_case_of_another_act_contradicts() -> None:
    verdict = resolve_chain(_publication(DECISION), _real_case(101001), DECISION)
    assert (verdict.status, verdict.law_edge, verdict.case_edge) == (
        "contradicted",
        "attested",
        "contradicted",
    )


def test_real_multi_decision_case_without_the_decision_is_ambiguous() -> None:
    verdict = resolve_chain(_publication(DECISION), _real_case(63056), DECISION)
    assert (verdict.status, verdict.case_edge) == ("ambiguous", "ambiguous")


def test_strong_case_edge_cannot_repair_a_missing_law_citation() -> None:
    verdict = resolve_chain(_publication(), _real_case(89016), DECISION)
    assert (verdict.status, verdict.law_edge, verdict.case_edge) == (
        "unresolved",
        "unresolved",
        "attested",
    )


def test_contradiction_outranks_ambiguity_across_edges() -> None:
    other = LawDecision("2021-2022", 86)
    verdict = resolve_chain(_publication(other), _real_case(63056), DECISION)
    assert (verdict.law_edge, verdict.case_edge, verdict.status) == (
        "contradicted",
        "ambiguous",
        "contradicted",
    )


def test_ambiguity_outranks_unresolved_across_edges() -> None:
    verdict = resolve_chain(_publication(), _real_case(63056), DECISION)
    assert (verdict.law_edge, verdict.case_edge, verdict.status) == (
        "unresolved",
        "ambiguous",
        "ambiguous",
    )


@pytest.mark.parametrize(
    ("law_decisions", "law_status"),
    [
        ((), "unresolved"),
        ((DECISION,), "attested"),
        ((LawDecision("2020-2021", 85),), "contradicted"),
        ((DECISION, LawDecision("2021-2022", 86)), "ambiguous"),
    ],
)
@pytest.mark.parametrize(
    ("case_decisions", "case_status", "outcomes"),
    [
        ((), "unresolved", ("unresolved", "unresolved", "contradicted", "ambiguous")),
        ((DECISION,), "attested", ("unresolved", "attested", "contradicted", "ambiguous")),
        (
            (LawDecision("2020-2021", 85),),
            "contradicted",
            ("contradicted", "contradicted", "contradicted", "contradicted"),
        ),
        (
            (LawDecision("2020-2021", 85), LawDecision("2021-2022", 86)),
            "ambiguous",
            ("ambiguous", "ambiguous", "contradicted", "ambiguous"),
        ),
    ],
)
def test_chain_preserves_both_edges_and_the_worst_verdict(
    law_decisions: tuple[LawDecision, ...],
    law_status: str,
    case_decisions: tuple[LawDecision, ...],
    case_status: str,
    outcomes: tuple[str, ...],
) -> None:
    """The chain's weakest-edge contract holds in either edge direction."""
    case = CaseMetadata(89016, "2021-2022", case_decisions, "0" * 64)
    verdict = resolve_chain(_publication(*law_decisions), case, DECISION)
    expected = outcomes[("unresolved", "attested", "contradicted", "ambiguous").index(law_status)]
    assert (verdict.law_edge, verdict.case_edge, verdict.status) == (
        law_status,
        case_status,
        expected,
    )
    assert (verdict.law_id, verdict.sak_id, verdict.decision) == (LAW, 89016, DECISION)
    assert verdict.other_case_decisions == tuple(d for d in case_decisions if d != DECISION)


def test_case_edge_on_empty_and_single_other_case() -> None:
    assert resolve_case_decision(_real_case(43206), DECISION) == "unresolved"
    assert resolve_case_decision(_real_case(101001), DECISION) == "contradicted"


def test_header_citation_is_read_with_hyphen_or_en_dash() -> None:
    for misc in (MISC, "Lovvedtak 85 (2021-2022)", "lovvedtak  85 ( 2021 \u2013 2022 )"):
        publication = parse_law_publication(_member(_document(misc=misc)))
        assert publication.lovvedtak == (DECISION,)
        assert resolve_law_decision(publication, DECISION) == "attested"


def test_parser_keeps_identity_and_member_digest() -> None:
    body = _document()
    publication = parse_law_publication(_member(body))
    assert publication.law_id == LAW
    assert publication.member_sha256 == sha256(body).hexdigest()


def test_repeated_citation_of_one_decision_is_one_decision() -> None:
    misc = "Lovvedtak 85 (2021\u20132022), jf. Lovvedtak 85 (2021-2022)."
    assert parse_law_publication(_member(_document(misc=misc))).lovvedtak == (DECISION,)


def test_two_cited_decisions_are_sorted_and_ambiguous() -> None:
    misc = (
        "Lovvedtak 9 (2022\u20132023), Lovvedtak 85 (2021\u20132022), Lovvedtak 3 (2021\u20132022)."
    )
    publication = parse_law_publication(_member(_document(misc=misc)))
    assert publication.lovvedtak == (
        LawDecision("2021-2022", 3),
        DECISION,
        LawDecision("2022-2023", 9),
    )
    assert resolve_law_decision(publication, DECISION) == "ambiguous"


@pytest.mark.parametrize(
    "misc",
    [
        "Besl.O.nr.87 (2008\u20132009).",
        "Lovvedtak 85 (2020\u20132022).",
        "Lovvedtak 85.",
        "Prop.85 L (2021\u20132022).",
    ],
)
def test_law_without_an_exact_citation_is_unresolved(misc: str) -> None:
    publication = parse_law_publication(_member(_document(misc=misc)))
    assert publication.lovvedtak == ()
    assert resolve_law_decision(publication, DECISION) == "unresolved"


def test_different_citation_is_contradicted() -> None:
    other = "Lovvedtak 86 (2021\u20132022)."
    publication = parse_law_publication(_member(_document(misc=other)))
    assert resolve_law_decision(publication, DECISION) == "contradicted"


def test_same_number_in_another_session_is_contradicted() -> None:
    other = "Lovvedtak 85 (2020\u20132021)."
    publication = parse_law_publication(_member(_document(misc=other)))
    assert resolve_law_decision(publication, DECISION) == "contradicted"


def test_statute_text_cannot_supply_a_citation() -> None:
    body = _document(misc="", body="<p>Lovvedtak 85 (2021\u20132022)</p>")
    assert parse_law_publication(_member(body)).lovvedtak == ()


def test_duplicated_header_field_counts_as_absent() -> None:
    forged = _document().replace(
        b'<dd class="refid">',
        b'<dd class="miscInformation">Lovvedtak 1 (2021-2022)</dd><dd class="refid">',
    )
    assert parse_law_publication(_member(forged)).lovvedtak == ()


@pytest.mark.parametrize(
    "body",
    [
        _document("LOV-2022-06-17-64"),
        _document(refid="lov/2022-06-17-64"),
        _document().replace(b"LTI/lov/2022-06-17-63", b"LTI/lov/2022-06-17-64"),
        _document().replace(b'class="legacyID"', b'class="other"'),
    ],
)
def test_a_document_of_another_act_is_refused(body: bytes) -> None:
    with pytest.raises(ParseError, match="^Lovdata law identity mismatch: lti/x.xml$"):
        parse_law_publication(_member(body))


def test_malformed_document_is_refused() -> None:
    with pytest.raises(ParseError, match="^Invalid Lovtidend document: lti/x.xml$"):
        parse_law_publication(_member(b"<html><body>"))


@pytest.mark.parametrize("law_id", ["lov/2022-06-17-63", "LOV-2022-06-17-063", "LOV-2022-6-17-63"])
def test_invalid_law_identity_is_refused_before_reading(tmp_path: Path, law_id: str) -> None:
    with pytest.raises(ParseError, match="Not a Lovdata law identity"):
        read_lovtidend_member(tmp_path / "absent.tar.bz2", law_id)


def test_member_is_read_exactly_and_nothing_is_written(tmp_path: Path) -> None:
    body = _document()
    decoy = _document("LOV-2022-06-17-630")
    archive = _archive(
        tmp_path / "a.tar.bz2",
        {"lti/2022/nl-20220617-630.xml": decoy, "lti/2022/nl-20220617-063.xml": body},
    )
    member = read_lovtidend_member(archive, LAW)
    assert member == LovtidendMember(
        LAW, "lti/2022/nl-20220617-063.xml", body, sha256(body).hexdigest()
    )
    assert [p.name for p in tmp_path.iterdir()] == ["a.tar.bz2"]


def test_wider_number_padding_of_a_sf_member_is_found(tmp_path: Path) -> None:
    law = "LOV-2014-04-04-634"
    archive = _archive(tmp_path / "a.tar.bz2", {"lti/2014/sf-20140404-0634.xml": b"x"})
    found = read_lovtidend_member(archive, law)
    assert found is not None
    assert found.name == "lti/2014/sf-20140404-0634.xml"


def test_absent_act_is_none_not_a_guess(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "a.tar.bz2", {"lti/2022/nl-20220617-064.xml": b"x"})
    assert read_lovtidend_member(archive, LAW) is None


def test_directory_named_like_the_member_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.bz2"
    with tarfile.open(path, "w:bz2") as tar:
        info = tarfile.TarInfo("lti/2022/nl-20220617-063.xml")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
    assert read_lovtidend_member(path, LAW) is None


def test_two_members_for_one_act_are_refused(tmp_path: Path) -> None:
    archive = _archive(
        tmp_path / "a.tar.bz2",
        {"lti/2022/nl-20220617-063.xml": b"a", "lti/2023/nl-20220617-063.xml": b"b"},
    )
    with pytest.raises(ExtractionError, match=f"^Several Lovtidend members claim {LAW}$"):
        read_lovtidend_member(archive, LAW)


def test_member_at_the_limit_is_read_and_above_it_refused(tmp_path: Path) -> None:
    name = "lti/2022/nl-20220617-063.xml"
    fits = _archive(tmp_path / "fits.tar.bz2", {name: b"x" * 2_000_000})
    found = read_lovtidend_member(fits, LAW)
    assert found is not None
    assert len(found.body) == 2_000_000
    over = _archive(tmp_path / "over.tar.bz2", {name: b"x" * 2_000_001})
    with pytest.raises(ExtractionError, match=f"^Lovtidend member exceeds byte limit: {name}$"):
        read_lovtidend_member(over, LAW)


def test_member_without_a_readable_stream_is_refused() -> None:
    class _NoStream:
        def extractfile(self, member: tarfile.TarInfo) -> None:
            return None

    member = tarfile.TarInfo("lti/x.xml")
    with pytest.raises(ExtractionError, match="^Unreadable Lovtidend member: lti/x.xml$"):
        _read_member(_NoStream(), member)  # type: ignore[arg-type]


def test_corrupt_archive_is_refused_with_its_name(tmp_path: Path) -> None:
    archive = tmp_path / "broken.tar.bz2"
    archive.write_bytes(b"not an archive")
    with pytest.raises(ExtractionError, match="^Unreadable Lovtidend archive: broken.tar.bz2$"):
        read_lovtidend_member(archive, LAW)


@pytest.mark.parametrize(
    "name",
    [
        "../lti/2022/nl-20220617-063.xml",
        "/lti/2022/nl-20220617-063.xml",
        "lti/2022/../2022/nl-20220617-063.xml",
        "lti/2022/nl-20220617-063.xml/extra",
    ],
)
def test_unsafe_or_inexact_member_names_cannot_supply_an_act(tmp_path: Path, name: str) -> None:
    archive = _archive(tmp_path / "a.tar.bz2", {name: _document()})
    assert read_lovtidend_member(archive, LAW) is None
    assert list(tmp_path.iterdir()) == [archive]


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_link_named_like_an_act_is_not_followed(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "a.tar.bz2"
    with tarfile.open(archive, "w:bz2") as tar:
        target = tarfile.TarInfo("payload.xml")
        body = _document()
        target.size = len(body)
        tar.addfile(target, io.BytesIO(body))
        link = tarfile.TarInfo("lti/2022/nl-20220617-063.xml")
        link.type = kind
        link.linkname = "payload.xml"
        tar.addfile(link)
    assert read_lovtidend_member(archive, LAW) is None
    assert list(tmp_path.iterdir()) == [archive]


def test_empty_archive_has_no_act(tmp_path: Path) -> None:
    archive = _archive(tmp_path / "empty.tar.bz2", {})
    assert read_lovtidend_member(archive, LAW) is None


@pytest.mark.parametrize("external", [False, True], ids=["internal", "external"])
def test_xml_entities_cannot_supply_a_law_decision(tmp_path: Path, external: bool) -> None:
    """AGENTS.md XML safety: entity content must never become source evidence."""
    if external:
        payload = tmp_path / "citation.txt"
        payload.write_text("Lovvedtak 85 (2021-2022)", encoding="utf-8")
        declaration = f'SYSTEM "{payload.as_uri()}"'
    else:
        declaration = '"Lovvedtak 85 (2021-2022)"'
    doctype = f"<!DOCTYPE html [<!ENTITY citation {declaration}>]>"
    body = _document(misc="&citation;").replace(b"<!DOCTYPE html>", doctype.encode())
    publication = parse_law_publication(_member(body))
    assert publication.lovvedtak == ()
    assert resolve_law_decision(publication, DECISION) == "unresolved"


def test_invalid_utf8_document_fails_with_member_context() -> None:
    body = _document().replace(b"<title>T</title>", b"<title>\xff</title>")
    with pytest.raises(ParseError, match="^Invalid Lovtidend document: lti/x.xml$"):
        parse_law_publication(_member(body))


def test_recorded_evidence_validates_against_contract_v0_and_stays_research_only() -> None:
    record = EVIDENCE["contract_v0_record"]
    Draft202012Validator(SCHEMA, format_checker=FormatChecker()).validate(record)
    assert record["release_state"] == "research_only"
    assert record["status"] == "attested"
    assert (record["amending_lov_id"], record["sak_id"]) == (LAW, 89016)
    assert record["decision"] == {"kind": "lovvedtak", "session_id": "2021-2022", "number": 85}


def test_recorded_evidence_carries_provenance_for_every_edge() -> None:
    record = EVIDENCE["contract_v0_record"]
    archive = EVIDENCE["lovdata_archive"]
    assert archive["parser_version"] == CHAIN_PARSER_VERSION
    assert archive["source_url"].startswith("https://api.lovdata.no/v1/publicData/get/")
    for item in record["evidence"]:
        assert item["capture_level"] == "raw_body_captured"
        assert len(item["body_sha256"]) == 64
        assert item["parser_version"] in {CHAIN_PARSER_VERSION, SAK_PARSER_VERSION}
    assert {e["role"]: e["source_url"].split("/")[2] for e in record["evidence"]} == {
        "law_identity": "api.lovdata.no",
        "law_decision_pair": "api.lovdata.no",
        "decision_case": "data.stortinget.no",
    }


def test_recorded_claims_keep_every_failure_outcome() -> None:
    assert {c["label"]: c["status"] for c in EVIDENCE["claims"]} == {
        "primary": "attested",
        "one_case_two_acts": "attested",
        "one_decision_two_acts": "attested",
        "no_lovvedtak_kind": "unresolved",
        "wrong_case": "contradicted",
        "absent_from_multi_decision_case": "ambiguous",
    }
