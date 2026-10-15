"""Offline API-contract tests; XML is a synthetic derivative of the official schema."""

import gzip
import json
import os
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from hashlib import sha256

import httpx
import pytest
from pytest_httpx import IteratorStream

from lovspor.stortinget_case_source import (
    CaseSnapshot,
    LawDecision,
    _validate_response,
    archive_sak,
    fetch_sak,
    parse_sak,
    resolve_publication,
)

URL = "https://data.stortinget.no/eksport/sak?sakid=89016"
OBSERVED = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
XML = b"""<?xml version="1.0" encoding="utf-8"?>
<detaljert_sak xmlns="http://data.stortinget.no">
  <id>89016</id><sak_sesjon>2021-2022</sak_sesjon>
  <henvisning>Prop. 81 L (2021-2022), Lovvedtak 85 (2021-2022)</henvisning>
  <publikasjon_referanse_liste>
    <publikasjon_referanse>
      <type>innstilling</type><lenke_tekst>Innst. 386 L (2021-2022)</lenke_tekst>
    </publikasjon_referanse>
    <publikasjon_referanse>
      <type>lovvedtak</type><lenke_tekst>Lovvedtak 85 (2021-2022)</lenke_tekst>
    </publikasjon_referanse>
  </publikasjon_referanse_liste>
</detaljert_sak>"""
DECISION = LawDecision("2021-2022", 85)


def _client(body: bytes = XML, status: int = 200, content_type: str = "application/xml"):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == URL
        return httpx.Response(status, content=body, headers={"Content-Type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _snapshot(body: bytes = XML, sak_id: int = 89016) -> CaseSnapshot:
    with _client(body) as client:
        return fetch_sak(client, sak_id, observed_at=OBSERVED)


def test_capture_provenance_and_bytes_are_exact() -> None:
    capture = _snapshot()
    assert capture.sak_id == 89016
    assert capture.source_url == URL
    assert capture.observed_at == OBSERVED
    assert capture.body == XML
    assert capture.source_sha256 == __import__("hashlib").sha256(XML).hexdigest()


def test_parse_official_namespaced_type_lovvedtak() -> None:
    case = parse_sak(_snapshot())
    assert case.sak_id == 89016
    assert case.session_id == "2021-2022"
    assert case.lovvedtak == (DECISION,)
    assert resolve_publication(case, DECISION) == "attested"


def test_plain_henvisning_is_not_a_verified_typed_publication() -> None:
    body = XML.replace(b"<type>lovvedtak</type>", b"<type>referat</type>")
    case = parse_sak(_snapshot(body))
    assert case.lovvedtak == ()
    assert resolve_publication(case, DECISION) == "unresolved"


def test_unexpected_lovvedtak_is_not_silently_paired() -> None:
    case = parse_sak(_snapshot(XML.replace(b"Lovvedtak 85", b"Lovvedtak 86")))
    assert resolve_publication(case, DECISION) == "contradicted"


def test_multiple_distinct_typed_decisions_are_ambiguous() -> None:
    extra = (
        b"<publikasjon_referanse><type>lovvedtak</type>"
        b"<lenke_tekst>Lovvedtak 86 (2021-2022)</lenke_tekst></publikasjon_referanse>"
    )
    body = XML.replace(b"</publikasjon_referanse_liste>", extra + b"</publikasjon_referanse_liste>")
    assert resolve_publication(parse_sak(_snapshot(body)), DECISION) == "ambiguous"


def test_wrong_document_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="case id mismatch"):
        parse_sak(_snapshot(XML.replace(b"<id>89016</id>", b"<id>89017</id>")))


def test_session_mismatch_between_case_and_publication_is_unresolved() -> None:
    case = parse_sak(_snapshot(XML.replace(b"<sak_sesjon>2021-2022", b"<sak_sesjon>2020-2021")))
    assert case.lovvedtak == ()
    assert resolve_publication(case, DECISION) == "unresolved"


def test_dtd_is_rejected_without_external_resolution() -> None:
    body = b'<!DOCTYPE detaljert_sak [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>' + XML
    with pytest.raises(ValueError):
        parse_sak(_snapshot(body))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-16-le", "utf-32"])
@pytest.mark.parametrize(
    "doctype",
    [
        '<!DOCTYPE detaljert_sak [<!ENTITY marker "expanded">]>',
        '<!DOCTYPE detaljert_sak SYSTEM "http://example.invalid/sak.dtd">',
    ],
)
def test_a_dtd_is_refused_in_any_encoding(encoding: str, doctype: str) -> None:
    declared = "utf-16" if encoding.startswith("utf-16") else encoding
    prolog = f'<?xml version="1.0" encoding="{declared}"?>'
    body = (prolog + doctype + XML.decode().split("?>", 1)[1]).encode(encoding)
    if encoding == "utf-16-le":
        body = b"\xff\xfe" + body
    with pytest.raises(ValueError, match="Forbidden XML DTD"):
        parse_sak(_snapshot(body))


@pytest.mark.parametrize("sak_id", [-1, 0, True, 1.3])
def test_bad_requested_case_id_fails_before_network(sak_id: object) -> None:
    with _client() as client, pytest.raises(ValueError, match="sak_id"):
        fetch_sak(client, sak_id, observed_at=OBSERVED)  # type: ignore[arg-type]


def test_http_redirect_is_not_followed() -> None:
    with _client(status=302) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_sak(client, 89016, observed_at=OBSERVED)


def test_rate_limit_is_not_misinterpreted_as_empty() -> None:
    with _client(status=429) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_sak(client, 89016, observed_at=OBSERVED)


def test_html_response_is_refused() -> None:
    with _client(content_type="text/html") as client, pytest.raises(ValueError, match="XML"):
        fetch_sak(client, 89016, observed_at=OBSERVED)


def test_excessive_body_is_rejected() -> None:
    with _client(body=b"x" * 2_000_001) as client, pytest.raises(ValueError, match="limit"):
        fetch_sak(client, 89016, observed_at=OBSERVED)


def test_archive_preserves_exact_bytes_and_manifest(tmp_path) -> None:
    capture = _snapshot()
    xml_path = archive_sak(capture, tmp_path)
    assert xml_path.read_bytes() == XML
    assert xml_path.name.endswith(".xml")
    assert xml_path.with_suffix(".json").exists()
    assert archive_sak(capture, tmp_path) == xml_path


def test_archive_refuses_existing_corrupted_object(tmp_path) -> None:
    capture = _snapshot()
    path = archive_sak(capture, tmp_path)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="mismatch"):
        archive_sak(capture, tmp_path)


