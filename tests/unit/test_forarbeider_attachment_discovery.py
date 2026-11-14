"""FC6b: attachment discovery from raw pages alone, and the verified inventory (#738).

Synthetic pages only: the invented Prop. 901 L of ``chapter-l.html`` and its
landing page ``landing-l-plain.html``, with invented links and bytes.
"""

import json
from typing import Any

import pytest

from lovspor.forarbeider.attachment_discovery import (
    DISCOVERY_READER,
    canonical_url,
    discover_attachments,
)
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.errors import RefusalReason
from lovspor.forarbeider.version_completeness import (
    publication_completeness,
    source_completeness,
)
from lovspor.forarbeider.version_manifest import (
    Evidence,
    manifest_text,
    parse_version_manifest,
    verify_version_manifest,
)
from lovspor.forarbeider.version_models import (
    AttachmentState,
    PublicationState,
    SourceState,
    VersionContent,
    VersionManifest,
)
from lovspor.forarbeider.version_reader import read_version_summaries
from tests.unit.forarbeider_version_support import (
    ASSETS,
    DOCUMENT_PATH,
    LANDING_URL,
    PDF,
    PDF_URL,
    ZIP,
    build,
    capture,
    codes,
    evidence,
    first_or_any,
    landing,
    pages,
    refused,
    sha,
    with_body,
    with_markup,
)
from tests.unit.test_forarbeider_document_extract import WHEN

HOST = "https://www.regjeringen.no"
PDF_PATH = PDF_URL.removeprefix(HOST)
SECOND_URL = ASSETS + "/vedlegg/vedlegg-2.pdf"
NAV = '<nav class="longdoc-nav">'
FOOTER = "</footer>"
EDITION_NOTICE = (
    '<div data-doc-meta="corrected-edition-date">(Korrigert utgave per 13.5.2100)</div>'
)
OPRI = ASSETS + "/opri/prp209921000901000dddopri.pdf"
RETT = ASSETS + "/rett/rettebrev.pdf"
EDITION_BLOCK = (
    '<div><h3 class="edition">Opprinnelig utgave og rettebrev</h3><ul class="link-list">'
    f'<li><a href="{OPRI.removeprefix(HOST)}">Opprinnelig utgave (1 MB)</a></li>'
    f'<li><a href="{RETT.removeprefix(HOST)}">Rettebrev 5. mai 2100</a></li>'
    "</ul></div>"
)


def linking(number: int, *hrefs: str) -> SourcePage:
    """Chapter ``number`` whose text links ``hrefs``."""
    links = "".join(f'<a href="{href}">vedlegg</a> ' for href in hrefs)
    return with_body(number, f"<p>Se {links}.</p>")


def downloads(page: SourcePage, *formats: str) -> SourcePage:
    items = "".join(
        f'<li><a href="/contentassets/{"1" * 32}/no/{kind}/prp209921000901000ddd{kind}.{kind[:4]}">'
        f"{kind}</a></li>"
        for kind in formats
    )
    wrapper = (
        '<div class="download-container-wrapper"><div class="longdoc-tools-area">'
        '<button class="longdoc-download" type="button"></button>'
        '<div class="longdoc-download-area"><ul class="longdoc-download-list">'
        f"{items}</ul></div></div></div>"
    )
    return with_markup(page, NAV, wrapper)


def corrected_landing() -> SourcePage:
    page = landing(EDITION_BLOCK)
    return with_markup(page, "<h1>", EDITION_NOTICE)


def discovery_of(given: list[SourcePage], front: SourcePage | None) -> Any:
    extraction = extract_document(given)
    return discover_attachments(extraction, {sha(item.payload): item for item in given}, front)


# --- verified inventories ----------------------------------------------------------


def test_every_page_scanned_and_nothing_found_is_a_proved_zero() -> None:
    manifest = build()
    attachments = manifest.content.attachments
    assert attachments.reader == DISCOVERY_READER
    assert attachments.discovery == "verified"
    assert {(page.role, page.chapter_number) for page in attachments.pages_scanned} == {
        ("chapter", 1),
        ("chapter", 2),
        ("chapter", 3),
        ("chapter", 4),
        ("landing", None),
    }
    assert (manifest.source.attachments_expected, manifest.source.attachments_captured) == (0, 0)
    assert manifest.source.state is SourceState.SOURCE_COMPLETE


