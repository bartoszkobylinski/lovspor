"""Label-free Lovvedtak → sak discovery (#610).

All XML here is SYNTHETIC, written in the shape of the real data.stortinget.no
responses (`saker_oversikt`, `publikasjoner_oversikt`, `detaljert_sak`). HTTP is
mocked with pytest-httpx only; the discovery rules run for real.
"""

from datetime import UTC, datetime
from hashlib import sha256

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import LovsporError, NetworkError, ParseError, StortingetStatusError
from lovspor.stortinget_case_source import CaseMetadata, CaseSnapshot, LawDecision
from lovspor.stortinget_discovery import (
    DISCOVERY_PARSER_VERSION,
    ListingSnapshot,
    Observation,
    SakHint,
    candidate_saks,
    case_identity,
    classify_drift,
    confirms,
    discover,
    export_id,
    fetch_case,
    fetch_listing,
    henvisning_numbers,
    parse_case,
    parse_lovvedtak_listing,
    parse_sak_listing,
    previous_session,
    typed_export_ids,
)

NS = 'xmlns="http://data.stortinget.no"'
SAKER_URL = "https://data.stortinget.no/eksport/saker?sesjonid=2021-2022"
PUBS_URL = (
    "https://data.stortinget.no/eksport/publikasjoner?publikasjontype=lovvedtak&sesjonid=2021-2022"
)
DECISION = LawDecision("2021-2022", 85)
XML = {"Content-Type": "text/xml; charset=utf-8"}


def _ref(text: str, export: str | None, kind: str = "lovvedtak") -> str:
    export_tag = "" if export is None else f"<eksport_id>{export}</eksport_id>"
    return (
        f"<publikasjon_referanse>{export_tag}<lenke_tekst>{text}</lenke_tekst>"
        f"<type>{kind}</type></publikasjon_referanse>"
    )


def _case(sak_id: int, *refs: str, session: str = "2021-2022", stamp: str = "T1") -> CaseSnapshot:
    body = (
        f'<?xml version="1.0" encoding="utf-8"?><detaljert_sak {NS}>'
        f"<respons_dato_tid>{stamp}</respons_dato_tid><id>{sak_id}</id>"
        f"<sak_sesjon>{session}</sak_sesjon>"
        "<henvisning>Lovvedtak 85 (2021-2022)</henvisning>"
        f"<publikasjon_referanse_liste>{''.join(refs)}</publikasjon_referanse_liste>"
        "</detaljert_sak>"
    ).encode()
    url = f"https://data.stortinget.no/eksport/sak?sakid={sak_id}"
    observed = datetime(2026, 11, 2, tzinfo=UTC)
    return CaseSnapshot(sak_id, url, observed, "text/xml", sha256(body).hexdigest(), body)


GOOD = _ref("Lovvedtak 85 (2021-2022)", "vedtak-202122-085")


def _listing(body: bytes, kind: str = "saker") -> ListingSnapshot:
    observed = datetime(2026, 11, 2, tzinfo=UTC)
    return ListingSnapshot(
        kind, "2021-2022", SAKER_URL, observed, "text/xml", sha256(body).hexdigest(), body
    )  # type: ignore[arg-type]


def _saker(*items: tuple[str, str]) -> bytes:
    saker = "".join(
        f"<sak><id>{sak_id}</id><henvisning>{text}</henvisning></sak>" for sak_id, text in items
    )
    return f"<saker_oversikt {NS}><saker_liste>{saker}</saker_liste></saker_oversikt>".encode()


