"""Behavioral regression cases for the listed approval-carry survivors."""

import json
from pathlib import Path

import pytest

from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.archive import read_artifact
from lovspor.promotion.carry import _carriable, historical_markdown
from lovspor.promotion.commands import Request, _context
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    DECISIONS_FILENAME,
    ApprovalReference,
    ArtifactKey,
    CarriedRecord,
    Decision,
    DecisionLog,
    HumanDecision,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.plan import Prepared, prepare
from tests.unit.backfilled_corpus_fixtures import backfilled_corpus
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    KLASS_VERSION,
    PAGE_URL,
    REVIEWER,
    REVIEWER_ROLE,
    git,
    invoke,
    make_corpus,
    promote,
    tree,
)


def decision(
    kind: Decision = Decision.APPROVE, version: int | None = EXTRACTOR_VERSION
) -> HumanDecision:
    return HumanDecision(
        artifact=ArtifactKey(authority_id=AUTHORITY, sha256="a" * 64, source_url=PAGE_URL),
        decision=kind,
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        decided_at=FIRST_SEEN,
        reason="Reviewed source",
        content_hash="b" * 64,
        extractor_version=version,
    )


@pytest.mark.parametrize(
    "kind,version", [(Decision.REJECT, EXTRACTOR_VERSION), (Decision.HOLD, EXTRACTOR_VERSION)]
)
def test_reference_refuses_nonapprovals_with_extractor(kind: Decision, version: int | None) -> None:
    with pytest.raises(ValueError) as caught:
        ApprovalReference.of(decision(kind, version))
    assert str(caught.value) == "only an approval naming its extractor can be carried"


@pytest.mark.parametrize(
    "metadata",
    [
        'version: 2\ncontent_hash: "wrong"\n',
        'version: 1\ncontent_hash: "wanted"\n',
    ],
)
def test_history_matches_only_frontmatter(tmp_path: Path, metadata: str) -> None:
    path = make_corpus(tmp_path)
    markdown = path / "lokale-forskrifter" / "example.md"
    text = f'---\n{metadata}---\nversion: 1\ncontent_hash: "wanted"\n\n---\nBody\n'
    markdown.write_text(text, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "publish")
    expected = text if metadata.startswith("version: 1") else None
    assert (
        historical_markdown(CorpusCheckout(path, []), "lokale-forskrifter/example.md", 1, "wanted")
        == expected
    )


def _old_approvals(root: Path) -> list[HumanDecision]:
    path = root / DECISIONS_FILENAME
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        if record["kind"] == "decision":
            record["extractor_version"] = EXTRACTOR_VERSION - 1
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return [
        r for r in DecisionLog(ObservatoryRoot(root, [])).records() if isinstance(r, HumanDecision)
    ]


def test_missing_history_explains_why_approval_cannot_carry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = backfilled_corpus(tmp_path, monkeypatch)
    _old_approvals(tmp_path / "observatory")
    manifest = corpus / "lokale-forskrifter" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for record in data["documents"].values():
        record["extractor_version"] = EXTRACTOR_VERSION - 1
    manifest.write_text(json.dumps(data), encoding="utf-8")
    git(corpus, "checkout", "-q", "--orphan", "squashed")
    git(corpus, "add", "-A")
    git(corpus, "commit", "-q", "-m", "squashed")
    before = tree(corpus)
    log_before = (tmp_path / "observatory" / DECISIONS_FILENAME).read_bytes()
    result = invoke("promote", "migrate", "--corpus", str(corpus))
    assert result.exit_code == 1, result.output
    assert (
        "its published bytes are not in this checkout's history, so they cannot be shown "
        "byte-identical and its approval is not carried; approve it again"
    ) in result.output
    assert tree(corpus) == before
    assert (tmp_path / "observatory" / DECISIONS_FILENAME).read_bytes() == log_before


def test_backfill_reuses_carried_approvals_when_reconstructing_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backfilled_corpus(tmp_path, monkeypatch)
    corpus = make_corpus(tmp_path / "fresh")
    root = tmp_path / "observatory"
    approvals = _old_approvals(root)
    log = DecisionLog(ObservatoryRoot(root, []))
    for approval in approvals:
        assert approval.content_hash is not None
        log.append(
            CarriedRecord(
                artifact=approval.artifact,
                carried_at=FIRST_SEEN,
                doc_id="lk-0301-000000000000",
                version=1,
                content_hash=approval.content_hash,
                from_extractor_version=EXTRACTOR_VERSION - 1,
                to_extractor_version=EXTRACTOR_VERSION,
                approval=ApprovalReference.of(approval),
            )
        )
    result = promote("backfill", approvals[0].artifact.sha256, corpus)
    assert result.exit_code == 0, result.output
    manifest = json.loads((corpus / "lokale-forskrifter/manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    assert record["version"] == 1
    assert record["content_hash"] == approvals[0].content_hash
    assert (corpus / record["markdown_path"]).is_file()


def test_running_extractor_approval_is_not_carriable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = backfilled_corpus(tmp_path, monkeypatch)
    log = DecisionLog(ObservatoryRoot(tmp_path / "observatory", []))
    approval = next(r for r in log.records() if isinstance(r, HumanDecision))
    context = _context(Request(AUTHORITY, approval.artifact.sha256, corpus, KLASS_VERSION))
    prepared = prepare(
        read_artifact(context.log, context.fetches, context.key, None),
        context.authority,
        context.corpus,
    )
    assert isinstance(prepared, Prepared)
    assert _carriable(approval, prepared) is False