def test_one_attachment_found_and_not_captured_is_incomplete() -> None:
    manifest = build([*pages(1, 3, 4), linking(2, PDF_PATH)])
    (attachment,) = manifest.content.attachments.attachments
    assert (attachment.url, attachment.kind, attachment.state) == (
        PDF_URL,
        "attachment",
        AttachmentState.NOT_CAPTURED,
    )
    assert attachment.found_at[0].page_sha256 == sha(linking(2, PDF_PATH).payload)
    assert attachment.found_at[0].locator.endswith("/p/a")
    assert manifest.source.state is SourceState.SOURCE_INCOMPLETE
    assert (manifest.source.attachments_expected, manifest.source.attachments_captured) == (1, 0)
    assert ("attachment_not_captured", "incomplete") in codes(manifest.source.reasons)


def test_a_found_attachment_captured_as_pdf_completes_the_source() -> None:
    manifest = build([*pages(1, 3, 4), linking(2, PDF_PATH)], captures=(capture(PDF_URL, PDF),))
    (attachment,) = manifest.content.attachments.attachments
    assert (attachment.state, attachment.raw_sha256, attachment.media_type) == (
        AttachmentState.CAPTURED,
        sha(PDF),
        "application/pdf",
    )
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    assert (manifest.source.attachments_expected, manifest.source.attachments_captured) == (1, 1)
    assert manifest.publication.state is not PublicationState.PUBLICATION_COMPLETE
    assert ("attachment_clearance_absent", "unknown") in codes(manifest.publication.reasons)


def test_attachments_on_different_chapters_and_the_landing_page_are_all_found() -> None:
    given = [*pages(1, 4), linking(2, PDF_PATH), linking(3, SECOND_URL)]
    manifest = build(given, corrected_landing())
    found = {(item.url, item.kind) for item in manifest.content.attachments.attachments}
    assert found == {
        (PDF_URL, "attachment"),
        (SECOND_URL, "attachment"),
        (OPRI, "edition"),
        (RETT, "edition"),
    }
    assert manifest.content.attachments.discovery == "verified"
    assert manifest.source.attachments_expected == 4


def test_an_embedded_image_is_an_attachment_and_an_extraction_gap() -> None:
    manifest = build([*pages(1, 3, 4), with_body(2, '<p>Se <img src="/bilde/figur.png"> her.</p>')])
    (attachment,) = manifest.content.attachments.attachments
    assert attachment.kind == "embedded"
    assert manifest.source.state is SourceState.SOURCE_INCOMPLETE


def test_duplicate_links_in_any_spelling_are_one_attachment_with_every_place() -> None:
    upper = "HTTPS://WWW.REGJERINGEN.NO" + PDF_PATH + "#page=2"
    given = [*pages(1, 4), linking(2, PDF_PATH, upper), linking(3, PDF_URL)]
    (attachment,) = build(given).content.attachments.attachments
    assert attachment.url == PDF_URL
    assert len(attachment.found_at) == 3
    assert canonical_url(LANDING_URL + "?ch=2", upper) == PDF_URL


def test_renditions_on_every_page_are_recorded_apart_and_not_counted() -> None:
    given = [downloads(item, "pdfs", "docx", "epub") for item in pages()]
    manifest = build(given)
    attachments = manifest.content.attachments
    assert [item.format for item in attachments.renditions] == ["docx", "epub", "pdfs"]
    assert all(len(item.found_at) == 4 for item in attachments.renditions)
    assert attachments.attachments == ()
    assert manifest.source.state is SourceState.SOURCE_COMPLETE


# --- unproved discovery ------------------------------------------------------------


def test_renditions_that_differ_between_pages_leave_discovery_unproved() -> None:
    given = [downloads(item, "pdfs") for item in pages(1, 2, 3)] + pages(4)
    manifest = build(given)
    assert ("discovery_renditions_disagree", "unknown") in codes(manifest.source.reasons)
    assert manifest.source.state is SourceState.UNKNOWN