def test_archive_rejects_snapshot_with_forged_digest(tmp_path) -> None:
    capture = _snapshot()
    altered = replace(capture, source_sha256="0" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        archive_sak(altered, tmp_path)


@pytest.mark.parametrize(
    "body, message",
    [
        (b"", "Invalid Stortinget case XML"),
        (XML[:-20], "Invalid Stortinget case XML"),
        (XML.replace(b"<id>89016", b"<id>\xff"), "Invalid Stortinget case XML"),
        (
            XML.replace(b"http://data.stortinget.no", b"http://example.invalid"),
            "Unexpected XML root",
        ),
        (XML.replace(b"<id>89016</id>", b""), "case id mismatch"),
        (XML.replace(b"<sak_sesjon>2021-2022</sak_sesjon>", b""), "session identity"),
    ],
)
def test_invalid_case_xml_fails_with_useful_error(body: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_sak(_snapshot(body))


@pytest.mark.parametrize(
    "reference",
    [b"Lovvedtak 85 (2020-2022)", b"Lovvedtak 85", b"Prop. 85 (2021-2022)"],
)
def test_inexact_typed_reference_does_not_attest(reference: bytes) -> None:
    body = XML.replace(b"Lovvedtak 85 (2021-2022)", reference)
    case = parse_sak(_snapshot(body))
    assert case.lovvedtak == ()
    assert resolve_publication(case, DECISION) == "unresolved"


def test_nested_reference_does_not_establish_a_direct_link() -> None:
    body = XML.replace(b"<publikasjon_referanse_liste>", b"<wrapper><publikasjon_referanse_liste>")
    body = body.replace(
        b"</publikasjon_referanse_liste>", b"</publikasjon_referanse_liste></wrapper>"
    )
    assert parse_sak(_snapshot(body)).lovvedtak == ()


def test_capture_bounds_decoded_gzip_bytes() -> None:
    decoded = XML + b" " * (2_000_000 - len(XML))
    for body, exceeds in [(decoded, False), (decoded + b" ", True)]:

        def handler(request: httpx.Request, body: bytes = body) -> httpx.Response:
            assert str(request.url) == URL
            return httpx.Response(
                200,
                content=gzip.compress(body),
                headers={"Content-Type": "text/xml; charset=utf-8", "Content-Encoding": "gzip"},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            if exceeds:
                with pytest.raises(ValueError, match="byte limit"):
                    fetch_sak(client, 89016, observed_at=OBSERVED)
            else:
                capture = fetch_sak(client, 89016, observed_at=OBSERVED)
                assert capture.body == decoded
                assert capture.source_sha256 == sha256(decoded).hexdigest()
                assert capture.content_type == "text/xml"


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ReadError])
def test_partial_download_propagates_transport_failure(failure: type[httpx.TransportError]) -> None:
    requests = []

    class InterruptedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield XML[:100]
            raise failure("interrupted response")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, stream=InterruptedBody(), headers={"Content-Type": "application/xml"}
        )

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(failure, match="interrupted response"),
    ):
        fetch_sak(client, 89016, observed_at=OBSERVED)
    assert len(requests) == 1


