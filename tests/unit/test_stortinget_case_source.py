"""Offline API-contract tests; XML is a synthetic derivative of the official schema."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from lovspor.stortinget_case_source import (
    CaseSnapshot,
    LawDecision,
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