@pytest.mark.parametrize(
    ("front", "code"),
    [
        (None, "landing_page_missing"),
        (landing()._replace(payload=landing().payload[:-120]), "landing_page_refused"),
        (
            landing()._replace(url=LANDING_URL.replace("id9000001", "id9000002")),
            "landing_page_refused",
        ),
    ],
)
def test_without_the_landing_page_of_this_document_discovery_is_unknown(
    front: SourcePage | None, code: str
) -> None:
    manifest = build(landing_page=front)
    assert manifest.content.attachments.discovery == "unverified"
    assert (f"discovery_{code}", "unknown") in codes(manifest.source.reasons)
    assert manifest.source.state is SourceState.UNKNOWN
    assert manifest.source.attachments_expected is None


def test_a_missing_chapter_page_leaves_discovery_unproved_whatever_else_is_found() -> None:
    manifest = build([*pages(1, 3), linking(2, PDF_PATH)])
    assert manifest.content.attachments.discovery == "unverified"
    assert ("discovery_chapter_page_missing", "unknown") in codes(manifest.source.reasons)
    assert manifest.source.attachments_expected is None
    assert ("attachment_not_captured", "incomplete") in codes(manifest.source.reasons)
    assert manifest.source.state is SourceState.SOURCE_INCOMPLETE


@pytest.mark.parametrize(
    ("before", "markup", "code"),
    [
        (FOOTER, f'<a href="{PDF_PATH}">fil</a>', "file_outside_known_container"),
        (
            NAV,
            '<div class="relatert"><a href="/no/annet/id1/">annet</a></div>',
            "unrecognized_reference",
        ),
        (NAV, '<div class="vedlegg-liste"><span>tom</span></div>', "unknown_container"),
        (
            NAV,
            f'<div class="relatert"><a href="{PDF_PATH}">fil</a></div>',
            "file_outside_known_container",
        ),
    ],
)
def test_an_unrecognised_reference_or_container_leaves_discovery_unknown(
    before: str, markup: str, code: str
) -> None:
    manifest = build([*pages(1, 3, 4), with_markup(first_or_any(2), before, markup)])
    assert manifest.content.attachments.discovery == "unverified"
    assert (f"discovery_{code}", "unknown") in codes(manifest.source.reasons)
    assert manifest.source.state is SourceState.UNKNOWN
    assert manifest.source.attachments_expected is None


def test_a_non_web_link_in_the_text_is_unrecognised() -> None:
    manifest = build([*pages(1, 3, 4), linking(2, "ftp://example.invalid/fil")])
    assert ("discovery_unrecognized_reference", "unknown") in codes(manifest.source.reasons)


def test_a_web_link_in_the_text_is_a_citation_not_an_attachment() -> None:
    manifest = build([*pages(1, 3, 4), linking(2, "https://lovdata.no/dokument/NL/lov/1")])
    assert manifest.content.attachments.attachments == ()
    assert manifest.source.state is SourceState.SOURCE_COMPLETE


# --- captures ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("captures", "state", "reason"),
    [
        ((), AttachmentState.NOT_CAPTURED, ("attachment_not_captured", "incomplete")),
        (
            (capture(PDF_URL, ZIP),),
            AttachmentState.UNSUPPORTED,
            ("attachment_unsupported", "incomplete"),
        ),
        (
            (capture(PDF_URL, PDF), capture(PDF_URL, PDF + b"x")),
            AttachmentState.CONFLICTING,
            ("attachment_capture_conflicting", "unknown"),
        ),
    ],
)
def test_a_found_attachment_without_one_supported_capture_keeps_the_source_open(
    captures: tuple[SourcePage, ...], state: AttachmentState, reason: tuple[str, str]
) -> None:
    manifest = build([*pages(1, 3, 4), linking(2, PDF_PATH)], captures=captures)
    (attachment,) = manifest.content.attachments.attachments
    assert attachment.state is state
    assert codes(manifest.source.reasons) == {reason}
    assert manifest.source.attachments_captured == 0