def test_archive_metadata_and_separate_observations_round_trip(tmp_path) -> None:
    capture = _snapshot()
    path = archive_sak(capture, tmp_path)
    later = replace(capture, observed_at=OBSERVED + timedelta(seconds=1))
    assert archive_sak(later, tmp_path) == path
    assert json.loads(path.with_suffix(".json").read_bytes()) == {
        "sak_id": 89016,
        "source_url": URL,
        "sha256": capture.source_sha256,
        "content_type": "application/xml",
    }
    observations = sorted((tmp_path / "observations").glob("*.json"))
    assert [json.loads(p.read_bytes()) for p in observations] == [
        {"object": path.name, "observed_at": time.isoformat()}
        for time in (OBSERVED, later.observed_at)
    ]
    assert list(tmp_path.glob("*.xml")) == [path]


def test_archive_refuses_corrupted_metadata_without_overwriting(tmp_path) -> None:
    capture = _snapshot()
    path = archive_sak(capture, tmp_path)
    metadata = path.with_suffix(".json")
    metadata.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash/content mismatch"):
        archive_sak(capture, tmp_path)
    assert metadata.read_bytes() == b"tampered"
    assert path.read_bytes() == XML


def test_naive_observation_is_rejected_before_fetch_or_archive(tmp_path) -> None:
    naive = OBSERVED.replace(tzinfo=None)
    with _client() as client, pytest.raises(ValueError, match="timezone aware"):
        fetch_sak(client, 89016, observed_at=naive)
    directory = tmp_path / "archive"
    with pytest.raises(ValueError, match="timezone aware"):
        archive_sak(replace(_snapshot(), observed_at=naive), directory)
    assert not directory.exists()


class _NoOffset(tzinfo):
    def utcoffset(self, dt):
        return None


def test_observation_with_timezone_but_no_offset_is_rejected(tmp_path) -> None:
    observed = OBSERVED.replace(tzinfo=_NoOffset())
    with (
        _client() as client,
        pytest.raises(ValueError, match="^observed_at must be timezone aware$"),
    ):
        fetch_sak(client, 89016, observed_at=observed)
    with pytest.raises(ValueError, match="^Stortinget observation time must be timezone aware$"):
        archive_sak(replace(_snapshot(), observed_at=observed), tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_streamed_chunks_are_concatenated_and_cumulatively_bounded(httpx_mock) -> None:
    for chunks, exceeds in [([b"abc", b"def"], False), ([b"x" * 1_000_001] * 2, True)]:
        httpx_mock.add_response(
            url=URL, stream=IteratorStream(chunks), headers={"Content-Type": "application/xml"}
        )
        with httpx.Client() as client:
            if exceeds:
                with pytest.raises(
                    ValueError, match="^Stortinget sak response exceeds byte limit$"
                ):
                    fetch_sak(client, 89016, observed_at=OBSERVED)
            else:
                assert fetch_sak(client, 89016, observed_at=OBSERVED).body == b"abcdef"


@pytest.mark.parametrize(
    "content_type", [None, "text/html", " Application/XML ; charset=utf-8; x=y"]
)
def test_response_media_type_parameters_and_missing_header(httpx_mock, content_type) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}
    httpx_mock.add_response(url=URL, content=XML, headers=headers)
    with httpx.Client() as client:
        if content_type is None or content_type == "text/html":
            with pytest.raises(ValueError, match="^Expected an XML response from Stortinget$"):
                fetch_sak(client, 89016, observed_at=OBSERVED)
        else:
            assert fetch_sak(client, 89016, observed_at=OBSERVED).content_type == "application/xml"


def test_response_endpoint_is_validated() -> None:
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://example.invalid/?sakid=1"),
        headers={"Content-Type": "application/xml"},
    )
    with pytest.raises(ValueError, match="^Unexpected source endpoint$"):
        _validate_response(response)


def test_first_case_and_default_observation_and_http_method(httpx_mock) -> None:
    httpx_mock.add_response(
        method="GET",
        url="https://data.stortinget.no/eksport/sak?sakid=1",
        content=XML,
        headers={"Content-Type": "application/xml"},
    )
    before = datetime.now(UTC)
    with httpx.Client() as client:
        capture = fetch_sak(client, 1)
    assert capture.sak_id == 1
    assert capture.observed_at.tzinfo is not None
    assert before <= capture.observed_at <= datetime.now(UTC)


def test_redirect_override_on_redirect_following_client(httpx_mock) -> None:
    httpx_mock.add_response(url=URL, status_code=302, headers={"Location": URL + "&redirect=1"})
    with httpx.Client(follow_redirects=True) as client, pytest.raises(httpx.HTTPStatusError):
        fetch_sak(client, 89016, observed_at=OBSERVED)


