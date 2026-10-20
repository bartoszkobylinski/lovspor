"""Received migration regressions using real files, decisions, and git history."""

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.decisions import DecisionLog, ReceivedKey, WithdrawalRecord
from lovspor.promotion.extract import EXTRACTOR_VERSION
from tests.unit.promotion_cli_fixtures import REVIEWER, REVIEWER_ROLE, git, tree
from tests.unit.promotion_fixtures import REGULATION_LINES
from tests.unit.test_promotion_migrate_received import (
    FIRST_AT,
    LATER,
    SECOND_AT,
    _markdown_path,
    _migrate,
    _promote_received,
    _receive,
    _record,
    corpus,
    root,
    workspace,
)
from tests.unit.test_promotion_received_commands import FJALER, LOCAL

# Imported fixtures remain ordinary pytest fixtures, shared with the CLI tests.
__all__ = ["corpus", "root", "workspace"]


def _commit(corpus: Path) -> None:
    git(corpus, "add", "-A")
    git(corpus, "commit", "-q", "-m", "migration input fixture")


def _age(corpus: Path, root: Path) -> None:
    """Write persisted metadata from the previous extractor, without replacing code."""
    for path in (corpus / LOCAL).rglob("*.json"):
        data = json.loads(path.read_bytes())
        if path.name == "manifest.json":
            for record in data["documents"].values():
                record["extractor_version"] = EXTRACTOR_VERSION - 1
        elif path.parent.name == "observations":
            for version in data["versions"]:
                version["promotion"]["extractor_version"] = EXTRACTOR_VERSION - 1
        path.write_text(json.dumps(data), encoding="utf-8")
    log = root / "promotions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    for record in records:
        if "extractor_version" in record:
            record["extractor_version"] = EXTRACTOR_VERSION - 1
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    _commit(corpus)


def _refused(corpus: Path) -> str:
    before = tree(corpus)
    result = _migrate(corpus)
    assert result.exit_code == 1, result.output
    assert tree(corpus) == before
    assert result.stdout == ""
    assert result.stderr.startswith("Refused: ")
    return result.stderr


def test_received_migration_recovers_missing_evidence(workspace, corpus, root, tmp_path):
    _promote_received(_receive(workspace, REGULATION_LINES, FIRST_AT), corpus, tmp_path)
    [evidence] = (corpus / LOCAL / "4646" / "evidence").glob("*.json")
    expected = evidence.read_bytes()
    evidence.unlink()
    _age(corpus, root)
    markdown = _markdown_path(corpus).read_bytes()
    result = _migrate(corpus)
    assert result.exit_code == 0, result.output
    assert evidence.read_bytes() == expected
    assert _markdown_path(corpus).read_bytes() == markdown


def test_received_migration_refuses_a_version_gap_with_path_and_range(
    workspace, corpus, root, tmp_path
):
    _promote_received(_receive(workspace, REGULATION_LINES, FIRST_AT), corpus, tmp_path)
    _age(corpus, root)
    [path] = (corpus / LOCAL / "4646" / "observations").glob("*.json")
    data = json.loads(path.read_bytes())
    data["versions"][0]["version"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    _commit(corpus)
    assert f"{path.relative_to(corpus)} does not list received versions 1..1" in _refused(corpus)


@pytest.mark.parametrize("match_by", ["artifact", "document"])
def test_received_migration_checks_both_withdrawal_identities(
    workspace, corpus, root, tmp_path, match_by
):
    sha = _receive(workspace, REGULATION_LINES, FIRST_AT)
    _promote_received(sha, corpus, tmp_path)
    _age(corpus, root)
    manifest = json.loads((corpus / LOCAL / "manifest.json").read_bytes())
    [doc_id] = manifest["documents"]
    key = ReceivedKey(authority_id="4646", innsyn_request=FJALER, sha256=sha)
    DecisionLog(ObservatoryRoot(root, [])).append(
        WithdrawalRecord(
            doc_id=doc_id if match_by == "document" else "another-document",
            authority_id="4646",
            slug=_record(corpus)["slug"],
            artifacts=(key,) if match_by == "artifact" else (),
            removed_reason="withdrawn_misclassified",
            decided_by=REVIEWER,
            reviewer_role=REVIEWER_ROLE,
            decided_at=datetime.now(UTC),
            reason="Misclassified regulation.",
        )
    )
    assert "was withdrawn" in _refused(corpus)


def test_missing_earlier_approval_lists_every_received_copy(workspace, corpus, root, tmp_path):
    first = _receive(workspace, REGULATION_LINES, FIRST_AT)
    _promote_received(first, corpus, tmp_path)
    _promote_received(_receive(workspace, LATER, SECOND_AT), corpus, tmp_path)
    _age(corpus, root)
    [path] = (corpus / LOCAL / "4646" / "observations").glob("*.json")
    data = json.loads(path.read_bytes())
    copies = data["versions"][0]["received"]
    other = "0" * 64
    copies.append({**copies[0], "source_sha256": other})
    path.write_text(json.dumps(data), encoding="utf-8")
    log = root / "promotions.jsonl"
    records = [json.loads(line) for line in log.read_text().splitlines()]
    records = [r for r in records if r.get("artifact", {}).get("sha256") != first]
    log.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    _commit(corpus)
    error = _refused(corpus)
    assert f"v1 (approve one of {', '.join(sorted([first, other]))}): " in error
    assert "no human decision" in error


def test_earlier_missing_source_explains_why_approval_cannot_carry(
    workspace, corpus, root, tmp_path
):
    first = _receive(workspace, REGULATION_LINES, FIRST_AT)
    _promote_received(first, corpus, tmp_path)
    _promote_received(_receive(workspace, LATER, SECOND_AT), corpus, tmp_path)
    _age(corpus, root)
    (workspace / "received" / first[:2] / first).unlink()
    error = _refused(corpus)
    assert f"v1 (approve one of {first}): " in error
    assert f"it is not byte-identical at extractor v{EXTRACTOR_VERSION} (" in error
    assert "missing or unreadable" in error
    assert "); approve it again" in error


def test_migration_refuses_a_corpus_inside_the_innsyn_workspace(workspace, corpus, root):
    nested = workspace / "corpus"
    shutil.copytree(corpus, nested)
    before = tree(nested)
    result = _migrate(nested)
    assert result.exit_code == 1, result.output
    assert f"overlaps {workspace}" in result.stderr
    assert tree(nested) == before