def test_a_capture_of_a_url_not_discovered_adds_no_attachment() -> None:
    manifest = build(captures=(capture(SECOND_URL, PDF),))
    assert manifest.content.attachments.attachments == ()
    assert manifest.source.attachments_expected == 0


# --- integrity ---------------------------------------------------------------------


def test_reordered_pages_and_captures_give_identical_bytes() -> None:
    given = [*pages(1, 4), linking(2, PDF_PATH), linking(3, SECOND_URL)]
    caught = (capture(PDF_URL, PDF), capture(SECOND_URL, PDF + b"2"))
    forward = build(given, corrected_landing(), caught)
    backward = build(list(reversed(given)), corrected_landing(), tuple(reversed(caught)))
    assert manifest_text(forward) == manifest_text(backward)


def test_the_version_id_follows_the_inventory_and_its_capture_state() -> None:
    given = [*pages(1, 3, 4), linking(2, PDF_PATH)]
    plain, caught = build(given), build(given, captures=(capture(PDF_URL, PDF),))
    assert plain.version_id != caught.version_id
    assert build().version_id != plain.version_id


def test_altered_landing_bytes_fail_verification() -> None:
    raw = manifest_text(build()).encode()
    altered = landing('<p class="x">endret</p>')
    with refused(RefusalReason.MANIFEST_INVALID):
        verify_version_manifest(raw, evidence(landing_page=altered))


def _forge_verified(payload: dict[str, Any]) -> None:
    attachments = payload["content"]["attachments"]
    attachments.update(discovery="verified", gaps=[])
    attachments["pages_scanned"].append(
        {"role": "landing", "chapter_number": None, "raw_sha256": "f" * 64}
    )
    payload["source"].update(
        state="source_complete", reasons=[], attachments_expected=0, attachments_captured=0
    )


def _omit_attachment(payload: dict[str, Any]) -> None:
    payload["content"]["attachments"]["attachments"] = []
    payload["source"].update(
        state="source_complete", reasons=[], attachments_expected=0, attachments_captured=0
    )