@pytest.mark.parametrize(
    "body,message",
    [
        (b"<!DOCTYPE broken", "Forbidden XML DTD"),
        (b"<broken", "Invalid Stortinget case XML"),
        (b"<wrong/>", "Unexpected XML root"),
        (XML.replace(b"<id>89016</id>", b""), "Stortinget case id mismatch"),
        (
            XML.replace(b"<sak_sesjon>2021-2022</sak_sesjon>", b""),
            "Invalid Stortinget session identity",
        ),
        (
            (
                "<?xml version='1.0' encoding='utf-16'?><!DOCTYPE detaljert_sak>"
                + XML.decode().split("?>", 1)[1]
            ).encode("utf-16"),
            "Forbidden XML DTD",
        ),
        (
            b'<detaljert_sak xmlns="http://data.stortinget.no">'
            + b"<x>" * 300
            + b"</x>" * 300
            + b"</detaljert_sak>",
            "Invalid Stortinget case XML",
        ),
        (
            (
                "<?xml version='1.0' encoding='utf-16'?>"
                "<!DOCTYPE detaljert_sak [<!ENTITY x SYSTEM "
                "'file:///definitely-absent-lovspor'>]>"
                "<detaljert_sak xmlns='http://data.stortinget.no'>&x;</detaljert_sak>"
            ).encode("utf-16"),
            "Forbidden XML DTD",
        ),
    ],
)
def test_parse_rejection_diagnostics_are_exact(body, message) -> None:
    with pytest.raises(ValueError) as error:
        parse_sak(_snapshot(body))
    assert str(error.value) == message


def test_metadata_keeps_evidence_digest() -> None:
    snapshot = _snapshot()
    assert parse_sak(snapshot).source_sha256 == sha256(XML).hexdigest()


def test_invalid_case_id_diagnostic() -> None:
    with (
        _client() as client,
        pytest.raises(ValueError, match="^sak_id must be a positive integer$"),
    ):
        fetch_sak(client, 0)


def test_archive_exact_identity_and_utc_observation_name(tmp_path, monkeypatch) -> None:
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "EST5")
    time.tzset()
    try:
        capture = _snapshot()
        directory = tmp_path / "missing" / "archive"
        path = archive_sak(capture, directory)
        name = f"sak-89016-{sha256(XML).hexdigest()}"
        assert path == directory / f"{name}.xml"
        assert (
            path.with_suffix(".json").read_bytes()
            == json.dumps(
                {
                    "sak_id": 89016,
                    "source_url": URL,
                    "sha256": sha256(XML).hexdigest(),
                    "content_type": "application/xml",
                },
                sort_keys=True,
            ).encode()
        )
        observation = directory / "observations" / f"{name}-20261014T080000000000Z.json"
        assert (
            observation.read_bytes()
            == json.dumps(
                {
                    "object": path.name,
                    "observed_at": OBSERVED.isoformat(),
                },
                sort_keys=True,
            ).encode()
        )
        observation.write_bytes(b"torn")
        with pytest.raises(ValueError, match="^Archived Stortinget object hash/content mismatch$"):
            archive_sak(capture, directory)
        assert observation.read_bytes() == b"torn"
    finally:
        if previous is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", previous)
        time.tzset()


@pytest.mark.parametrize("observed", [OBSERVED.replace(tzinfo=None), OBSERVED])
def test_snapshot_verification_exact_diagnostics(tmp_path, observed) -> None:
    capture = replace(_snapshot(), observed_at=observed)
    if observed.tzinfo is not None:
        capture = replace(capture, source_sha256="0" * 64)
        message = "Stortinget evidence digest mismatch"
    else:
        message = "Stortinget observation time must be timezone aware"
    with pytest.raises(ValueError) as error:
        archive_sak(capture, tmp_path)
    assert str(error.value) == message


def test_utf16_external_entity_is_not_loaded(tmp_path) -> None:
    entity = tmp_path / "entity.xml"
    entity.write_bytes(b"\xff")
    body = (
        "<?xml version='1.0' encoding='utf-16'?>"
        f"<!DOCTYPE detaljert_sak [<!ENTITY x SYSTEM '{entity.as_uri()}'>]>"
        "<detaljert_sak xmlns='http://data.stortinget.no'>&x;</detaljert_sak>"
    ).encode("utf-16")
    with pytest.raises(ValueError, match="^Forbidden XML DTD$"):
        parse_sak(_snapshot(body))


@pytest.mark.parametrize(
    "field", [b"<type>lovvedtak</type>", b"<lenke_tekst>Lovvedtak 85 (2021-2022)</lenke_tekst>"]
)
def test_missing_reference_fields_do_not_attest(field) -> None:
    assert parse_sak(_snapshot(XML.replace(field, b""))).lovvedtak == ()
