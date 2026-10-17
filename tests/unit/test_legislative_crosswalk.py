"""Fixture-based #610 case-page probe; no external requests or invented vote joins."""

from hashlib import sha256

import pytest

from lovspor.legislative_crosswalk import CrosswalkRequest, _text, resolve_case_page

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


@pytest.mark.parametrize("authority", ["invalid", "65536", "0", "443", "", "user@"])
def test_any_port_or_userinfo_is_unsupported_never_an_exception(authority: str) -> None:
    if authority.endswith("@"):
        url = OFFICIAL_URL.replace("www.", f"{authority}www.")
    else:
        url = OFFICIAL_URL.replace(".no/", f".no:{authority}/")
    assert probe(source_url=url).status == "unsupported"


@pytest.mark.parametrize("authority", ["[", "[not-an-ip]", "www.stortinget.no]", "[::1"])
def test_a_broken_bracket_authority_is_unsupported_never_an_exception(authority: str) -> None:
    url = OFFICIAL_URL.replace("www.stortinget.no", authority)
    assert probe(source_url=url).status == "unsupported"


def test_script_content_is_not_used_as_attestation() -> None:
    body = "<script>Lovvedtak 85 (2021-2022) Sanksjonert 17.06.2022 (lov63)</script>"
    assert probe(body).status == "unresolved"


def test_only_hashes_supplied_evidence_bytes() -> None:
    assert probe("Lovvedtak 85 (2021-2022)").input_sha256 != probe().input_sha256


@pytest.mark.parametrize(
    "query", ["", "p=", "p=0", "p=-1", "p=1.5", "p=\uff18\uff19\uff10\uff11\uff16", "p=89016&x=1"]
)
def test_invalid_case_query_has_no_typed_id(query: str) -> None:
    result = probe(source_url=OFFICIAL_URL.split("?", maxsplit=1)[0] + "?" + query)
    assert result.status == "unsupported"
    assert result.sak_id is None


@pytest.mark.parametrize(
    "url",
    [
        OFFICIAL_URL + "#evidence",
        OFFICIAL_URL.replace("/Sak/", "/Voteringsoversikt/"),
    ],
)
def test_non_case_page_url_is_unsupported(url: str) -> None:
    assert probe(source_url=url).status == "unsupported"


@pytest.mark.parametrize(
    "overrides",
    [
        {"law_id": "LOV-2022-06-17"},
        {"law_id": "LOV-2022-06-17-63suffix"},
        {"session_id": "2021/2022"},
        {"lovvedtak_number": 0},
        {"lovvedtak_number": -1},
        {"representation": "html"},
    ],
)
def test_invalid_request_is_unsupported_even_with_matching_evidence(
    overrides: dict[str, object],
) -> None:
    assert probe(**overrides).status == "unsupported"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "Sanksjonert 31.02.2022 (lov63) Lovvedtak 85 (2021-2022)",
        "Sanksjonert 17.06.2022 (lov63) Lovvedtak 85 (2021-2023)",
    ],
)
def test_missing_or_invalid_identity_evidence_is_unresolved(body: str) -> None:
    assert probe(body).status == "unresolved"


@pytest.mark.parametrize("tag", ["script", "style"])
def test_hidden_conflicting_identities_do_not_change_visible_attestation(tag: str) -> None:
    hidden = f"<{tag}>Lovvedtak 86 (2021-2022) Sanksjonert 17.06.2022 (lov64)</{tag}>"
    assert probe(hidden + STORTING_EXCERPT).status == "attested"


def test_comments_and_attributes_are_not_evidence() -> None:
    evidence = "Lovvedtak 85 (2021-2022) Sanksjonert 17.06.2022 (lov63)"
    assert (
        probe(f'<!-- {evidence} --><p title="{evidence}">No identities</p>').status == "unresolved"
    )


def test_visible_entities_and_whitespace_preserve_attestation() -> None:
    body = "<p>Lovvedtak&nbsp;85 (2021&ndash;2022)</p><p>Sanksjonert\n17.6.2022 (lov 63)</p>"
    assert probe(body).status == "attested"


def test_duplicate_sanction_is_not_ambiguous() -> None:
    assert probe(STORTING_EXCERPT + " Sanksjonert 17.6.2022 (lov 063)").status == "attested"


def test_conflicting_decision_number_is_contradicted() -> None:
    assert probe(lovvedtak_number=86).status == "contradicted"


def test_probe_is_deterministic_and_preserves_exact_evidence_digest() -> None:
    # The pilot promises a checksum of supplied UTF-8 bytes, not normalized HTML.
    body = STORTING_EXCERPT + "<!-- æøå -->\r\n"
    result = probe(body, representation="full_page")
    assert result == probe(body, representation="full_page")
    assert result.status == "attested"
    assert result.source_url == OFFICIAL_URL
    assert result.representation == "full_page"
    assert result.input_sha256 == sha256(body.encode("utf-8")).hexdigest()
    assert result.input_sha256 != probe(body.replace("\r\n", "\n")).input_sha256
    assert (
        probe().input_sha256 == "d452e746b719a1b08e46771ec1c3e005f7ee6b9cbd30b60d1a078395c5ed290f"
    )


