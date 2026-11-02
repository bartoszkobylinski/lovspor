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
def test_missing_staged_input_is_a_refusal_without_a_report(tmp_path: Path, target: str) -> None:
    """Unreadable inputs give exit 1 with nothing written (operations.md)."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    checkpoints = desk.all_checkpoints()
    path = desk.metadata if target == "metadata" else desk.observations[3]
    path.unlink()
    before = {item: item.read_bytes() for item in desk.workspace.rglob("*") if item.is_file()}

    result = desk.preflight(authorization, checkpoints)

    assert result.exit_code == 1, result.output
    assert said(f"refused: input_unreadable: {target}", result.output)
    assert str(path) not in result.output
    assert not (desk.workspace / "reports").exists()
    assert {
        item: item.read_bytes() for item in desk.workspace.rglob("*") if item.is_file()
    } == before


def test_preflight_retry_never_replaces_conflicting_report_bytes(tmp_path: Path) -> None:
    """Digest-named records are never replaced, even by an otherwise valid retry."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    checkpoints = desk.all_checkpoints()
    first = desk.preflight(authorization, checkpoints)
    assert first.exit_code == 0, first.output
    sha = said_digest(first.output, "report")
    path = desk.workspace / "reports" / f"{sha}.json"
    path.write_bytes(b"conflicting private report bytes")
    before = {item: item.read_bytes() for item in desk.workspace.rglob("*") if item.is_file()}
    inode = path.stat().st_ino

    result = desk.preflight(authorization, checkpoints)

    assert result.exit_code == 1, result.output
    assert said("refused: observation_exists: reports", result.output)
    assert str(path) not in result.output
    assert "conflicting private report bytes" not in result.output
    assert path.stat().st_ino == inode
    assert {
        item: item.read_bytes() for item in desk.workspace.rglob("*") if item.is_file()
    } == before


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("symlink", "unsafe_input_path"),
        ("permissions", "observation_unsafe"),
        ("checkout", "unsafe_output_path"),
    ],
)
def test_preflight_rechecks_evidence_safety_after_authorization(
    tmp_path: Path, change: str, reason: str
) -> None:
    """The private-storage contract applies when evidence is read again, too."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    before = {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    }
    rights = desk.evidence["source_rights"]
    original = rights.read_bytes()
    if change == "symlink":
        target = rights.with_name("replacement.txt")
        rights.rename(target)
        rights.symlink_to(target)
    elif change == "permissions":
        rights.chmod(0o644)
    else:
        (rights.parent / ".git").mkdir()

    result = desk.preflight(authorization, desk.all_checkpoints())

    assert result.exit_code == 1, result.output
    # A checkout makes the shared evidence directory unsafe for every artifact;
    # the dossier's first artifact is encountered before source_rights.
    kind = "privacy_processing_basis" if change == "checkout" else "source_rights"
    assert said(f"refused: {reason}: evidence {kind}", result.output)
    assert str(tmp_path) not in result.output
    assert original.decode() not in result.output
    assert rights.read_bytes() == original
    assert not (desk.workspace / "reports").exists()
    assert {
        item.relative_to(desk.workspace): item.read_bytes()
        for item in desk.workspace.rglob("*.json")
    } == before


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
