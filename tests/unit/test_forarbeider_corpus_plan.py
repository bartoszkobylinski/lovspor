"""Plan records, drafts and the transaction's edges (#738, ADR-0018 PR3B-2).

The CLI tests drive the whole path; these pin what each part refuses on its
own, including the states only a damaged or forged workspace could reach.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import corpus_transaction
from lovspor.forarbeider.corpus_commands import Verified, rendering_of
from lovspor.forarbeider.corpus_models import (
    INDEX_PATH,
    MANIFEST_PATH,
    ForarbeiderManifest,
)
from lovspor.forarbeider.corpus_plan import PromotionPlan, withdraw_draft
from lovspor.forarbeider.corpus_transaction import JOURNAL, STAGING, Journal, Operation, recover
from lovspor.forarbeider.corpus_tree import Dataset, create_sandbox
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import PreflightReport, digest
from lovspor.forarbeider.release_preflight import Observed, PreflightInputs
from lovspor.forarbeider.release_store import write_record
from tests.unit.cli_output import said
from tests.unit.forarbeider_corpus_support import manifest, member, metadata_of
from tests.unit.forarbeider_promotion_support import planned, sandbox, simulate
from tests.unit.forarbeider_release_support import Desk

DIGEST = "b" * 64
DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"
_AT = datetime(2100, 4, 12, tzinfo=UTC)


def _file(path: str) -> dict[str, Any]:
    return {"path": path, "sha256": DIGEST, "size_bytes": 1}


def _plan(**changes: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "forarbeider_promotion_plan",
        "action": "release",
        "document": {
            "id": "regjeringen-9000001",
            "document_type": "prop-l",
            "session": "2099-2100",
            "proposition_number": 901,
            "source_document_id": 9000001,
            "source_url": "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/",
        },
        "report_sha256": DIGEST,
        "authorization_sha256": DIGEST,
        "checkpoints": [],
        "at": "2100-04-10T12:00:00Z",
        "base_manifest_sha256": None,
        "writes": [_file(DOCUMENT), _file(INDEX_PATH), _file(MANIFEST_PATH)],
        "removes": [],
        "renderer_version": 1,
        "synthetic_inputs": True,
        "publication_authorized": False,
        "writes_corpus": False,
        "legal_sufficiency_decided_by_code": False,
        **changes,
    }


def test_a_plan_of_the_dataset_files_is_valid() -> None:
    assert PromotionPlan.model_validate(_plan()).action == "release"


@pytest.mark.parametrize(
    "changes",
    [
        {"writes": [_file(INDEX_PATH), _file("manifest.json"), _file(MANIFEST_PATH)]},
        {"writes": [_file(INDEX_PATH), _file("lover/x.md"), _file(MANIFEST_PATH)]},
        {"writes": [_file(INDEX_PATH), _file(INDEX_PATH), _file(MANIFEST_PATH)]},
        {"writes": [_file(MANIFEST_PATH), _file(INDEX_PATH)]},
        {"writes": [_file(DOCUMENT), _file(MANIFEST_PATH)]},
        {"removes": ["forarbeider/manifest.json"]},
        {"removes": [DOCUMENT]},
        {"removes": ["forarbeider/2099-2100/prop-l/9000002.md"] * 2},
        {"report_sha256": None},
        {"action": "withdraw"},
        {"publication_authorized": True},
        {"writes_corpus": True},
        {"at": "2100-04-10T12:00:00"},
    ],
)
def test_a_plan_outside_its_bounds_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PromotionPlan.model_validate(_plan(**changes))


def test_a_withdrawal_plan_rests_on_no_report() -> None:
    plan = _plan(
        action="withdraw",
        report_sha256=None,
        authorization_sha256=None,
        writes=[_file(INDEX_PATH), _file(MANIFEST_PATH)],
        removes=[DOCUMENT],
    )

    assert PromotionPlan.model_validate(plan).removes == (DOCUMENT,)


def test_a_withdrawal_names_no_unknown_document(tmp_path: Path) -> None:
    dataset = Dataset(ForarbeiderManifest.model_validate(manifest(member(Desk(tmp_path)))), None)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        withdraw_draft(dataset, "regjeringen-1", _AT)

    assert caught.value.reason is RefusalReason.DOCUMENT_NOT_RELEASED


def test_a_member_whose_chapters_are_under_another_url_is_refused(tmp_path: Path) -> None:
    built = member(Desk(tmp_path))
    built["document"] = {**built["document"], "source_url": built["document"]["source_url"] + "x/"}

    with pytest.raises(ValidationError):
        ForarbeiderManifest.model_validate(manifest(built))


def test_two_documents_with_one_source_id_are_refused(tmp_path: Path) -> None:
    first = member(Desk(tmp_path))
    second = {**first, "path": "forarbeider/2099-2100/prop-ls/9000001.md"}
    second["document"] = {**first["document"], "document_type": "prop-ls"}

    with pytest.raises(ValidationError):
        ForarbeiderManifest.model_validate(manifest(first, second))


def test_a_plan_without_its_dossier_renders_nothing(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    report = PreflightReport.model_construct(planned_chapters=(2,))
    verified = Verified(_inputs_without_dossier(desk), report, DIGEST)

    with pytest.raises(ForarbeiderRefusedError) as caught:
        rendering_of(verified)

    assert (caught.value.reason, caught.value.detail) == (RefusalReason.PROMOTION_DENIED, "report")


def _inputs_without_dossier(desk: Desk) -> PreflightInputs:
    return PreflightInputs(
        metadata=metadata_of(desk),
        metadata_page=None,
        observed=(Observed(desk.results[2], None, None),),
        dossier=None,
        evidence={},
        authorization=None,
        revoked=False,
        checkpoints={},
        at=_AT,
    )


@pytest.fixture
def box(tmp_path: Path) -> Path:
    return create_sandbox(tmp_path / "sandbox", ())


def _journal(box: Path, *operations: Operation) -> None:
    journal = Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        target_manifest_sha256=DIGEST,
        operations=operations,
    )
    (box / JOURNAL).write_text(journal.model_dump_json())


def test_recovery_refuses_staged_bytes_that_are_not_their_digest(box: Path) -> None:
    (box / STAGING).mkdir()
    (box / STAGING / DIGEST).write_bytes(b"not those bytes")
    _journal(
        box, Operation(path=DOCUMENT, sha256=DIGEST), Operation(path=INDEX_PATH, sha256=DIGEST)
    )

    with pytest.raises(ForarbeiderRefusedError) as caught:
        recover(box)

    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_TREE_MISMATCH,
        "staging",
    )
    assert not (box / DOCUMENT).exists()


def test_recovery_never_writes_through_a_symlinked_directory(box: Path, tmp_path: Path) -> None:
    payload = b"x\n"
    (box / STAGING).mkdir()
    (box / STAGING / digest(payload)).write_bytes(payload)
    (tmp_path / "elsewhere").mkdir()
    (box / "forarbeider").symlink_to(tmp_path / "elsewhere")
    operation = Operation(path=DOCUMENT, sha256=digest(payload))
    _journal(box, operation, Operation(path=INDEX_PATH, sha256=digest(payload)))

    with pytest.raises(ForarbeiderRefusedError) as caught:
        recover(box)

    assert caught.value.reason is RefusalReason.UNSAFE_OUTPUT_PATH
    assert list((tmp_path / "elsewhere").iterdir()) == []


def test_recovery_that_does_not_reach_its_manifest_keeps_the_journal(box: Path) -> None:
    _journal(box, Operation(path=DOCUMENT, sha256=None), Operation(path=INDEX_PATH, sha256=None))

    with pytest.raises(ForarbeiderRefusedError) as caught:
        recover(box)

    assert caught.value.reason is RefusalReason.CORPUS_TREE_MISMATCH
    assert (box / JOURNAL).exists()


def test_nothing_is_written_when_the_journal_cannot_be_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desk = Desk(tmp_path / "desk")
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    sha = _with_terminal(desk, monkeypatch, path)

    def refuse(source: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(corpus_transaction, "replace", refuse)
    result = simulate(desk, sha, path)

    assert result.exit_code == 1
    assert said("refused: output_write_failed: staging", result.output)
    assert not (path / "forarbeider").exists()
    assert not (path / JOURNAL).exists()


def _with_terminal(desk: Desk, monkeypatch: pytest.MonkeyPatch, path: Path) -> str:
    monkeypatch.setattr("lovspor.forarbeider.release_commands._interactive", lambda: True)
    return planned(desk, path)


def test_a_plan_forged_to_say_synthetic_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag is a convenience: the simulator checks the pages again itself."""
    desk = Desk(tmp_path / "desk", extras={3: "<p>Ikke en del av pakken.</p>"})
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    honest = _with_terminal(desk, monkeypatch, path)
    stored = PromotionPlan.model_validate_json(
        (desk.workspace / "plans" / f"{honest}.json").read_bytes()
    )
    forged = write_record(
        desk.workspace, "plans", stored.model_copy(update={"synthetic_inputs": True})
    )

    result = simulate(desk, forged, path)

    assert result.exit_code == 1
    assert said("refused: input_not_synthetic: observation", result.output)
    assert not (path / "forarbeider").exists()


@pytest.mark.parametrize(
    "path",
    ["../outside.md", "lover/README.md", "manifest.json", "forarbeider/../manifest.json", "/etc/x"],
)
def test_a_journal_naming_a_path_outside_the_dataset_is_refused(
    box: Path, tmp_path: Path, path: str
) -> None:
    payload = b"overwritten\n"
    (box / STAGING).mkdir()
    (box / STAGING / digest(payload)).write_bytes(payload)
    raw = {
        "schema_version": 1,
        "record_type": "forarbeider_corpus_journal",
        "target_manifest_sha256": DIGEST,
        "operations": [
            {"path": path, "sha256": digest(payload)},
            {"path": INDEX_PATH, "sha256": None},
        ],
    }
    (box / JOURNAL).write_text(json.dumps(raw))
    before = {name: (box / name).read_bytes() for name in ("manifest.json", "lover/README.md")}

    with pytest.raises(ForarbeiderRefusedError) as caught:
        recover(box)

    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_TREE_MISMATCH,
        "journal",
    )
    assert {name: (box / name).read_bytes() for name in before} == before
    assert not (tmp_path / "outside.md").exists()
