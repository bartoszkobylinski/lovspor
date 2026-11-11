"""The sandbox reader reads manifest versions 1 and 2 through one parser (#738, A2b).

A version 1 sandbox, as the simulator writes it, is served with each chapter
lifted to ``owner_review`` under ``provenance: manifest_v1``. A version 2
manifest is served as it states its clearances, and its members are still
proved against the bundled synthetic pages: a policy clearance never makes a
forged receipt servable.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.corpus_models import (
    MANIFEST_PATH,
    ForarbeiderManifestV2,
    PolicyClearance,
    ReviewClearance,
    manifest_v2_text,
    parse_any_manifest,
)
from lovspor.forarbeider.corpus_reader import _restated, _review_time, synthetic_rendering
from lovspor.forarbeider.release_models import digest
from tests.unit.cli_output import said
from tests.unit.forarbeider_reader_support import L_ID, exact, loaded, read, released_box
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")

POLICY = {
    "cleared_by": "standing_policy",
    "policy_sha256": "b" * 64,
    "source_proof_sha256": "c" * 64,
    "individually_reviewed": False,
}
POLICY_AT = "2100-04-03T18:00:00Z"


@pytest.fixture
def box(tmp_path: Path) -> Path:
    return released_box(Desk(tmp_path / "l"), tmp_path / "sandbox")


def _rewrite(box: Path, edit: Callable[[dict[str, Any]], None]) -> bytes:
    """The sandbox's manifest lifted to version 2, ``edit`` applied, written canonically."""
    payload = json.loads(manifest_v2_text(parse_any_manifest((box / MANIFEST_PATH).read_bytes())))
    edit(payload)
    raw = manifest_v2_text(ForarbeiderManifestV2.model_validate(payload)).encode()
    (box / MANIFEST_PATH).write_bytes(raw)
    return raw


def _policy_for_third(payload: dict[str, Any]) -> None:
    document = payload["documents"][0]
    document["chapters"][1]["clearance"] = dict(POLICY)
    document["times"]["policy_decided_at"] = POLICY_AT


def test_a_v1_sandbox_is_served_with_lifted_owner_review_clearances(box: Path) -> None:
    shown = loaded(read(box, "show", *exact(L_ID)))

    assert shown["schema_version"] == 2
    assert shown["times"]["policy_decided_at"] is None
    assert shown["times"]["reviewed_at"] is not None
    for receipt in shown["chapters"]:
        clearance = receipt["clearance"]
        assert clearance["cleared_by"] == "owner_review"
        assert clearance["provenance"] == "manifest_v1"
        assert clearance["review"] is None
        assert clearance["individually_reviewed"] is True
        assert "review_decided_at" not in receipt


def test_a_v2_sandbox_serves_each_chapter_with_its_stated_clearance(box: Path) -> None:
    raw = _rewrite(box, _policy_for_third)

    listing = loaded(read(box, "list"))
    shown = loaded(read(box, "show", *exact(L_ID)))
    provenance = loaded(read(box, "provenance", *exact(L_ID)))
    chapter = loaded(read(box, "chapter", *exact(L_ID), "--chapter", "3"))

    assert listing["manifest_sha256"] == digest(raw)
    clearances = {item["chapter_number"]: item["clearance"] for item in shown["chapters"]}
    assert clearances[3] == POLICY
    assert clearances[2]["cleared_by"] == "owner_review"
    assert shown["times"]["policy_decided_at"] == POLICY_AT
    assert shown["labels"]["normative"] is False
    assert shown["labels"]["source_license"] is None
    assert provenance["schema_version"] == 2
    assert {item["chapter_number"] for item in provenance["chapters"]} == {2, 3}
    assert chapter["schema_version"] == 2
    assert chapter["chapter"]["clearance"] == POLICY
    assert chapter["markdown"].startswith("Source: <https://www.regjeringen.no/")


def test_a_v2_document_cleared_only_by_policy_has_no_review_time(box: Path) -> None:
    def all_policy(payload: dict[str, Any]) -> None:
        document = payload["documents"][0]
        for receipt in document["chapters"]:
            receipt["clearance"] = dict(POLICY)
        document["times"]["policy_decided_at"] = POLICY_AT
        document["times"]["reviewed_at"] = None

    _rewrite(box, all_policy)
    shown = loaded(read(box, "show", *exact(L_ID)))

    assert shown["times"]["reviewed_at"] is None
    assert {item["clearance"]["cleared_by"] for item in shown["chapters"]} == {"standing_policy"}


