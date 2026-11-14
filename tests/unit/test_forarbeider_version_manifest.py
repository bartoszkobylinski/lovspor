"""FC6: version manifest v3 and both completeness axes, from evidence only (#738).

Synthetic pages only: the invented proposition of ``chapter-l.html`` lists
chapters 1 to 4, chapter 4 an annex. Attachment bytes are invented too.
"""

import hashlib
import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import parse_any_manifest
from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.version_completeness import (
    publication_completeness,
    source_completeness,
)
from lovspor.forarbeider.version_manifest import (
    AttachmentInventory,
    AttachmentListing,
    Evidence,
    build_version_manifest,
    manifest_text,
    parse_version_manifest,
    verify_version_manifest,
)
from lovspor.forarbeider.version_models import (
    AttachmentState,
    Effect,
    PublicationState,
    SourceState,
    VersionManifest,
    VersionSpan,
)
from lovspor.forarbeider.version_reader import read_version_summaries
from tests.unit.test_forarbeider_document_extract import (
    HEAD,
    QUOTED,
    URL,
    WHEN,
    chapter_page,
    page,
    section,
)

TITLES = {1: "1 Innledning", 2: "2 Bakgrunn", 3: "Tilråding", 4: "1 Oppdiktet vedlegg"}
PDF = b"%PDF-1.7\n% invented attachment\n%%EOF\n"
ZIP = b"PK\x03\x04 invented archive"
PDF_URL = "https://www.regjeringen.no/contentassets/" + "0" * 32 + "/no/pdfs/vedlegg.pdf"
CORPUS = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "corpus"


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def first_or_any(number: int) -> SourcePage:
    """Chapter ``number``; the first chapter links back to the landing page."""
    built = chapter_page(number, TITLES[number])
    landing = 'href="/no/dokumenter/prop.-901-l-20992100/id9000001/"'
    raw = re.sub(r'href="[^"]*\?ch=0"', landing, built.payload.decode())
    return built._replace(payload=raw.encode())


def pages(*numbers: int) -> list[SourcePage]:
    return [first_or_any(number) for number in numbers or (1, 2, 3, 4)]


def with_body(number: int, body: str) -> SourcePage:
    """Chapter ``number`` with ``body`` after its heading section."""
    original = first_or_any(number)
    marker = f"<p>Tekst i kapittel {number}.</p>"
    raw = original.payload.decode().replace(marker, body).encode()
    return original._replace(payload=raw)


def inventory(
    *attachments: AttachmentListing, read_from: SourcePage | None = None
) -> AttachmentInventory:
    basis = sha((read_from or pages(1)[0]).payload)
    return AttachmentInventory(
        reader="synthetic-reader/1", read_from=(basis,), attachments=attachments
    )


def listing(raw_sha256: str | None) -> AttachmentListing:
    return AttachmentListing(url=PDF_URL, listed_on=sha(pages(1)[0].payload), raw_sha256=raw_sha256)


def build(
    given: list[SourcePage] | None = None,
    attachments: AttachmentInventory | None = None,
    raw: Mapping[str, bytes] | None = None,
) -> VersionManifest:
    chosen = pages() if given is None else given
    return build_version_manifest(Evidence(tuple(chosen), attachments, dict(raw or {})))


def complete_source() -> VersionManifest:
    return build(attachments=inventory())


def codes(reasons: Any) -> set[tuple[str, str]]:
    return {(reason.code, reason.effect.value) for reason in reasons}


def refused(reason: RefusalReason) -> Any:
    return pytest.raises(ForarbeiderRefusedError, match=reason.value)


# --- source completeness -----------------------------------------------------------


def test_every_listed_chapter_extracted_and_a_verified_empty_inventory_is_source_complete() -> None:
    source = complete_source().source
    assert source.state is SourceState.SOURCE_COMPLETE
    assert (source.parts_expected, source.parts_extracted) == (4, 4)
    assert (source.attachments_expected, source.attachments_captured) == (0, 0)
    assert source.extraction_gaps == 0
    assert source.reasons == ()


def test_a_chapter_not_given_is_confirmed_incomplete_with_its_number() -> None:
    source = build(pages(1, 2, 3), inventory()).source
    assert source.state is SourceState.SOURCE_INCOMPLETE
    assert (source.parts_expected, source.parts_extracted) == (4, 3)
    assert ("not_fetched", "incomplete") in codes(source.reasons)
    missing = [reason for reason in source.reasons if reason.code == "chapter_missing"]
    assert [reason.detail for reason in missing] == ["ch4"]


def test_a_refused_page_is_confirmed_incomplete() -> None:
    cut = pages(2)[0]._replace(payload=pages(2)[0].payload[:-200])
    source = build([*pages(), cut], inventory()).source
    assert source.state is SourceState.SOURCE_INCOMPLETE
    assert ("page_refused", "incomplete") in codes(source.reasons)