def _rehashed(payload: dict[str, Any]) -> None:
    """Make the forgery fully self-consistent: new id, both axes recomputed from it."""
    content = VersionContent.model_validate(payload["content"])
    source = source_completeness(content)
    payload["source"] = source.model_dump(mode="json")
    payload["publication"] = publication_completeness(content, source).model_dump(mode="json")
    text = json.dumps(payload["content"], sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    payload["version_id"] = sha(text.encode())


def _forged(manifest: VersionManifest, *changes: Any) -> bytes:
    payload = json.loads(manifest_text(manifest))
    for change in changes:
        change(payload)
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def test_a_verified_inventory_without_its_landing_page_cannot_be_constructed() -> None:
    dumped = build(landing_page=None).model_dump()
    dumped["content"]["attachments"]["discovery"] = "verified"
    dumped["content"]["attachments"]["gaps"] = ()
    with pytest.raises(ValueError, match="verified exactly when"):
        VersionManifest.model_validate(dumped)


def test_counts_without_verified_discovery_cannot_be_constructed() -> None:
    dumped = build(landing_page=None).model_dump()
    dumped["source"].update(attachments_expected=0, attachments_captured=0)
    with pytest.raises(ValueError, match="no count, no source_complete"):
        VersionManifest.model_validate(dumped)


@pytest.mark.parametrize(
    ("given", "front", "forge"),
    [
        (None, None, _forge_verified),
        ([*pages(1, 3, 4), linking(2, PDF_PATH)], landing(), _omit_attachment),
    ],
)
def test_a_forged_complete_manifest_is_refused_by_verification_and_never_read_complete(
    given: list[SourcePage] | None, front: SourcePage | None, forge: Any
) -> None:
    honest = build(given, front)
    forged = _forged(honest, forge, _rehashed)
    assert parse_version_manifest(forged).source.state is SourceState.SOURCE_COMPLETE
    with refused(RefusalReason.MANIFEST_INVALID):
        verify_version_manifest(forged, evidence(given, front))
    (summary,) = read_version_summaries(forged)
    assert summary.source_state is not SourceState.SOURCE_COMPLETE
    with refused(RefusalReason.MANIFEST_INVALID):
        read_version_summaries(forged, evidence(given, front))


def test_a_forged_manifest_inconsistent_with_its_own_content_is_refused_on_parse() -> None:
    def claim(payload: dict[str, Any]) -> None:
        payload["source"].update(state="source_complete", reasons=[])

    with refused(RefusalReason.MANIFEST_INVALID):
        parse_version_manifest(_forged(build(landing_page=None), claim))


def test_discovery_alone_never_makes_a_document_eligible_for_release() -> None:
    manifest = build()
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    assert manifest.publication.state is not PublicationState.PUBLICATION_COMPLETE
    assert (manifest.cleared, manifest.automatic_release_eligible) == (False, False)


def test_discovery_reads_raw_bytes_in_any_order() -> None:
    given = [*pages(1, 4), linking(2, PDF_PATH), linking(3, SECOND_URL)]
    assert discovery_of(given, landing()) == discovery_of(list(reversed(given)), landing())


def test_the_evidence_takes_no_inventory_list_or_flag() -> None:
    assert Evidence._fields == ("pages", "landing", "captures")
    assert DOCUMENT_PATH in LANDING_URL
    assert WHEN.tzinfo is not None


@pytest.mark.parametrize("attribute", ["src", "data", "data-src", "data-href", "poster", "action"])
def test_non_href_references_outside_known_containers_are_not_silently_lost(
    attribute: str,
) -> None:
    # The reader promises to classify every reference, including form actions
    # and lazy-loading attributes, rather than just links selected by href.
    altered = with_markup(first_or_any(2), NAV, f'<div {attribute}="{PDF_PATH}"></div>')
    manifest = build([*pages(1, 3, 4), altered])
    (gap,) = manifest.content.attachments.gaps
    assert (gap.code, gap.detail, gap.page_sha256) == (
        "file_outside_known_container",
        PDF_URL,
        sha(altered.payload),
    )
    assert gap.locator is not None
    assert manifest.content.attachments.discovery == "unverified"
    assert manifest.source.attachments_expected is None
    assert manifest.source.state is SourceState.UNKNOWN


def test_srcset_discovers_every_candidate_and_deduplicates_the_src() -> None:
    body = f'<p><img src="{PDF_PATH}" srcset="{PDF_PATH} 1x, {SECOND_URL} 2x" alt="figur"></p>'
    altered = with_body(2, body)
    discovery = discovery_of([*pages(1, 3, 4), altered], landing())
    assert discovery.exhaustive
    assert [(item.url, item.kind) for item in discovery.attachments] == [
        (PDF_URL, "embedded"),
        (SECOND_URL, "embedded"),
    ]
    for item in discovery.attachments:
        (place,) = item.found_at
        assert place.page_sha256 == sha(altered.payload)
        assert place.locator.endswith("/p/img")


def test_one_url_used_as_both_attachment_and_embed_is_a_discovery_gap() -> None:
    body = f'<p><a href="{PDF_PATH}">fil</a><img src="{PDF_PATH}" alt="figur"></p>'
    manifest = build([*pages(1, 3, 4), with_body(2, body)])
    (gap,) = manifest.content.attachments.gaps
    assert (gap.code, gap.detail) == ("conflicting_reference", PDF_URL)
    assert manifest.content.attachments.discovery == "unverified"
    assert ("discovery_conflicting_reference", "unknown") in codes(manifest.source.reasons)
    assert manifest.source.attachments_expected is None


def test_identical_captures_of_canonical_url_aliases_are_one_capture() -> None:
    alias = "HTTPS://WWW.REGJERINGEN.NO:443" + PDF_PATH + "#page=2"
    manifest = build(
        [*pages(1, 3, 4), linking(2, PDF_PATH)],
        captures=(capture(PDF_URL, PDF), capture(alias, PDF), capture(PDF_URL, PDF)),
    )
    (attachment,) = manifest.content.attachments.attachments
    assert (attachment.url, attachment.state, attachment.raw_sha256) == (
        PDF_URL,
        AttachmentState.CAPTURED,
        sha(PDF),
    )
    assert (manifest.source.attachments_expected, manifest.source.attachments_captured) == (1, 1)
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    assert parse_version_manifest(manifest_text(manifest).encode()) == manifest


@pytest.mark.parametrize(
    ("raw", "media_type"),
    [
        (b"\x89PNG\r\n\x1a\nsynthetic image", "image/png"),
        (b"\xff\xd8\xffsynthetic image", "image/jpeg"),
        (b"", None),
        (b"%PDF", None),
        (b"\x89PNG\r\n\x1a", None),
        (b"\xff\xd8", None),
    ],
)
def test_discovered_capture_requires_a_supported_signature(
    raw: bytes, media_type: str | None
) -> None:
    manifest = build([*pages(1, 3, 4), linking(2, PDF_PATH)], captures=(capture(PDF_URL, raw),))
    (attachment,) = manifest.content.attachments.attachments
    assert (attachment.raw_sha256, attachment.media_type) == (sha(raw), media_type)
    supported = media_type is not None
    assert attachment.state is (
        AttachmentState.CAPTURED if supported else AttachmentState.UNSUPPORTED
    )
    assert manifest.source.attachments_captured == int(supported)
    assert manifest.source.state is (
        SourceState.SOURCE_COMPLETE if supported else SourceState.SOURCE_INCOMPLETE
    )


# --- landing page blocks read off real saved landing pages (structure only) ----------

LANDING_SIDEBAR = (
    '<aside class="content-col-1 article-sidebar"><div class="content-owner-dep">'
    '<div><a href="/no/dep/test/id1/">Testdepartementet</a></div></div>'
    '<div class="content-intro-topics"><ul class="topiclist">'
    '<li><a href="/no/tema/lov-og-rett/id1/">Lov og rett</a></li></ul></div>'
    '<div class="article-related"><ul class="link-list">'
    '<li><a href="/no/aktuelt/nyhet/id2/">Nyhet</a></li></ul></div>'
    "</aside>"
)
STORTINGET = '<a href="https://www.stortinget.no/id/PRP/901/20992100/000">Følg proposisjonen</a>'


def landing_downloads(lang: str) -> str:
    base = f"/contentassets/{'1' * 32}/{lang}"
    return (
        '<div class="download-container-wrapper"><div class="longdoc-tools-area">'
        '<div class="longdoc-download-area"><ul class="longdoc-download-list shown">'
        f'<li><a href="{base}/pdfs/prp209921000901000dddpdfs.pdf">PDF</a></li>'
        f'<li><a href="{base}/docx/prp209921000901000ddddocx.docx">DOCX</a></li>'
        "</ul></div></div></div>"
    )


@pytest.mark.parametrize("lang", ["no", "nn-no"])
def test_a_landing_page_with_downloads_sidebar_and_stortinget_link_is_fully_read(
    lang: str,
) -> None:
    front = landing(landing_downloads(lang) + STORTINGET + LANDING_SIDEBAR)
    chapters = [with_markup(item, NAV, landing_downloads(lang)) for item in pages()]
    manifest = build(chapters, front)
    attachments = manifest.content.attachments
    assert attachments.gaps == ()
    assert attachments.discovery == "verified"
    assert [item.format for item in attachments.renditions] == ["docx", "pdfs"]
    assert all(len(item.found_at) == 5 for item in attachments.renditions)
    assert (manifest.source.attachments_expected, manifest.source.state) == (
        0,
        SourceState.SOURCE_COMPLETE,
    )


def test_a_file_in_the_landing_sidebar_is_still_a_gap() -> None:
    sidebar = LANDING_SIDEBAR.replace("/no/aktuelt/nyhet/id2/", PDF_PATH)
    manifest = build(landing_page=landing(sidebar))
    assert ("discovery_file_outside_known_container", "unknown") in codes(manifest.source.reasons)


def test_a_stortinget_link_other_than_the_proposition_page_is_unrecognised() -> None:
    other = STORTINGET.replace("/id/PRP/901/20992100/000", "/no/annet/")
    manifest = build(landing_page=landing(other))
    assert ("discovery_unrecognized_reference", "unknown") in codes(manifest.source.reasons)
