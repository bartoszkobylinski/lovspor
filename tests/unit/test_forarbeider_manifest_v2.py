"""The forarbeider corpus manifest, schema 2: who cleared each chapter, by type (#738, A2b).

Every chapter states its clearance path. ``standing_policy`` carries the
policy and source-proof digests and no review field; ``owner_review`` carries
its review provenance and no policy digest. A version 1 manifest is lifted,
never reinterpreted: v1 was written only by the per-chapter path, so each
chapter becomes ``owner_review`` with the one review time v1 recorded, and the
pin, authorization and report it never recorded stay explicit ``null`` under
``provenance: manifest_v1``. ``parse_manifest`` stays version 1 only.
"""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import (
    ChapterReceiptV2,
    ForarbeiderManifestV2,
    ManifestDocumentV2,
    PolicyClearance,
    ReviewClearance,
    lift_v1,
    manifest_v2_text,
    parse_any_manifest,
    parse_manifest,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

CORPUS = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "corpus"
GOLDEN_V1 = CORPUS / "manifest.json"
GOLDEN_V2 = CORPUS.parent / "manifest-v2.json"
POLICY = "b" * 64
PROOF = "c" * 64
PIN = {"subject_key": "d" * 64, "revision": 2, "head_sha256": "e" * 64}


def _raw(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _golden() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(GOLDEN_V2.read_text(encoding="utf-8"))
    return loaded


def _refused(raw: bytes, reason: RefusalReason) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_any_manifest(raw)
    assert caught.value.reason is reason
    assert caught.value.detail == "manifest"


def _invalid(payload: dict[str, Any]) -> None:
    _refused(_raw(payload), RefusalReason.MANIFEST_INVALID)


def _policy_clearance(**changes: Any) -> dict[str, Any]:
    fields = {
        "cleared_by": "standing_policy",
        "policy_sha256": POLICY,
        "source_proof_sha256": PROOF,
        "individually_reviewed": False,
    }
    return {**fields, **changes}


def _review_clearance(**changes: Any) -> dict[str, Any]:
    fields = {
        "cleared_by": "owner_review",
        "review_decided_at": "2100-04-02T12:00:00Z",
        "review": PIN,
        "authorization_sha256": "f" * 64,
        "report_sha256": "0" * 64,
        "provenance": "recorded",
        "individually_reviewed": True,
    }
    return {**fields, **changes}


def test_the_v2_golden_round_trips_byte_exact() -> None:
    raw = GOLDEN_V2.read_bytes()

    parsed = parse_any_manifest(raw)

    assert manifest_v2_text(parsed).encode() == raw
    first, second = parsed.documents
    assert isinstance(first.chapters[0].clearance, ReviewClearance)
    assert isinstance(first.chapters[1].clearance, PolicyClearance)
    assert isinstance(second.chapters[0].clearance, PolicyClearance)
    assert second.times.reviewed_at is None
    assert first.times.policy_decided_at is not None


def test_the_serializer_gives_the_same_bytes_for_the_same_input_in_any_key_order() -> None:
    payload = _golden()
    shuffled = json.loads(json.dumps(payload), object_pairs_hook=lambda pairs: dict(pairs[::-1]))

    first = manifest_v2_text(ForarbeiderManifestV2.model_validate(payload))
    second = manifest_v2_text(ForarbeiderManifestV2.model_validate(shuffled))
    third = manifest_v2_text(parse_any_manifest(first.encode()))

    assert first == second == third
    assert first.endswith("}\n")
    assert first.encode() == GOLDEN_V2.read_bytes()


def test_a_different_input_gives_different_bytes() -> None:
    changed = _golden()
    changed["documents"][1]["chapters"][0]["clearance"]["policy_sha256"] = "1" * 64

    before = manifest_v2_text(ForarbeiderManifestV2.model_validate(_golden()))
    after = manifest_v2_text(ForarbeiderManifestV2.model_validate(changed))

    assert before != after


def test_a_v1_manifest_is_lifted_with_owner_review_and_explicit_legacy_nulls() -> None:
    raw = GOLDEN_V1.read_bytes()
    original = parse_manifest(raw)

    lifted = parse_any_manifest(raw)

    assert lifted == lift_v1(original)
    assert lifted.schema_version == 2
    assert len(lifted.documents) == len(original.documents)
    for old, new in zip(original.documents, lifted.documents, strict=True):
        assert new.renderer_version == original.renderer_version
        assert new.times.policy_decided_at is None
        assert new.times.reviewed_at == old.times.reviewed_at
        assert new.times.owner_decided_at == old.times.owner_decided_at
        assert new.times.released_at == old.times.released_at
        assert (new.normative, new.source_license) == (False, None)
        assert new.rights_basis == "åndsverkloven § 14"
        for before, after in zip(old.chapters, new.chapters, strict=True):
            clearance = after.clearance
            assert isinstance(clearance, ReviewClearance)
            assert clearance.provenance == "manifest_v1"
            assert clearance.review_decided_at == before.review_decided_at
            assert (clearance.review, clearance.authorization_sha256) == (None, None)
            assert clearance.report_sha256 is None
            assert clearance.individually_reviewed is True
            assert after.text_sha256 == before.text_sha256
            assert after.source_sha256 == before.source_sha256


def test_the_empty_v1_manifest_lifts_to_an_empty_v2_one() -> None:
    lifted = parse_any_manifest((CORPUS / "manifest-empty.json").read_bytes())

    assert lifted.documents == ()
    assert manifest_v2_text(lifted).startswith('{\n  "dataset": "forarbeider"')


def test_parse_manifest_still_refuses_version_2() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        parse_manifest(GOLDEN_V2.read_bytes())

    assert caught.value.reason is RefusalReason.MANIFEST_INCOMPATIBLE


@pytest.mark.parametrize("version", [0, 3, "2", None])
def test_another_version_is_incompatible(version: Any) -> None:
    _refused(_raw({**_golden(), "schema_version": version}), RefusalReason.MANIFEST_INCOMPATIBLE)


@pytest.mark.parametrize("raw", [b"", b"\xff", b"{", b'{"a": 1, "a": 2}'])
def test_bytes_that_are_not_json_are_invalid(raw: bytes) -> None:
    _refused(raw, RefusalReason.MANIFEST_INVALID)


@pytest.mark.parametrize("raw", [b"[]", b'"manifest"'])
def test_json_that_is_not_an_object_is_incompatible(raw: bytes) -> None:
    _refused(raw, RefusalReason.MANIFEST_INCOMPATIBLE)


def test_a_bare_version_2_is_invalid_not_incompatible() -> None:
    _refused(b'{"schema_version": 2}\n', RefusalReason.MANIFEST_INVALID)


def test_v2_bytes_not_as_written_are_refused_though_they_parse() -> None:
    compact = json.dumps(_golden(), sort_keys=True, ensure_ascii=False).encode()

    _refused(compact, RefusalReason.MANIFEST_INVALID)


def test_documents_out_of_path_order_are_invalid() -> None:
    payload = _golden()
    payload["documents"] = payload["documents"][::-1]

    _invalid(payload)


def _with_clearance(clearance: dict[str, Any], document: int = 1, chapter: int = 0) -> None:
    payload = _golden()
    payload["documents"][document]["chapters"][chapter]["clearance"] = clearance
    _invalid(payload)


@pytest.mark.parametrize(
    "clearance",
    [
        _policy_clearance(individually_reviewed=True),
        _policy_clearance(review_decided_at="2100-04-02T12:00:00Z"),
        _policy_clearance(review=PIN),
        _policy_clearance(authorization_sha256="f" * 64),
        _policy_clearance(report_sha256="0" * 64),
        _policy_clearance(policy_sha256=None),
        _policy_clearance(source_proof_sha256="not-a-digest"),
        {k: v for k, v in _policy_clearance().items() if k != "policy_sha256"},
        _policy_clearance(cleared_by="automatic"),
    ],
)
def test_a_policy_clearance_carries_its_digests_and_no_review_field(
    clearance: dict[str, Any],
) -> None:
    _with_clearance(clearance)


@pytest.mark.parametrize(
    "clearance",
    [
        _review_clearance(policy_sha256=POLICY),
        _review_clearance(source_proof_sha256=PROOF),
        _review_clearance(individually_reviewed=False),
        _review_clearance(review=None),
        _review_clearance(authorization_sha256=None),
        _review_clearance(report_sha256=None),
        _review_clearance(provenance="manifest_v1"),
        _review_clearance(provenance="guessed"),
        _review_clearance(review_decided_at="2100-04-02T12:00:00"),
    ],
)
def test_a_review_clearance_carries_review_provenance_and_no_policy_digest(
    clearance: dict[str, Any],
) -> None:
    _with_clearance(clearance, document=0, chapter=0)


def test_a_legacy_review_clearance_has_every_unrecorded_reference_null() -> None:
    legacy = _review_clearance(
        provenance="manifest_v1", review=None, authorization_sha256=None, report_sha256=None
    )
    ReviewClearance.model_validate(legacy)

    for field, value in (("review", PIN), ("authorization_sha256", "f" * 64)):
        with pytest.raises(ValidationError):
            ReviewClearance.model_validate({**legacy, field: value})
    with pytest.raises(ValidationError):
        ReviewClearance.model_validate({**legacy, "report_sha256": "0" * 64})


def test_a_recorded_review_clearance_is_valid_with_all_three_references() -> None:
    clearance = ReviewClearance.model_validate(_review_clearance())

    assert clearance.review is not None
    assert clearance.provenance == "recorded"


def _times(document: int, **changes: Any) -> None:
    payload = _golden()
    payload["documents"][document]["times"].update(changes)
    _invalid(payload)


def test_review_time_is_null_exactly_when_no_chapter_was_reviewed() -> None:
    _times(1, reviewed_at="2100-04-03T12:00:00Z")
    _times(0, reviewed_at=None)


def test_policy_time_is_null_exactly_when_no_chapter_was_policy_cleared() -> None:
    _times(0, policy_decided_at=None)
    _times(1, policy_decided_at=None)


def test_a_policy_decided_after_the_owner_decision_is_out_of_order() -> None:
    _times(1, policy_decided_at="2100-04-05T12:00:00Z")


def test_a_review_after_the_owner_decision_is_out_of_order() -> None:
    _times(0, reviewed_at="2100-04-05T12:00:00Z")
    payload = _golden()
    payload["documents"][0]["chapters"][0]["clearance"]["review_decided_at"] = (
        "2100-04-05T12:00:00Z"
    )
    _invalid(payload)


def test_release_before_the_owner_decision_or_withdrawal_before_release_is_invalid() -> None:
    _times(1, released_at="2100-04-03T12:00:00Z")
    payload = _golden()
    payload["documents"][1]["release_status"] = "withdrawn"
    payload["documents"][1]["markdown_sha256"] = None
    payload["documents"][1]["times"]["withdrawn_at"] = "2100-04-09T12:00:00Z"
    _invalid(payload)


def test_the_distinct_time_axes_survive_a_round_trip() -> None:
    parsed = parse_any_manifest(GOLDEN_V2.read_bytes())
    times = parsed.documents[0].times

    moments = {times.reviewed_at, times.policy_decided_at, times.owner_decided_at}
    assert len(moments | {times.released_at}) == 4
    assert times.time_basis == "operator_supplied_unauthenticated"
    assert times.recorded_at == "git_commit_time"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("normative", True),
        ("source_license", "NLOD 2.0"),
        ("rights_basis", "NLOD"),
        ("source_document_type", "lov"),
        ("asserts_complete_proposition", True),
        ("renderer_version", 2),
        ("path", "forarbeider/2099-2100/prop-l/1.md"),
        ("coverage", "every_listed_chapter"),
    ],
)
def test_a_label_that_is_fixed_by_type_cannot_change(field: str, value: Any) -> None:
    payload = _golden()
    payload["documents"][1][field] = value

    _invalid(payload)


def test_a_chapter_of_another_document_is_invalid() -> None:
    payload = _golden()
    foreign = copy.deepcopy(payload["documents"][0]["chapters"][1])
    payload["documents"][1]["chapters"].insert(0, foreign)

    _invalid(payload)


def test_models_are_frozen_and_strict() -> None:
    receipt = ChapterReceiptV2.model_validate(_golden()["documents"][1]["chapters"][0])
    member = ManifestDocumentV2.model_validate(_golden()["documents"][1])

    with pytest.raises(ValidationError):
        receipt.model_validate({**receipt.model_dump(), "review_decided_at": None})
    assert member.renderer_version == 1