def test_two_different_pages_of_one_chapter_leave_it_unrepresented() -> None:
    other = with_body(2, "<p>En annen tekst.</p>")
    manifest = build([*pages(), other], inventory())
    assert manifest.source.state is SourceState.SOURCE_INCOMPLETE
    assert ("conflicting_pages", "incomplete") in codes(manifest.source.reasons)
    assert [item.chapter_number for item in manifest.content.parts] == [1, 3, 4]


def test_coverage_that_is_not_exact_is_incomplete_for_that_part() -> None:
    content = complete_source().content
    part = content.parts[1]
    broken = part.model_copy(update={"coverage": part.coverage.model_copy(update={"exact": False})})
    content = content.model_copy(update={"parts": (content.parts[0], broken, *content.parts[2:])})
    source = source_completeness(content)
    assert source.state is SourceState.SOURCE_INCOMPLETE
    (reason,) = [item for item in source.reasons if item.code == "coverage_not_exact"]
    assert reason.part_id == part.id


def test_an_extraction_gap_is_incomplete_and_names_its_part_and_entry() -> None:
    table = with_body(2, "<table><tr><td>x</td></tr></table>")
    manifest = build([*pages(1, 3, 4), table], inventory())
    source = manifest.source
    assert source.state is SourceState.SOURCE_INCOMPLETE
    assert source.extraction_gaps == 1
    (reason,) = [item for item in source.reasons if item.code == "table"]
    assert reason.effect is Effect.INCOMPLETE
    assert reason.part_id == "regjeringen-9000001-ch2"
    assert reason.entry_id == "regjeringen-9000001-ch2/kap2-1/1"


def test_an_unconfirmed_boundary_leaves_the_source_unknown_not_incomplete() -> None:
    wrong = with_body(2, "")
    raw = wrong.payload.decode().replace("<h1>2 Bakgrunn</h1>", "<h1>2 Noe annet</h1>").encode()
    source = build([*pages(1, 3, 4), wrong._replace(payload=raw)], inventory()).source
    assert source.state is SourceState.UNKNOWN
    assert ("boundary_unconfirmed", "unknown") in codes(source.reasons)


def test_no_attachment_inventory_is_unknown_never_zero() -> None:
    source = build().source
    assert source.state is SourceState.UNKNOWN
    assert (source.attachments_expected, source.attachments_captured) == (None, None)
    assert codes(source.reasons) == {("attachment_inventory_missing", "unknown")}


def test_an_inventory_whose_basis_page_is_absent_is_unverified_and_states_no_count() -> None:
    elsewhere = SourcePage(payload=b"<html>listing</html>", url=URL, retrieved_at=WHEN)
    manifest = build(attachments=inventory(read_from=elsewhere))
    assert manifest.content.attachments is not None
    assert manifest.content.attachments.basis.value == "unverified"
    assert manifest.source.state is SourceState.UNKNOWN
    assert manifest.source.attachments_expected is None
    assert ("attachment_inventory_unverified", "unknown") in codes(manifest.source.reasons)


def test_the_basis_page_may_come_from_the_raw_evidence() -> None:
    elsewhere = SourcePage(payload=b"<html>listing</html>", url=URL, retrieved_at=WHEN)
    manifest = build(
        attachments=inventory(read_from=elsewhere), raw={sha(elsewhere.payload): elsewhere.payload}
    )
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    assert manifest.source.attachments_expected == 0


@pytest.mark.parametrize(
    ("claimed", "given", "state", "reason"),
    [
        (None, {}, AttachmentState.NOT_CAPTURED, ("attachment_not_captured", "incomplete")),
        (sha(PDF), {}, AttachmentState.UNVERIFIED, ("attachment_unverified", "unknown")),
        (
            sha(ZIP),
            {sha(ZIP): ZIP},
            AttachmentState.UNSUPPORTED,
            ("attachment_unsupported", "incomplete"),
        ),
    ],
)
def test_an_attachment_not_proved_captured_keeps_the_source_from_complete(
    claimed: str | None, given: dict[str, bytes], state: AttachmentState, reason: tuple[str, str]
) -> None:
    manifest = build(attachments=inventory(listing(claimed)), raw=given)
    assert manifest.content.attachments is not None
    assert [item.state for item in manifest.content.attachments.attachments] == [state]
    assert codes(manifest.source.reasons) == {reason}
    assert manifest.source.attachments_expected == 1
    assert manifest.source.attachments_captured == 0


def test_an_attachment_whose_bytes_are_given_and_hash_is_captured() -> None:
    manifest = build(attachments=inventory(listing(sha(PDF))), raw={sha(PDF): PDF})
    (attachment,) = manifest.content.attachments.attachments  # type: ignore[union-attr]
    assert (attachment.state, attachment.media_type) == (
        AttachmentState.CAPTURED,
        "application/pdf",
    )
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    assert (manifest.source.attachments_expected, manifest.source.attachments_captured) == (1, 1)


