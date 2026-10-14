"""Fixture-based #610 case-page probe; no external requests or invented vote joins."""

from lovspor.legislative_crosswalk import CrosswalkRequest, resolve_case_page

OFFICIAL_URL = "https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016"
# Checked against the official case page on 2026-10-14; deliberately an
# excerpt, not a byte-identical archived HTML response.
STORTING_EXCERPT = """
<h1>Endringer i forvaltningsloven</h1>
<p>Prop. 81 L (2021-2022), Innst. 386 L (2021-2022),
Lovvedtak 85 (2021-2022)</p>
<p>Sanksjonert 17.06.2022 (lov63)</p>
<p>Ikrafttredelse 01.07.2022 (lov 63)</p>
"""


def probe(source_body: str = STORTING_EXCERPT, **overrides: object):
    identifiers: dict[str, object] = {
        "law_id": "LOV-2022-06-17-63",
        "session_id": "2021-2022",
        "lovvedtak_number": 85,
    }
    origin: dict[str, object] = {
        "source_url": OFFICIAL_URL,
        "source_body": source_body,
    }
    for key, value in overrides.items():
        (identifiers if key in identifiers else origin)[key] = value
    request = CrosswalkRequest(**identifiers)  # type: ignore[arg-type]
    return resolve_case_page(request, **origin)  # type: ignore[arg-type]


def test_official_case_attests_lov_to_lovvedtak_to_sak() -> None:
    result = probe()
    assert result.status == "attested"
    assert (result.law_id, result.session_id, result.lovvedtak_number, result.sak_id) == (
        "LOV-2022-06-17-63",
        "2021-2022",
        85,
        89016,
    )
    assert result.representation == "excerpt"
    assert len(result.input_sha256) == 64


def test_repeated_identical_lovvedtak_does_not_create_ambiguity() -> None:
    assert probe(STORTING_EXCERPT + "<p>Lovvedtak 85 (2021\u20132022)</p>").status == "attested"


def test_missing_sanction_stays_unresolved_not_guess_from_date() -> None:
    result = probe("Lovvedtak 85 (2021-2022). Ikrafttredelse 01.07.2022 (lov63)")
    assert result.status == "unresolved"


def test_missing_lovvedtak_stays_unresolved() -> None:
    assert probe("Sanksjonert 17.06.2022 (lov63)").status == "unresolved"


def test_conflicting_law_fails_closed() -> None:
    assert probe(STORTING_EXCERPT, law_id="LOV-2022-06-17-64").status == "contradicted"


def test_conflicting_parliamentary_session_fails_closed() -> None:
    assert probe(STORTING_EXCERPT, session_id="2022-2023").status == "contradicted"


def test_multiple_laws_are_ambiguous_not_cross_product() -> None:
    assert probe(STORTING_EXCERPT + " Sanksjonert 17.06.2022 (lov64)").status == "ambiguous"


def test_multiple_decisions_are_ambiguous() -> None:
    assert probe(STORTING_EXCERPT + " Lovvedtak 86 (2021-2022)").status == "ambiguous"


def test_reject_host_path_and_duplicate_case_id() -> None:
    for url in (
        "https://evil.example/no/Saker-og-publikasjoner/Saker/Sak/?p=89016",
        "https://www.stortinget.no.evil.example/no/Saker-og-publikasjoner/Saker/Sak/?p=89016",
        "https://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016&p=42",
        "http://www.stortinget.no/no/Saker-og-publikasjoner/Saker/Sak/?p=89016",
    ):
        assert probe(source_url=url).status == "unsupported"


def test_script_content_is_not_used_as_attestation() -> None:
    body = "<script>Lovvedtak 85 (2021-2022) Sanksjonert 17.06.2022 (lov63)</script>"
    assert probe(body).status == "unresolved"


def test_only_hashes_supplied_evidence_bytes() -> None:
    assert probe("Lovvedtak 85 (2021-2022)").input_sha256 != probe().input_sha256
