"""The forarbeider corpus manifest fails closed (#738, ADR-0018 PR3B-2, M1).

Membership is what the manifest lists, labels are fixed by type, times are
never invented, and anything ambiguous is refused rather than repaired.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import (
    EMPTY_MANIFEST,
    ForarbeiderManifest,
    ManifestDocument,
    manifest_text,
    member_files,
    parse_manifest,
    verify_members,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import digest
from tests.unit.forarbeider_corpus_support import DIGEST, manifest, member, times
from tests.unit.forarbeider_release_support import DECIDED, Desk


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path)


def _raw(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _refused(raw: bytes, reason: RefusalReason) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_manifest(raw)
    assert caught.value.reason is reason
    assert caught.value.detail == "manifest"


def test_a_member_round_trips_through_its_canonical_bytes(desk: Desk) -> None:
    raw = _raw(manifest(member(desk)))

    parsed = parse_manifest(raw)

    assert manifest_text(parsed).encode() == raw
    only = parsed.documents[0]
    assert (only.source_document_type, only.normative, only.source_license) == (
        "forarbeid",
        False,
        None,
    )
    assert only.rights_basis == "åndsverkloven § 14"
    assert only.source_provider == "regjeringen.no"
    assert only.path == "forarbeider/2099-2100/prop-l/9000001.md"
    assert only.coverage == "partial_curated_excerpt"
    assert only.asserts_complete_proposition is False


def test_the_empty_dataset_is_a_valid_manifest() -> None:
    assert parse_manifest(manifest_text(EMPTY_MANIFEST).encode()) == EMPTY_MANIFEST


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_license", "NLOD 2.0"),
        ("source_license", "CC0"),
        ("rights_basis", "NLOD 2.0"),
        ("normative", True),
        ("source_document_type", "lov"),
        ("source_provider", "lovdata.no"),
        ("asserts_complete_proposition", True),
    ],
)
def test_a_label_other_than_the_fixed_one_is_refused(desk: Desk, field: str, value: Any) -> None:
    _refused(_raw(manifest(member(desk, **{field: value}))), RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize("key", ["license", "valid_at", "in_force", "licence_url"])
def test_an_unknown_key_is_refused_so_it_cannot_carry_a_claim(desk: Desk, key: str) -> None:
    _refused(_raw(manifest(member(desk, **{key: "x"}))), RefusalReason.MANIFEST_INVALID)


def test_a_document_listed_twice_is_refused(desk: Desk) -> None:
    payload = manifest(member(desk))
    payload["documents"] = [*payload["documents"], *payload["documents"]]

    _refused(_raw(payload), RefusalReason.MANIFEST_INVALID)


def test_a_key_given_twice_is_refused(desk: Desk) -> None:
    raw = _raw(manifest(member(desk))).replace(
        b'"dataset": "forarbeider",', b'"dataset": "forarbeider",\n  "dataset": "forarbeider",'
    )

    _refused(raw, RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize(
    "path",
    [
        "forarbeider/2099-2100/prop-l/../../manifest.json",
        "../lover/9000001.md",
        "/forarbeider/2099-2100/prop-l/9000001.md",
        "forarbeider/2099-2100/prop-ls/9000001.md",
        "forarbeider/2099-2100/prop-l/9000002.md",
        "lover/2099-2100/prop-l/9000001.md",
        "forarbeider/2099-2100/prop-l/9000001.md\n",
    ],
)
def test_a_path_other_than_the_scoped_one_is_refused(desk: Desk, path: str) -> None:
    _refused(_raw(manifest(member(desk, path=path))), RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize("document_type", ["nou", "prop-s", "otprp", "rundskriv"])
def test_an_unsupported_document_type_is_refused(desk: Desk, document_type: str) -> None:
    built = member(desk)
    built["document"] = {**built["document"], "document_type": document_type}

    _refused(_raw(manifest(built)), RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_another_schema_version_is_incompatible(desk: Desk, version: Any) -> None:
    payload = {**manifest(member(desk)), "schema_version": version}

    _refused(_raw(payload), RefusalReason.MANIFEST_INCOMPATIBLE)


@pytest.mark.parametrize("raw", [b"", b"[]", b"\xff", b"{", b'"manifest"'])
def test_bytes_that_are_not_a_manifest_object_are_refused(raw: bytes) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_manifest(raw)
    assert caught.value.reason in {
        RefusalReason.MANIFEST_INVALID,
        RefusalReason.MANIFEST_INCOMPATIBLE,
    }


def test_bytes_not_as_written_are_refused_though_they_parse(desk: Desk) -> None:
    payload = manifest(member(desk))
    compact = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()

    _refused(compact, RefusalReason.MANIFEST_INVALID)


def _receipts_changed(desk: Desk, **changes: Any) -> dict[str, Any]:
    built = member(desk)
    built["chapters"] = [{**built["chapters"][0], **changes}, built["chapters"][1]]
    return built


@pytest.mark.parametrize(
    "changes",
    [
        {"chapter_id": "regjeringen-9000002-ch2"},
        {"chapter_id": "regjeringen-9000001-ch3"},
        {"chapter_number": 4},
        {"source_page_url": "https://www.regjeringen.no/no/dokumenter/x/id1/?ch=2"},
        {"text_sha256": "not-a-digest"},
        {"extractor_version": 2},
        {"retrieved_at": "2100-04-01T12:00:00"},
        {"review_decided_at": "2100-04-05T12:00:00Z"},
    ],
)
def test_a_receipt_that_contradicts_its_document_is_refused(
    desk: Desk, changes: dict[str, Any]
) -> None:
    _refused(_raw(manifest(_receipts_changed(desk, **changes))), RefusalReason.MANIFEST_INVALID)


def test_chapters_out_of_source_order_are_refused(desk: Desk) -> None:
    built = member(desk)
    built["chapters"] = list(reversed(built["chapters"]))

    _refused(_raw(manifest(built)), RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize(
    "changes",
    [
        {"coverage": "every_listed_chapter"},
        {"coverage": "complete"},
        {"listed_chapters": [1, 2]},
        {"listed_chapters": [2, 3]},
        {"listed_chapters": []},
        {"chapters": []},
    ],
)
def test_coverage_never_overstates_the_released_chapters(
    desk: Desk, changes: dict[str, Any]
) -> None:
    _refused(_raw(manifest(member(desk, **changes))), RefusalReason.MANIFEST_INVALID)


def test_every_listed_chapter_is_still_not_a_complete_proposition(desk: Desk) -> None:
    built = member(desk, listed_chapters=[1, 2, 3], coverage="every_listed_chapter")
    built["chapters"] = [{**built["chapters"][0], "chapter_number": 1}]

    with pytest.raises(ValidationError):
        ManifestDocument.model_validate(built)


@pytest.mark.parametrize(
    "changes",
    [
        {"released_at": "2100-04-03T00:00:00Z"},
        {"owner_decided_at": "2100-04-02T00:00:00Z"},
        {"withdrawn_at": "2100-04-09T00:00:00Z"},
        {"time_basis": "authenticated"},
        {"recorded_at": "2100-04-10T12:00:00Z"},
        {"released_at": None},
        {"released_at": "2100-04-10T12:00:00"},
    ],
)
def test_times_out_of_order_or_invented_are_refused(desk: Desk, changes: dict[str, Any]) -> None:
    built = member(desk, times={**times(), **changes})
    if "withdrawn_at" in changes:
        built = {**built, "release_status": "withdrawn", "markdown_sha256": None}

    _refused(_raw(manifest(built)), RefusalReason.MANIFEST_INVALID)


def test_times_on_another_offset_are_kept_as_the_same_instant(desk: Desk) -> None:
    shifted = {**times(), "owner_decided_at": "2100-04-04T14:00:00+02:00"}

    parsed = ManifestDocument.model_validate(member(desk, times=shifted))

    assert parsed.times.owner_decided_at.isoformat() == DECIDED.replace("Z", "+00:00")


@pytest.mark.parametrize(
    "changes",
    [
        {"release_status": "withdrawn"},
        {"markdown_sha256": None},
        {"times": times("2100-04-11T00:00:00Z")},
        {"release_status": "withdrawn", "times": times("2100-04-11T00:00:00Z")},
        {"release_status": "adopted"},
    ],
)
def test_release_status_states_exactly_one_thing(desk: Desk, changes: dict[str, Any]) -> None:
    _refused(_raw(manifest(member(desk, **changes))), RefusalReason.MANIFEST_INVALID)


def test_a_withdrawn_member_keeps_its_receipts_and_names_no_file(desk: Desk) -> None:
    withdrawn = member(
        desk,
        release_status="withdrawn",
        markdown_sha256=None,
        times=times("2100-04-11T00:00:00Z"),
    )

    parsed = ForarbeiderManifest.model_validate(manifest(withdrawn))

    assert member_files(parsed) == {}
    assert len(parsed.documents[0].chapters) == 2


def _with_rendering(desk: Desk, payload: bytes) -> ForarbeiderManifest:
    return ForarbeiderManifest.model_validate(
        manifest(member(desk, markdown_sha256=digest(payload)))
    )


def test_members_match_the_files_exactly(desk: Desk) -> None:
    parsed = _with_rendering(desk, b"rendered\n")

    verify_members(parsed, {"forarbeider/2099-2100/prop-l/9000001.md": b"rendered\n"})


@pytest.mark.parametrize(
    ("files", "shown"),
    [
        ({}, "forarbeider/2099-2100/prop-l/9000001.md"),
        (
            {"forarbeider/2099-2100/prop-l/9000001.md": b"edited\n"},
            "forarbeider/2099-2100/prop-l/9000001.md",
        ),
        (
            {
                "forarbeider/2099-2100/prop-l/9000001.md": b"rendered\n",
                "forarbeider/2099-2100/prop-l/9000002.md": b"stray\n",
            },
            "forarbeider/2099-2100/prop-l/9000002.md",
        ),
        (
            {
                "forarbeider/2099-2100/prop-l/9000001.md": b"rendered\n",
                "forarbeider/notes.txt": b"x",
            },
            "forarbeider",
        ),
    ],
)
def test_a_dangling_member_or_a_stray_file_is_refused(
    desk: Desk, files: dict[str, bytes], shown: str
) -> None:
    parsed = _with_rendering(desk, b"rendered\n")

    with pytest.raises(ForarbeiderRefusedError) as caught:
        verify_members(parsed, files)

    assert caught.value.reason is RefusalReason.CORPUS_TREE_MISMATCH
    assert caught.value.detail == shown


def test_a_withdrawn_member_whose_file_remains_is_refused(desk: Desk) -> None:
    withdrawn = member(
        desk, release_status="withdrawn", markdown_sha256=None, times=times("2100-04-11T00:00:00Z")
    )
    parsed = ForarbeiderManifest.model_validate(manifest(withdrawn))

    with pytest.raises(ForarbeiderRefusedError):
        verify_members(parsed, {"forarbeider/2099-2100/prop-l/9000001.md": b"rendered\n"})


def test_two_documents_are_sorted_by_path_and_distinct(tmp_path: Path) -> None:
    first = member(Desk(tmp_path / "l"))
    second = member(Desk(tmp_path / "ls", kind="ls"))

    parsed = ForarbeiderManifest.model_validate(manifest(first, second))

    assert [item.document.document_type for item in parsed.documents] == ["prop-l", "prop-ls"]
    with pytest.raises(ValidationError):
        ForarbeiderManifest.model_validate({**manifest(), "documents": [second, first]})
    assert DIGEST not in manifest_text(EMPTY_MANIFEST)


@pytest.mark.parametrize(
    ("field", "value"),
    [("session", "2098-2099"), ("source_document_id", 9000002), ("id", "regjeringen-9000002")],
)
def test_a_member_whose_identity_disagrees_with_its_path_or_chapters_is_refused(
    desk: Desk, field: str, value: Any
) -> None:
    built = member(desk)
    built["document"] = {**built["document"], field: value}

    _refused(_raw(manifest(built)), RefusalReason.MANIFEST_INVALID)