@pytest.mark.parametrize(
    ("body", "overrides", "status", "reason"),
    [
        (STORTING_EXCERPT, {}, "attested", "The official case page names both exact identifiers"),
        ("", {}, "unresolved", "Missing explicit sanction or Lovvedtak reference"),
        (
            STORTING_EXCERPT + " Lovvedtak 86 (2021-2022)",
            {},
            "ambiguous",
            "Several law/decision identities occur in the source",
        ),
        (
            STORTING_EXCERPT,
            {"lovvedtak_number": 86},
            "contradicted",
            "Explicit source identifiers contradict the requested link",
        ),
        (
            STORTING_EXCERPT,
            {"law_id": "invalid"},
            "unsupported",
            "Invalid official case URL or Lovdata law identity",
        ),
        (
            STORTING_EXCERPT,
            {"lovvedtak_number": 0},
            "unsupported",
            "Invalid parliamentary decision identity",
        ),
        (
            STORTING_EXCERPT,
            {"representation": "html"},
            "unsupported",
            "Unrecognised evidence representation",
        ),
    ],
)
def test_probe_preserves_verdict_explanation(
    body: str, overrides: dict[str, object], status: str, reason: str
) -> None:
    result = probe(body, **overrides)
    assert (result.status, result.reason) == (status, reason)


@pytest.mark.parametrize("suffix", ["X", "/X", "xX/"])
def test_case_path_cannot_have_trailing_non_slash_characters(suffix: str) -> None:
    url = OFFICIAL_URL.replace("/Sak/", "/Sak" + suffix)
    result = probe(source_url=url)
    assert result.status == "unsupported"
    assert result.sak_id is None


@pytest.mark.parametrize("query", ["p=89016&p=", "p=89016&x=", "p=&p=89016"])
def test_blank_query_fields_cannot_be_discarded(query: str) -> None:
    result = probe(source_url=OFFICIAL_URL.split("?", maxsplit=1)[0] + "?" + query)
    assert result.status == "unsupported"
    assert result.sak_id is None


def test_smallest_positive_case_and_decision_are_valid() -> None:
    result = probe(
        "Lovvedtak 1 (2021-2022) Sanksjonert 17.06.2022 (lov63)",
        source_url=OFFICIAL_URL.replace("89016", "1"),
        lovvedtak_number=1,
    )
    assert result.status == "attested"
    assert result.sak_id == 1


def test_invalid_sanction_does_not_hide_later_valid_sanction() -> None:
    body = "Sanksjonert 31.02.2022 (lov64) " + STORTING_EXCERPT
    assert probe(body).status == "attested"


def test_plain_text_normalizes_whitespace_to_single_spaces() -> None:
    assert _text("  Lovvedtak\t85\r\n(2021-2022)  ") == "Lovvedtak 85 (2021-2022)"


@pytest.mark.parametrize("tag", ["script", "style"])
def test_unmatched_hidden_end_tag_does_not_hide_visible_evidence(tag: str) -> None:
    assert probe(f"</{tag}>" + STORTING_EXCERPT).status == "attested"


def test_nested_hidden_markup_does_not_leak_evidence() -> None:
    body = (
        "<script><style></style>"
        "Lovvedtak 86 (2021-2022) Sanksjonert 17.06.2022 (lov64)"
        "</script>" + STORTING_EXCERPT
    )
    assert probe(body).status == "attested"


@pytest.mark.parametrize("tag", ["script", "style"])
def test_hidden_section_ends_before_plain_visible_evidence(tag: str) -> None:
    body = f"<{tag}>untrusted text</{tag}>Lovvedtak 85 (2021-2022) Sanksjonert 17.06.2022 (lov63)"
    assert probe(body).status == "attested"


@pytest.mark.parametrize(
    "body",
    [
        "Lovvedtak 85 (2021-2022) Lovvedtak 85 (2022-2023)",
        "Sanksjonert 17.06.2022 (lov63) Sanksjonert 18.06.2022 (lov63)",
    ],
)
def test_distinct_identities_are_ambiguous_even_without_other_edge(body: str) -> None:
    # The pilot explicitly fails closed on multiple distinct identities.
    assert probe(body).status == "ambiguous"


@pytest.mark.parametrize(
    "overrides",
    [
        {"source_url": OFFICIAL_URL.replace("stortinget.no", "example.org")},
        {"law_id": "invalid"},
        {"lovvedtak_number": 0},
        {"representation": "html"},
    ],
)
def test_invalid_origin_or_request_cannot_be_overridden_by_ambiguous_evidence(
    overrides: dict[str, object],
) -> None:
    body = STORTING_EXCERPT + " Lovvedtak 86 (2021-2022)"
    assert probe(body, **overrides).status == "unsupported"


@pytest.mark.parametrize("year, status", [(2000, "attested"), (1900, "unresolved")])
def test_sanction_date_obeys_century_leap_year_boundary(year: int, status: str) -> None:
    body = f"Lovvedtak 85 ({year - 1}-{year}) Sanksjonert 29.02.{year} (lov63)"
    result = probe(body, law_id=f"LOV-{year}-02-29-63", session_id=f"{year - 1}-{year}")
    assert result.status == status


@pytest.mark.parametrize("host", ["stortinget.no", "WWW.STORTINGET.NO"])
def test_official_host_variants_preserve_case_identity(host: str) -> None:
    url = OFFICIAL_URL.replace("www.stortinget.no", host)
    result = probe(source_url=url)
    assert result.status == "attested"
    assert result.sak_id == 89016
    assert result.source_url == url