# --- henvisning parsing --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prop. 81 L (2021-2022), Innst. 386 L (2021-2022), Lovvedtak 85 (2021-2022)", {85}),
        ("Innst. 4 L (2021-2022), Lovvedtak 11-20 (2021-2022)", set(range(11, 21))),
        ("Lovvedtak 108 \u2013 114 (2021\u20132022)", set(range(108, 115))),
        ("Lovvedtak 3, 5 og 7-8 (2021-2022)", {3, 5, 7, 8}),
        ("Lovvedtak 11 og 12 (2021-2022), Lovvedtak 40 (2021-2022)", {11, 12, 40}),
        ("lovvedtak 2 (2021 - 2022)", {2}),
        ("Lovvedtak 110 (2021-2022)", {110}),
        ("Lovvedtak 15-17 (2022-2023)", set()),
        ("Lovvedtak 20-11 (2021-2022)", set()),
        ("Lovvedtak 0 (2021-2022)", set()),
        ("Lovvedtak 1-401 (2021-2022)", set(range(1, 402))),
        ("Lovvedtak 1-402 (2021-2022)", set()),
        ("Lovvedtak 12345 (2021-2022)", set()),
        ("Lovvedtak 85", set()),
        ("Innst. 85 L (2021-2022)", set()),
        ("Besl. O. nr. 85 (2021-2022)", set()),
        ("Endringslovvedtak 85 (2021-2022)", set()),
        ("", set()),
    ],
)
def test_henvisning_numbers(text: str, expected: set[int]) -> None:
    assert henvisning_numbers(text, "2021-2022") == frozenset(expected)


def test_number_eleven_never_matches_one_hundred_and_ten() -> None:
    hints = [SakHint(1, "Lovvedtak 110 (2021-2022)"), SakHint(2, "Lovvedtak 11 (2021-2022)")]
    assert candidate_saks(hints, LawDecision("2021-2022", 11)) == (2,)
    assert candidate_saks(hints, LawDecision("2021-2022", 110)) == (1,)
    assert candidate_saks(hints, LawDecision("2021-2022", 1)) == ()


def test_candidates_are_sorted_unique_and_session_bound() -> None:
    hints = [
        SakHint(9, "Lovvedtak 11-20 (2021-2022)"),
        SakHint(3, "Lovvedtak 15 (2021-2022)"),
        SakHint(3, "Lovvedtak 15 (2021-2022)"),
        SakHint(4, "Lovvedtak 15 (2022-2023)"),
    ]
    assert candidate_saks(hints, LawDecision("2021-2022", 15)) == (3, 9)
    assert candidate_saks(hints, LawDecision("2022-2023", 15)) == (4,)


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (LawDecision("2021-2022", 85), "vedtak-202122-085"),
        (LawDecision("2025-2026", 3), "vedtak-202526-003"),
        (LawDecision("1999-2000", 120), "vedtak-199900-120"),
        (LawDecision("2024-2025", 1234), "vedtak-202425-1234"),
    ],
)
def test_export_id(decision: LawDecision, expected: str) -> None:
    assert export_id(decision) == expected


@pytest.mark.parametrize("session", ["2021-2023", "2021/2022", "21-22", "2021-2022 "])
def test_session_must_be_two_consecutive_years(session: str) -> None:
    with pytest.raises(ParseError, match="Not a Stortinget session"):
        previous_session(session)


def test_previous_session() -> None:
    assert previous_session("2025-2026") == "2024-2025"
    assert previous_session("2000-2001") == "1999-2000"


# --- discovery outcome rules -------------------------------------------------------


def test_exactly_one_typed_confirmation_is_discovered() -> None:
    other = _case(2, _ref("Lovvedtak 84 (2021-2022)", "vedtak-202122-084"))
    found = discover(DECISION, [_case(1, GOOD), other])
    assert (found.outcome, found.sak_ids, found.candidates, found.failed) == (
        "discovered",
        (1,),
        (1, 2),
        (),
    )
    assert found.decision == DECISION


def test_no_typed_confirmation_is_not_found() -> None:
    assert discover(DECISION, []).outcome == "not_found"
    henvisning_only = _case(1)
    assert discover(DECISION, [henvisning_only]).outcome == "not_found"


