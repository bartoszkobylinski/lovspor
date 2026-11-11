"""``forarbeider-release batch withdraw``: forward-only takedown (#738, B1b).

A released, committed batch in a scratch clone; the owner withdraws one
document at a terminal. The member stays as provenance, marked withdrawn,
its file is removed and the index stops listing it; Git history keeps the
earlier commit, and the command says so. Nothing is served afterwards: the
dataset reader refuses the old file if it comes back.
"""

import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.corpus_models import INDEX_PATH, MANIFEST_PATH, parse_any_manifest
from lovspor.forarbeider.corpus_tree import read_any_dataset_files
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from tests.unit.cli_output import said
from tests.unit.forarbeider_apply_support import (
    DOC1,
    DOC2,
    PATH1,
    PATH2,
    ApplyBench,
    outside_dataset,
    without_records,
)
from tests.unit.forarbeider_batch_support import BRANCH
from tests.unit.forarbeider_checkout_support import commit_all, git
from tests.unit.forarbeider_release_support import at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApplyBench:
    return ApplyBench(tmp_path, monkeypatch)


@pytest.fixture
def released(
    bench: ApplyBench,
    httpx_mock: HTTPXMock,
    at_a_terminal: None,  # noqa: F811
) -> tuple[str, Path]:
    """A two-document batch applied and committed by the owner."""
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    assert bench.apply(sha, request).exit_code == 0
    commit_all(bench.clone, "owner: release batch")
    bench.prompts.clear()
    return sha, request


def _refused(result: object, reason: str, detail: str) -> None:
    output = getattr(result, "output", "")
    assert getattr(result, "exit_code", None) == 1, output
    assert said(f"refused: {reason}: {detail}", output), output
    assert "WITHDRAWN" not in output


@pytest.mark.usefixtures("released")
def test_a_withdrawal_removes_the_file_keeps_provenance_and_warns(bench: ApplyBench) -> None:
    head = git(bench.clone, "rev-parse", "HEAD").strip()
    before = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())
    outside = without_records(outside_dataset(bench.root, bench.clone), bench.batch.workspace)

    result = bench.withdraw(DOC1, "2", reason="privacy")

    assert result.exit_code == 0, result.output
    assert bench.prompts == [f"withdraw {DOC1} chapters 2,3 from {BRANCH}"]
    assert said(f"WITHDRAWN {DOC1} record", result.output)
    assert said("reason privacy", result.output)
    assert said("Git history is never rewritten", result.output)
    assert said("a revert does not erase", result.output)
    assert bench.status() == sorted([PATH1, INDEX_PATH, MANIFEST_PATH])
    assert git(bench.clone, "rev-parse", "HEAD").strip() == head
    assert git(bench.clone, "show", f"HEAD:{PATH1}"), "history keeps the earlier bytes"
    after = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())
    gone = next(item for item in after.documents if item.document.id == DOC1)
    kept = next(item for item in before.documents if item.document.id == DOC1)
    assert gone.release_status == "withdrawn"
    assert gone.markdown_sha256 is None
    assert gone.chapters == kept.chapters
    assert gone.times.withdrawn_at is not None
    assert gone.times.withdrawn_at.isoformat() == "2100-04-10T12:45:00+00:00"
    index = (bench.clone / INDEX_PATH).read_text()
    assert PATH1.removeprefix("forarbeider/") not in index
    assert PATH2.removeprefix("forarbeider/") in index
    assert without_records(outside_dataset(bench.root, bench.clone), bench.batch.workspace) == (
        outside
    )


@pytest.mark.usefixtures("released")
def test_the_intent_and_receipt_carry_the_reason_and_both_chapter_lists(bench: ApplyBench) -> None:
    assert bench.withdraw(DOC1, "3", reason="rights").exit_code == 0

    (intent_file,) = bench.records("release_withdrawal_intents")
    (receipt_file,) = bench.records("release_withdrawals")
    intent = json.loads(intent_file.read_text())
    receipt = json.loads(receipt_file.read_text())
    assert intent["chapters_named"] == [3]
    assert intent["chapters_withdrawn"] == [2, 3]
    assert intent["reason"] == receipt["reason"] == "rights"
    assert intent["identity_authenticated"] is False
    assert intent["history_erased"] is receipt["history_erased"] is False
    assert intent["removed"] == [PATH1]
    assert receipt["written_to"] == "working_tree_only"