def test_evidence_whose_bytes_do_not_hash_to_their_name_is_refused() -> None:
    with refused(RefusalReason.MANIFEST_INVALID):
        build(attachments=inventory(listing(sha(PDF))), raw={sha(PDF): ZIP})


def test_an_attachment_listed_on_a_page_outside_the_basis_is_refused() -> None:
    stray = AttachmentListing(url=PDF_URL, listed_on="e" * 64, raw_sha256=None)
    with pytest.raises(ValidationError):
        inventory(stray)


# --- publication completeness ------------------------------------------------------


def test_a_valid_fc3_extraction_alone_never_authorizes_publication() -> None:
    manifest = complete_source()
    assert manifest.source.state is SourceState.SOURCE_COMPLETE
    publication = manifest.publication
    assert publication.state is not PublicationState.PUBLICATION_COMPLETE
    assert publication.spans_publishable == 0
    assert ("publication_constraints_absent", "unknown") in codes(publication.reasons)
    assert manifest.cleared is False
    assert manifest.automatic_release_eligible is False


def test_official_text_candidates_still_lack_clearance_evidence() -> None:
    manifest = complete_source()
    official = {
        entry.id
        for part in manifest.content.parts
        for entry in part.entries
        if isinstance(entry, VersionSpan) and entry.rights.value == "official_text_candidate"
    }
    absent = {
        reason.entry_id
        for reason in manifest.publication.reasons
        if reason.code == "clearance_evidence_absent"
    }
    assert official
    assert absent == official


def test_quotations_annexes_and_privacy_hits_are_release_gaps() -> None:
    quoted = with_body(
        2, QUOTED.removeprefix(HEAD) + section("<p>Kontakt ola@example.invalid.</p>", "kap2-9")
    )
    manifest = build([*pages(1, 3, 4), quoted], inventory())
    publication = manifest.publication
    assert publication.state is PublicationState.PUBLICATION_INCOMPLETE
    found = codes(publication.reasons)
    assert {
        ("rights_unresolved", "incomplete"),
        ("third_party_work", "incomplete"),
        ("privacy_unresolved", "incomplete"),
    } <= found
    quotes = [reason for reason in publication.reasons if reason.code == "rights_unresolved"]
    assert {reason.part_id for reason in quotes} == {"regjeringen-9000001-ch2"}
    assert publication.release_gaps == len(
        [
            reason
            for reason in publication.reasons
            if reason.entry_id and reason.effect is Effect.INCOMPLETE
        ]
    )


def test_an_incomplete_source_makes_publication_incomplete_and_unknown_stays_unknown() -> None:
    assert ("source_not_complete", "incomplete") in codes(build(pages(1, 2)).publication.reasons)
    assert ("source_not_complete", "unknown") in codes(build().publication.reasons)


def test_missing_inventory_or_unpublishable_attachments_block_publication() -> None:
    assert ("attachment_inventory_missing", "unknown") in codes(build().publication.reasons)
    manifest = build(attachments=inventory(listing(None)))
    assert ("attachment_unpublishable", "incomplete") in codes(manifest.publication.reasons)
    assert manifest.publication.attachments_publishable == 0
    assert build().publication.attachments_publishable is None


def test_publication_axis_is_computed_apart_from_the_source_axis() -> None:
    manifest = complete_source()
    again = publication_completeness(manifest.content, manifest.source)
    assert again == manifest.publication


def test_publication_complete_without_source_complete_is_refused() -> None:
    manifest = build(pages(1, 2), inventory())
    claimed = manifest.publication.model_copy(
        update={"state": PublicationState.PUBLICATION_COMPLETE, "reasons": (), "release_gaps": 0}
    )
    with pytest.raises(ValidationError, match="requires source_complete"):
        VersionManifest.model_validate(
            {**manifest.model_dump(), "publication": claimed.model_dump()}
        )


def test_a_state_that_does_not_follow_from_its_reasons_is_refused() -> None:
    source = build().source.model_dump()
    with pytest.raises(ValidationError, match="does not follow"):
        type(build().source).model_validate({**source, "state": "source_complete"})


# --- identity, replay, tampering ---------------------------------------------------


def test_same_evidence_in_any_order_gives_the_same_bytes() -> None:
    forward = manifest_text(build(pages(), inventory()))
    backward = manifest_text(build(list(reversed(pages())), inventory()))
    assert forward == backward


def test_version_id_ignores_retrieval_time_and_follows_the_bytes() -> None:
    later = [item._replace(retrieved_at=WHEN + timedelta(days=3)) for item in pages()]
    first, second = complete_source(), build(later, inventory())
    assert first.version_id == second.version_id
    assert first.provenance != second.provenance
    changed = build([*pages(1, 3, 4), with_body(2, "<p>Ny tekst.</p>")], inventory())
    assert changed.version_id != first.version_id