def test_a_forged_v2_receipt_is_not_synthetic_whatever_clears_it(box: Path) -> None:
    def forged(payload: dict[str, Any]) -> None:
        _policy_for_third(payload)
        payload["documents"][0]["chapters"][1]["text_sha256"] = "9" * 64

    _rewrite(box, forged)
    result = read(box, "show", *exact(L_ID))

    assert result.exit_code == 1
    assert said("refused: input_not_synthetic: document", result.output)


def test_a_v2_entry_restating_another_listing_is_not_synthetic(box: Path) -> None:
    def relisted(payload: dict[str, Any]) -> None:
        payload["documents"][0]["ministry"] = "Et annet departement"
        payload["documents"][0]["chapters"][0]["chapter_title"] = "Et annet kapittel"

    _rewrite(box, relisted)
    result = read(box, "list")

    assert result.exit_code == 1
    assert said("refused: input_not_synthetic: document", result.output)


def test_a_v2_review_after_the_owner_decision_is_refused_as_invalid(box: Path) -> None:
    payload = json.loads(manifest_v2_text(parse_any_manifest((box / MANIFEST_PATH).read_bytes())))
    payload["documents"][0]["chapters"][0]["clearance"]["review_decided_at"] = (
        "2100-04-05T12:00:00Z"
    )
    (box / MANIFEST_PATH).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")

    result = read(box, "list")

    assert result.exit_code == 1
    assert said("refused: manifest_invalid: manifest", result.output)


def test_another_manifest_version_is_incompatible_for_the_reader(box: Path) -> None:
    (box / MANIFEST_PATH).write_bytes(b'{"schema_version": 3}\n')

    result = read(box, "list")

    assert result.exit_code == 1
    assert said("refused: manifest_incompatible: manifest", result.output)


def test_review_time_is_the_review_decided_at_only_for_a_review_clearance() -> None:
    policy = PolicyClearance(
        cleared_by="standing_policy",
        policy_sha256="b" * 64,
        source_proof_sha256="c" * 64,
        individually_reviewed=False,
    )
    review = ReviewClearance.model_validate(
        {
            "cleared_by": "owner_review",
            "review_decided_at": "2100-04-02T12:00:00Z",
            "review": {"subject_key": "d" * 64, "revision": 1, "head_sha256": "e" * 64},
            "authorization_sha256": "f" * 64,
            "report_sha256": "0" * 64,
            "provenance": "recorded",
            "individually_reviewed": True,
        }
    )

    assert _review_time(policy) is None
    assert _review_time(review) == review.review_decided_at


def test_synthetic_rendering_carries_review_times_and_always_drops_withdrawal(
    box: Path,
) -> None:
    entry = parse_any_manifest((box / MANIFEST_PATH).read_bytes()).documents[0]
    withdrawn_entry = entry.model_copy(
        update={"times": entry.times.model_copy(update={"withdrawn_at": "2100-04-11T00:00:00Z"})}
    )

    rendering = synthetic_rendering(entry)
    withdrawn_rendering = synthetic_rendering(withdrawn_entry)

    assert len(rendering.chapters) == len(entry.chapters)
    for chapter, source in zip(rendering.chapters, entry.chapters, strict=True):
        assert chapter.review_decided_at == source.clearance.review_decided_at
        assert chapter.review_decided_at is not None
    assert rendering.times.withdrawn_at is None
    assert withdrawn_rendering.times.withdrawn_at is None


def test_restated_requires_the_same_number_of_chapters_as_rendered(box: Path) -> None:
    entry = parse_any_manifest((box / MANIFEST_PATH).read_bytes()).documents[0]
    rendering = synthetic_rendering(entry)
    assert len(entry.chapters) > 1
    short_rendering = rendering._replace(chapters=rendering.chapters[:-1])

    with pytest.raises(ValueError, match=r"zip\(\)"):
        _restated(entry, short_rendering)
