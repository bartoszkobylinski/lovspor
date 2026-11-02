"""Regression checks for the private release contract in docs/operations.md."""

from pathlib import Path

import pytest

from lovspor.forarbeider.release_commands import ReleaseScope, revoke_impl
from lovspor.forarbeider.release_models import ReleaseRevocation, record_text
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    OWNER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")


@pytest.mark.parametrize("target", ["metadata", "observation"])
@pytest.mark.parametrize(
    "payload", [b"\xff", b'{"schema_version":'], ids=["invalid-utf8", "truncated-json"]
)
def test_malformed_staged_input_is_refused_without_changing_release_records(
    tmp_path: Path, target: str, payload: bytes
) -> None:
    """Malformed inputs are typed refusals, not written deny reports (operations.md)."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    checkpoints = desk.all_checkpoints()
    before = {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    }
    path = desk.metadata if target == "metadata" else desk.observations[3]
    path.write_bytes(payload)

    result = desk.preflight(authorization, checkpoints)

    assert result.exit_code == 1, result.output
    assert said("refused: schema_invalid", result.output)
    assert not (desk.workspace / "reports").exists()
    assert {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    } == before


@pytest.mark.parametrize("missing", [False, True], ids=["changed", "removed"])
def test_only_chapters_with_intact_bound_evidence_are_planned(
    tmp_path: Path, missing: bool
) -> None:
    """'Only the chapters that pass are planned' also permits a partial preview."""
    desk = Desk(tmp_path)
    desk.approve_all()
    options: list[str] = []
    for kind, path in desk.evidence.items():
        chapters = "2" if kind == "source_rights" else "2,3"
        options += ["--evidence", f"{kind}@{chapters}={path}"]
    rights = desk.write_evidence("source_rights", "-3")
    options += ["--evidence", f"source_rights@3={rights}"]
    dossier = said_digest(desk.dossier(options).output, "dossier")
    authorized = desk.authorize(dossier)
    assert authorized.exit_code == 0, authorized.output
    authorization = said_digest(authorized.output, "authorization")
    if missing:
        rights.unlink()
    else:
        rights.write_bytes(b"replacement rights evidence")

    result = desk.preflight(authorization, desk.all_checkpoints())

    assert result.exit_code == 0, result.output
    report = desk.report(result.output)
    assert report["outcome"] == "eligible_preview"
    assert report["document_reasons"] == []
    assert report["planned_chapters"] == [2]
    assert report["coverage"] == "partial_curated_excerpt"
    assert report["publication_authorized"] is False
    assert report["writes_corpus"] is False
    good, bad = report["chapters"]
    assert good["chapter_number"] == 2
    assert good["eligible"] is True
    assert good["reasons"] == good["missing_evidence"] == []
    assert bad["chapter_number"] == 3
    assert bad["eligible"] is False
    assert bad["missing_evidence"] == ["source_rights"]
    fault = "evidence_unavailable" if missing else "evidence_digest_mismatch"
    assert set(bad["reasons"]) == {fault, "evidence_incomplete"}


def test_a_revocation_bound_to_another_authorization_is_refused(tmp_path: Path) -> None:
    """A canonical record still must revoke the authorization used to find it."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    scope = ReleaseScope(workspace=desk.workspace, ledger=desk.ledger)
    revoke_impl(scope, authorization, f"{OWNER}@2100-04-05T00:00:00Z")
    path = desk.workspace / "revocations" / f"{authorization}.json"
    record = ReleaseRevocation.model_validate_json(path.read_bytes())
    other = record.model_copy(update={"authorization_sha256": "f" * 64})
    path.write_text(record_text(other), encoding="utf-8")
    before = {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    }

    result = desk.preflight(authorization, desk.all_checkpoints())

    assert result.exit_code == 1, result.output
    assert said("refused: release_record_corrupt", result.output)
    assert not (desk.workspace / "reports").exists()
    assert {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    } == before