@pytest.mark.usefixtures("released")
def test_withdrawn_content_is_no_longer_served(bench: ApplyBench) -> None:
    original = (bench.clone / PATH1).read_bytes()
    assert bench.withdraw(DOC1, "2,3").exit_code == 0

    dataset, files = read_any_dataset_files(bench.clone)
    assert PATH1 not in files
    assert PATH2 in files
    assert [item.release_status for item in dataset.manifest.documents] == [
        "withdrawn",
        "released",
    ]
    (bench.clone / PATH1).write_bytes(original)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_any_dataset_files(bench.clone)
    assert caught.value.reason.value == "corpus_tree_mismatch"


def test_a_withdrawn_document_is_never_released_again_by_a_batch(
    bench: ApplyBench, released: tuple[str, Path]
) -> None:
    _, request = released
    assert bench.withdraw(DOC1, "2,3").exit_code == 0
    commit_all(bench.clone, "owner: withdraw")
    again = bench.planned(request, at="2100-04-10T12:50:00Z")

    result = bench.apply(again, request, decided="2100-04-10T12:55:00Z")

    assert said(f"refused: release_conflict: {DOC1}", result.output), result.output


@pytest.mark.usefixtures("released")
def test_a_withdrawal_is_done_once_and_a_lost_receipt_is_completed(bench: ApplyBench) -> None:
    assert bench.withdraw(DOC1, "2").exit_code == 0
    (receipt,) = bench.records("release_withdrawals")

    _refused(bench.withdraw(DOC1, "2"), "document_not_released", "document")
    receipt.unlink()
    completed = bench.withdraw(DOC1, "2")
    assert completed.exit_code == 0, completed.output
    assert len(bench.records("release_withdrawals")) == 1
    commit_all(bench.clone, "owner: withdraw")
    _refused(bench.withdraw(DOC1, "2"), "document_not_released", "document")
    assert len(bench.prompts) == 1


@pytest.mark.usefixtures("released")
def test_a_withdrawal_refuses_an_unverified_dirty_or_wrong_clone(bench: ApplyBench) -> None:
    (bench.clone / "stray.txt").write_text("x\n")
    _refused(bench.withdraw(DOC1, "2"), "checkout_dirty", "untracked")
    (bench.clone / "stray.txt").unlink()
    _refused(
        bench.withdraw(DOC1, "2", branch="forarbeider/other"), "checkout_branch_mismatch", "branch"
    )
    _refused(bench.withdraw(DOC1, "2", branch="main"), "target_branch_invalid", "branch")
    git(bench.clone, "config", "core.hooksPath", "/elsewhere")
    _refused(bench.withdraw(DOC1, "2"), "checkout_config_unsafe", "core.hookspath")
    git(bench.clone, "config", "--unset", "core.hooksPath")
    _refused(bench.withdraw(DOC1, "2", checkout=bench.root / "nowhere"), "checkout_unsafe", "path")
    assert bench.status() == []
    assert bench.prompts == []


@pytest.mark.usefixtures("released")
def test_a_withdrawal_names_a_released_document_and_its_released_chapters(
    bench: ApplyBench,
) -> None:
    _refused(bench.withdraw("regjeringen-9999999", "2"), "document_not_released", "document")
    _refused(bench.withdraw(DOC2, "4"), "chapter_not_released", "chapters")
    _refused(bench.withdraw(DOC2, "2,2"), "chapter_not_released", "chapters")
    bad = bench.withdraw(DOC2, "two")
    assert bad.exit_code == 2
    assert bench.status() == []


@pytest.mark.usefixtures("released")
def test_a_withdrawal_needs_the_phrase_a_terminal_and_a_valid_moment(
    bench: ApplyBench, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench.typed = lambda shown: shown.replace("chapters 2,3", "chapters 2")
    _refused(bench.withdraw(DOC1, "2"), "review_not_interactive", "withdrawal")
    bench.typed = lambda shown: shown
    _refused(
        bench.withdraw(DOC1, "2", decided="2100-04-10T13:00:01Z"), "invalid_timestamp", "decided"
    )
    _refused(
        bench.withdraw(DOC1, "2", decided="2100-04-10T12:00:00Z"),
        "manifest_invalid",
        "withdrawn_at",
    )
    monkeypatch.setattr(release_commands, "_interactive", lambda: False)
    _refused(bench.withdraw(DOC1, "2"), "review_not_interactive", "withdrawal")
    assert bench.status() == []
    assert bench.records("release_withdrawal_intents") == []