def test_two_typed_confirmations_are_ambiguous_never_picked() -> None:
    found = discover(DECISION, [_case(7, GOOD), _case(3, GOOD)], failed=[9])
    assert (found.outcome, found.sak_ids, found.candidates, found.failed) == (
        "ambiguous",
        (3, 7),
        (3, 7, 9),
        (9,),
    )


@pytest.mark.parametrize("cases", [[], ["one"]])
def test_an_unread_candidate_leaves_discovery_incomplete(cases: list[str]) -> None:
    snapshots = [_case(1, GOOD)] if cases else []
    found = discover(DECISION, snapshots, failed=[5, 5])
    assert (found.outcome, found.failed) == ("incomplete", (5,))


def test_duplicate_snapshots_of_one_case_are_one_confirmation() -> None:
    assert discover(DECISION, [_case(1, GOOD), _case(1, GOOD, stamp="T2")]).sak_ids == (1,)


@pytest.mark.parametrize(
    "reference",
    [
        _ref("Lovvedtak 85 (2021-2022)", None),
        _ref("Lovvedtak 85 (2021-2022)", "vedtak-202122-086"),
        _ref("Lovvedtak 85 (2021-2022)", "vedtak-202122-085", kind="innstilling"),
        _ref("Lovvedtak 86 (2021-2022)", "vedtak-202122-085"),
        _ref("Lovvedtak 85 (2021-2022) og 86", "vedtak-202122-085"),
    ],
)
def test_typed_confirmation_needs_link_text_and_export_id(reference: str) -> None:
    assert not confirms(_case(1, reference), DECISION)


def test_an_untyped_export_id_does_not_confirm_through_another_reference() -> None:
    case = _case(
        1,
        _ref("Lovvedtak 85 (2021-2022)", None),
        _ref("Innst. 386 L (2021-2022)", "vedtak-202122-085", kind="innstilling"),
    )
    assert typed_export_ids(case) == frozenset({""})
    assert not confirms(case, DECISION)


def test_cross_session_case_is_discovered_from_its_own_typed_reference() -> None:
    later = LawDecision("2025-2026", 26)
    case = _case(103533, _ref("Lovvedtak 26 (2025-2026)", "vedtak-202526-026"), session="2024-2025")
    found = discover(later, [case])
    assert (found.outcome, found.sak_ids) == ("discovered", (103533,))


def test_multi_decision_case_still_discovers_each_exact_decision() -> None:
    case = _case(
        102903,
        _ref("Lovvedtak 92 (2024-2025)", "vedtak-202425-092"),
        _ref("Lovvedtak 93 (2024-2025)", "vedtak-202425-093"),
        session="2024-2025",
    )
    for number in (92, 93):
        assert discover(LawDecision("2024-2025", number), [case]).sak_ids == (102903,)
    assert discover(LawDecision("2024-2025", 94), [case]).outcome == "not_found"


def test_invalid_case_is_a_parse_error_in_the_lovspor_family() -> None:
    broken = CaseSnapshot(1, "u", datetime(2026, 11, 2, tzinfo=UTC), "text/xml", "0", b"<x/>")
    with pytest.raises(ParseError, match="^Stortinget sak 1: Unexpected XML root$"):
        confirms(broken, DECISION)
    with pytest.raises(ParseError):
        parse_case(broken)


# --- listings ----------------------------------------------------------------------


def test_sak_listing_reads_ids_and_henvisning_only() -> None:
    body = _saker(("89016", "Lovvedtak 85 (2021-2022)"), ("1", ""))
    assert parse_sak_listing(_listing(body)) == (
        SakHint(89016, "Lovvedtak 85 (2021-2022)"),
        SakHint(1, ""),
    )