def test_version_id_is_the_digest_of_the_content_and_the_content_names_no_version() -> None:
    manifest = complete_source()
    content = manifest.content.model_dump(mode="json")
    text = json.dumps(content, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert manifest.version_id == sha(text.encode())
    assert not any("version_id" in key or "release" in key for key in _keys(content))


def _keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_the_manifest_carries_digests_and_versions_never_the_text() -> None:
    manifest = complete_source()
    text = manifest_text(manifest)
    assert "Tekst i kapittel" not in text
    assert manifest.content.extractor_version == "regjeringen-span/1"
    assert manifest.content.classifier_version == "span-candidates/1"
    document = extract_document(pages())
    assert [page.raw_sha256 for page in manifest.provenance.pages] == [
        part.source_sha256 for part in document.parts
    ]


def test_parse_reads_back_its_own_bytes() -> None:
    manifest = complete_source()
    assert parse_version_manifest(manifest_text(manifest).encode()) == manifest


def _tampered(change: Any) -> bytes:
    payload = json.loads(manifest_text(complete_source()))
    change(payload)
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _flip_rights(payload: dict[str, Any]) -> None:
    payload["content"]["parts"][3]["entries"][0]["rights"] = "official_text_candidate"


def _flip_and_rehash(payload: dict[str, Any]) -> None:
    _flip_rights(payload)
    text = json.dumps(payload["content"], sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    payload["version_id"] = sha(text.encode())


def _claim_complete(payload: dict[str, Any]) -> None:
    payload["publication"].update(state="publication_complete", reasons=[], release_gaps=0)


@pytest.mark.parametrize("change", [_flip_rights, _flip_and_rehash, _claim_complete])
def test_a_hand_edited_manifest_is_refused(change: Any) -> None:
    with refused(RefusalReason.MANIFEST_INVALID):
        parse_version_manifest(_tampered(change))


def test_non_canonical_bytes_are_refused() -> None:
    raw = manifest_text(complete_source()).encode()
    with refused(RefusalReason.MANIFEST_INVALID):
        parse_version_manifest(raw.replace(b"\n", b"\r\n"))


def test_verify_rebuilds_from_the_evidence_and_refuses_other_bytes() -> None:
    raw = manifest_text(complete_source()).encode()
    assert verify_version_manifest(raw, Evidence(tuple(pages()), inventory(), {})).version_id
    other = [*pages(1, 3, 4), with_body(2, "<p>Ny tekst.</p>")]
    with refused(RefusalReason.MANIFEST_INVALID):
        verify_version_manifest(raw, Evidence(tuple(other), inventory(), {}))


def test_a_rehashed_but_rebuilt_manifest_still_fails_verification() -> None:
    raw = _tampered(_flip_and_rehash)
    with refused(RefusalReason.MANIFEST_INVALID):
        verify_version_manifest(raw, Evidence(tuple(pages()), inventory(), {}))


# --- schema compatibility ----------------------------------------------------------


def test_a_v3_manifest_reads_as_one_summary_with_both_axes() -> None:
    manifest = complete_source()
    (summary,) = read_version_summaries(manifest_text(manifest).encode())
    assert summary.schema_version == 3
    assert summary.version_id == manifest.version_id
    assert summary.source_state is SourceState.SOURCE_COMPLETE
    assert summary.publication_state is manifest.publication.state


@pytest.mark.parametrize("name", ["manifest.json", "../manifest-v2.json"])
def test_legacy_manifests_still_read_and_lift_to_never_complete(name: str) -> None:
    raw = (CORPUS / name).read_bytes()
    legacy = parse_any_manifest(raw)
    summaries = read_version_summaries(raw)
    assert [item.document_id for item in summaries] == [
        member.document.id for member in legacy.documents
    ]
    for summary in summaries:
        assert summary.version_id is None
        assert summary.source_state is SourceState.SOURCE_INCOMPLETE
        assert summary.publication_state is PublicationState.PUBLICATION_INCOMPLETE
        assert ("legacy_manifest", "incomplete") in codes(summary.reasons)


def test_an_unknown_schema_version_is_refused() -> None:
    with refused(RefusalReason.MANIFEST_INCOMPATIBLE):
        read_version_summaries(b'{"schema_version": 4}\n')


def test_the_single_page_fixture_still_builds() -> None:
    manifest = build([SourcePage(payload=page(), url=URL, retrieved_at=WHEN)])
    assert manifest.source.state is SourceState.SOURCE_INCOMPLETE
    assert manifest.source.parts_extracted == 1
    assert datetime(2100, 4, 1, 12, tzinfo=UTC) == manifest.provenance.pages[0].retrieved_at
