"""Refresh refusal, traversal and output contracts exercised through real files."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import ArtifactKey, DecisionLog, WithdrawalRecord
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.migrate import MigrationInputs, published
from lovspor.promotion.observed_refresh import (
    RefreshAllRequest,
    plan_observed_refresh,
    refresh_all_impl,
)
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    invoke,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import html_page


@pytest.fixture
def checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    root = tmp_path / "observatory"
    root.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(root))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    monkeypatch.delenv(ENV_INNSYN_DIR, raising=False)
    register(root)
    corpus = make_corpus(tmp_path)
    sha = store(root, html_page())
    result = approve(sha, Decision().write(tmp_path))
    assert result.exit_code == 0, result.output
    result = promote("local", sha, corpus)
    assert result.exit_code == 0, result.output
    return root, corpus, sha


def _manifest(corpus: Path) -> Path:
    return corpus / "lokale-forskrifter/manifest.json"


def _refresh(corpus: Path, *args: str):
    return invoke("promote", "refresh", "--corpus", str(corpus), *args)


@pytest.mark.parametrize("by", ["artifact", "document"])
def test_refresh_obeys_withdrawal_in_a_checkout_that_has_not_seen_it(checkout, by: str) -> None:
    root, corpus, sha = checkout
    manifest = json.loads(_manifest(corpus).read_bytes())
    [(doc_id, record)] = manifest["documents"].items()
    # The archive's standing withdrawal can precede its application to this checkout.
    # Independently exercise matching by source and matching by document identity.
    withdrawal = WithdrawalRecord(
        doc_id=doc_id if by == "document" else "lf-20200101-0001",
        authority_id=AUTHORITY,
        slug=record["slug"],
        removed_reason="withdrawn_identity",
        artifacts=(
            ArtifactKey(
                authority_id=AUTHORITY,
                source_url=PAGE_URL,
                sha256=sha if by == "artifact" else "0" * 64,
            ),
        ),
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        decided_at=FIRST_SEEN,
        reason="To dokumenter er ett.",
    )
    DecisionLog(ObservatoryRoot(root, [])).append(withdrawal)
    before = tree(corpus)

    result = _refresh(corpus)

    assert result.exit_code == 1, result.output
    assert "was withdrawn" in result.stderr
    assert "withdrawn_identity" in result.stderr
    assert tree(corpus) == before


def test_removed_record_does_not_hide_a_later_current_document(checkout) -> None:
    _, corpus, _ = checkout
    manifest = json.loads(_manifest(corpus).read_bytes())
    [(doc_id, record)] = manifest["documents"].items()
    removed = record | {"status": "removed", "removed_reason": "withdrawn_identity"}
    assert doc_id > "lf-00000000-0000"
    manifest["documents"]["lf-00000000-0000"] = removed
    _manifest(corpus).write_text(json.dumps(manifest), encoding="utf-8")
    before = tree(corpus)

    result = _refresh(corpus)

    assert result.exit_code == 0, result.output
    assert f"{doc_id}: unchanged; its rendering is current\n" in result.stdout
    assert tree(corpus) == before


def test_refresh_refuses_a_corpus_inside_the_innsyn_workspace(
    checkout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, corpus, _ = checkout
    monkeypatch.setenv(ENV_INNSYN_DIR, str(tmp_path))
    before = tree(corpus)

    result = _refresh(corpus)

    assert result.exit_code == 1, result.output
    assert f"--corpus {corpus} overlaps {tmp_path}" in result.output
    assert tree(corpus) == before


def test_current_refresh_prints_the_exact_noop_notice(checkout) -> None:
    _, corpus, _ = checkout
    before = tree(corpus)

    result = _refresh(corpus)

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == (
        "Nothing to refresh; nothing written, nothing to commit."
    )
    assert tree(corpus) == before


def test_stale_front_matter_dry_run_prints_the_exact_notice(checkout) -> None:
    _, corpus, _ = checkout
    manifest = json.loads(_manifest(corpus).read_bytes())
    [record] = manifest["documents"].values()
    path = corpus / record["markdown_path"]
    markdown = path.read_text(encoding="utf-8")
    lines = markdown.splitlines(keepends=True)
    assert any(line.startswith("ikraft_text: ") for line in lines)
    path.write_text(
        "".join(
            'ikraft_text: "straks"\n' if line.startswith("ikraft_text: ") else line
            for line in lines
        ),
        encoding="utf-8",
    )
    before = tree(corpus)

    result = _refresh(corpus, "--dry-run")

    assert result.exit_code == 0, result.output
    assert '-ikraft_text: "straks"' in result.stdout
    assert result.stdout.splitlines()[-1] == "Dry run: nothing written."
    assert tree(corpus) == before


def test_refresh_has_no_timestamped_writes(checkout, capsys: pytest.CaptureFixture[str]) -> None:
    """Probe whether the run instant has any observable effect on this path."""
    _, corpus, _ = checkout
    before = tree(corpus)
    for now in (FIRST_SEEN, datetime(2099, 1, 1, tzinfo=UTC)):
        refresh_all_impl(RefreshAllRequest(corpus, False), now)
        assert capsys.readouterr().out.splitlines()[-1] == (
            "Nothing to refresh; nothing written, nothing to commit."
        )
        assert tree(corpus) == before


def test_observed_plan_defaults_to_refusing_an_earlier_extractor_approval(checkout) -> None:
    """Only an explicit preview may plan a changed rendering pending re-approval."""
    root, corpus, _ = checkout
    # Represent an archive whose owner approved the text at the previous extractor.
    log_path = root / "promotions.jsonl"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["kind"] == "decision":
            record["extractor_version"] = EXTRACTOR_VERSION - 1
    log_path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    checkout_root = ObservatoryRoot(root, [])
    checkout_corpus = CorpusCheckout(corpus, [root])
    [(doc_id, record)] = checkout_corpus.local_manifest().documents.items()
    path = corpus / record.markdown_path
    markdown = path.read_text(encoding="utf-8")
    assert any(line.startswith("ikraft_text: ") for line in markdown.splitlines())
    path.write_text(
        "".join(
            'ikraft_text: "straks"\n' if line.startswith("ikraft_text: ") else line
            for line in markdown.splitlines(keepends=True)
        ),
        encoding="utf-8",
    )
    inputs = MigrationInputs(checkout_root, DecisionLog(checkout_root), checkout_corpus, FIRST_SEEN)
    document = published(checkout_corpus, doc_id, record)
    before, archive_before = tree(corpus), tree(root)

    preview = plan_observed_refresh(inputs, document, preview=True)
    assert preview is not None
    assert preview.needs_reapproval is True
    assert preview.markdown != document.markdown
    with pytest.raises(
        PromotionRefusedError, match="approval was given for another text or extractor"
    ):
        plan_observed_refresh(inputs, document)

    assert tree(corpus) == before
    assert tree(root) == archive_before