@pytest.mark.parametrize("sak_id", ["", "x1", "\u0661"])
def test_sak_listing_refuses_a_non_numeric_id(sak_id: str) -> None:
    with pytest.raises(ParseError, match="Invalid sak id"):
        parse_sak_listing(_listing(_saker((sak_id, "Lovvedtak 85 (2021-2022)"))))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"<saker_oversikt", "Invalid Stortinget listing XML"),
        (f"<other {NS}/>".encode(), "Unexpected Stortinget listing root"),
        (b"<saker_oversikt/>", "Unexpected Stortinget listing root"),
        (b'<!DOCTYPE x [<!ENTITY a "b">]><saker_oversikt/>', "Forbidden XML DTD"),
        (
            '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE x [<!ENTITY a "b">]>'
            f"<saker_oversikt {NS}/>".encode("utf-16"),
            "Forbidden XML DTD",
        ),
    ],
)
def test_listing_refusals(body: bytes, message: str) -> None:
    with pytest.raises(ParseError, match=message):
        parse_sak_listing(_listing(body))


def test_lovvedtak_listing_keeps_only_lovvedtak_ids() -> None:
    body = (
        f"<publikasjoner_oversikt {NS}><publikasjoner_liste>"
        "<publikasjon><id>vedtak-202122-085</id><type>lovvedtak</type></publikasjon>"
        "<publikasjon><id>beso-200809-087</id><type>lovvedtak</type></publikasjon>"
        "<publikasjon><id>inns-202122-386</id><type>innstilling</type></publikasjon>"
        "</publikasjoner_liste></publikasjoner_oversikt>"
    ).encode()
    assert parse_lovvedtak_listing(_listing(body, "publikasjoner")) == {
        "vedtak-202122-085",
        "beso-200809-087",
    }
    with pytest.raises(ParseError, match="root"):
        parse_lovvedtak_listing(_listing(_saker(), "publikasjoner"))


def test_fetch_listing_records_exact_bytes(httpx_mock: HTTPXMock) -> None:
    body = _saker(("89016", "Lovvedtak 85 (2021-2022)"))
    httpx_mock.add_response(url=SAKER_URL, content=body, headers=XML)
    with httpx.Client() as client:
        snapshot = fetch_listing(client, "saker", "2021-2022")
    assert (snapshot.kind, snapshot.session_id, snapshot.source_url) == (
        "saker",
        "2021-2022",
        SAKER_URL,
    )
    assert (snapshot.body, snapshot.source_sha256) == (body, sha256(body).hexdigest())
    assert snapshot.content_type == "text/xml"
    assert snapshot.observed_at.tzinfo is UTC


def test_fetch_publication_listing_asks_for_lovvedtak(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=PUBS_URL, content=b"<x/>", headers={"Content-Type": "application/xml"}
    )
    with httpx.Client() as client:
        assert fetch_listing(client, "publikasjoner", "2021-2022").source_url == PUBS_URL


@pytest.mark.parametrize("status", [429, 404, 500, 302])
def test_listing_refusal_is_a_recorded_status_not_an_empty_listing(
    httpx_mock: HTTPXMock, status: int
) -> None:
    httpx_mock.add_response(url=SAKER_URL, status_code=status, headers={"Location": "/x"})
    with httpx.Client() as client, pytest.raises(StortingetStatusError) as raised:
        fetch_listing(client, "saker", "2021-2022")
    assert (raised.value.status_code, raised.value.url) == (status, SAKER_URL)
    assert isinstance(raised.value, NetworkError)


def test_listing_html_is_refused(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SAKER_URL, content=b"<html/>", headers={"Content-Type": "text/html"}
    )
    with httpx.Client() as client, pytest.raises(ParseError, match="Expected an XML listing"):
        fetch_listing(client, "saker", "2021-2022")


def test_listing_over_the_byte_limit_is_refused(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SAKER_URL, content=b"x" * 20_000_001, headers=XML)
    with httpx.Client() as client, pytest.raises(ParseError, match="byte limit"):
        fetch_listing(client, "saker", "2021-2022")


def test_listing_at_the_byte_limit_is_read(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SAKER_URL, content=b"x" * 20_000_000, headers=XML)
    with httpx.Client() as client:
        assert len(fetch_listing(client, "saker", "2021-2022").body) == 20_000_000


def test_listing_transport_failure_is_a_network_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ReadTimeout("slow"), url=SAKER_URL)
    with (
        httpx.Client() as client,
        pytest.raises(NetworkError, match="saker 2021-2022: ReadTimeout"),
    ):
        fetch_listing(client, "saker", "2021-2022")


def test_listing_refuses_a_malformed_session_before_network(httpx_mock: HTTPXMock) -> None:
    with httpx.Client() as client, pytest.raises(ParseError):
        fetch_listing(client, "saker", "2021")
    assert httpx_mock.get_requests() == []


# --- case fetch failures are recorded as LovsporError --------------------------------


CASE_URL = "https://data.stortinget.no/eksport/sak?sakid=89016"


def test_fetch_case_returns_the_snapshot(httpx_mock: HTTPXMock) -> None:
    body = _case(89016, GOOD).body
    httpx_mock.add_response(url=CASE_URL, content=body, headers=XML)
    with httpx.Client() as client:
        snapshot = fetch_case(client, 89016)
    assert (snapshot.sak_id, snapshot.body) == (89016, body)


@pytest.mark.parametrize("status", [429, 404, 503])
def test_case_http_refusal_is_recorded_with_its_status(httpx_mock: HTTPXMock, status: int) -> None:
    httpx_mock.add_response(url=CASE_URL, status_code=status)
    with httpx.Client() as client, pytest.raises(StortingetStatusError) as raised:
        fetch_case(client, 89016)
    assert raised.value.status_code == status


def test_case_transport_and_shape_failures_stay_in_the_family(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"), url=CASE_URL)
    with httpx.Client() as client, pytest.raises(NetworkError, match="sak 89016: ConnectError"):
        fetch_case(client, 89016)
    httpx_mock.add_response(url=CASE_URL, content=b"<html/>", headers={"Content-Type": "text/html"})
    with httpx.Client() as client, pytest.raises(ParseError, match="^Stortinget sak 89016: "):
        fetch_case(client, 89016)
    with httpx.Client() as client, pytest.raises(LovsporError):
        fetch_case(client, 0)


# --- source drift ------------------------------------------------------------------


def test_same_identity_with_a_new_digest_is_drift_in_bytes_only() -> None:
    first, second = _case(1, GOOD, stamp="T1"), _case(1, GOOD, stamp="T2")
    assert first.source_sha256 != second.source_sha256
    before = Observation(first.source_sha256, case_identity(parse_case(first)))
    after = Observation(second.source_sha256, case_identity(parse_case(second)))
    assert classify_drift(before, after) == "drift_in_bytes_only"
    assert classify_drift(before, before) == "identical"


def test_a_changed_parsed_identity_is_flagged() -> None:
    first = _case(1, GOOD)
    second = _case(1, GOOD, _ref("Lovvedtak 86 (2021-2022)", "vedtak-202122-086"))
    before = Observation(first.source_sha256, case_identity(parse_case(first)))
    after = Observation(second.source_sha256, case_identity(parse_case(second)))
    assert classify_drift(before, after) == "identity_changed"
    assert classify_drift(Observation("a", ("x",)), Observation("a", ("y",))) == "identity_changed"


def test_case_identity_is_canonical() -> None:
    case = CaseMetadata(
        103533, "2024-2025", (LawDecision("2025-2026", 26), LawDecision("2025-2026", 27)), "0"
    )
    assert case_identity(case) == (
        "sak:103533",
        "sesjon:2024-2025",
        "lovvedtak:2025-2026:26",
        "lovvedtak:2025-2026:27",
    )


def test_discovery_parser_is_versioned() -> None:
    assert DISCOVERY_PARSER_VERSION == "lovvedtak-discovery-1"
