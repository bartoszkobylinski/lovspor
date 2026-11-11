"""An interrupted batch apply or withdrawal, recovery and retry (#738, B1b).

A crash is injected at every phase of the transaction by failing the one
call that moves bytes into place (``corpus_transaction.replace``) on its Nth
use: the journal itself (1), each document (2, 3), the index (4) and the
manifest (5); then at the read-back, after the journal is cleared, and with
the lock left behind as a killed process leaves it. Each time the clone is
never half-published silently: every command refuses until ``batch recover``
settles it, a release is rolled back and a withdrawal finished, and a retry
then succeeds exactly once.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider import batch_apply, corpus_transaction, release_lock
from lovspor.forarbeider.corpus_models import MANIFEST_PATH, parse_any_manifest
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK, STAGING
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.cli_output import said
from tests.unit.forarbeider_apply_support import DOC1, PATH1, PATH2, ApplyBench
from tests.unit.forarbeider_checkout_support import commit_all
from tests.unit.forarbeider_release_support import at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")
WRITTEN = sorted([PATH1, PATH2, "forarbeider/INDEX.md", MANIFEST_PATH])


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApplyBench:
    return ApplyBench(tmp_path, monkeypatch)


class Crash:
    """Fail ``corpus_transaction.replace`` on its ``at``-th call, once."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, at: int) -> None:
        self.at, self.calls = at, 0
        self.real = corpus_transaction.replace
        monkeypatch.setattr(corpus_transaction, "replace", self)

    def __call__(self, source: Path, target: Path) -> None:
        self.calls += 1
        if self.calls == self.at:
            raise OSError("injected crash")
        self.real(source, target)


def _batch(bench: ApplyBench, httpx_mock: HTTPXMock) -> tuple[str, Path]:
    request = bench.mixed(httpx_mock)
    return bench.planned(request), request


def _pending_refusals(bench: ApplyBench, sha: str, request: Path, kind: str) -> None:
    """Every command but recovery refuses the interrupted clone, and writes nothing."""
    before = bench.status()
    for result in (
        bench.apply(sha, request),
        bench.invoke(
            [
                "preflight",
                sha,
                "--checkout",
                str(bench.clone),
                "--request",
                str(request),
                "--source-proof",
                str(bench.batch.run_file),
            ]
        ),
        bench.withdraw(DOC1, "2"),
    ):
        assert result.exit_code == 1, result.output
        assert said(f"refused: checkout_pending_change: {kind}", result.output), result.output
    assert bench.status() == before


@pytest.mark.parametrize("at", [2, 3, 4, 5])
def test_a_crash_after_the_journal_is_rolled_back_by_recovery_then_retried(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch, at: int
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, at)

    first = bench.apply(sha, request)

    assert first.exit_code == 1, first.output
    assert said("refused: output_write_failed: forarbeider", first.output)
    assert (bench.clone / JOURNAL).exists()
    assert not (bench.clone / LOCK).exists(), "a refused run releases its lock"
    assert "BATCH APPLIED" not in first.output
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    _pending_refusals(bench, sha, request, "journal")

    recovered = bench.recover()

    assert recovered.exit_code == 0, recovered.output
    assert said("RECOVERED rolled back release confirmation", recovered.output)
    assert bench.status() == []
    for leftover in (JOURNAL, STAGING, LOCK):
        assert not (bench.clone / leftover).exists()
    again = bench.apply(sha, request)
    assert again.exit_code == 0, again.output
    assert bench.status() == WRITTEN


def test_a_crash_while_installing_the_journal_leaves_only_orphaned_staging(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, 1)

    first = bench.apply(sha, request)

    assert said("refused: output_write_failed: staging", first.output)
    assert bench.status() == []
    assert not (bench.clone / JOURNAL).exists()
    assert (bench.clone / STAGING).exists()
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    _pending_refusals(bench, sha, request, "staging")
    assert said("RECOVERED orphaned staging removed", bench.recover().output)
    assert bench.apply(sha, request).exit_code == 0


def test_a_failed_read_back_keeps_the_journal_for_recovery(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    real = batch_apply.read_back_of

    def failing(branch: str, head: str, held: Any) -> Any:
        def read_back(root: Path, expected: str | None, changed: frozenset[str]) -> None:
            raise ForarbeiderRefusedError(RefusalReason.CORPUS_TREE_MISMATCH, "status")

        return read_back

    monkeypatch.setattr(batch_apply, "read_back_of", failing)
    first = bench.apply(sha, request)
    monkeypatch.setattr(batch_apply, "read_back_of", real)

    assert said("refused: corpus_tree_mismatch: status", first.output)
    assert bench.status() == WRITTEN, "every file was written, but unverified"
    assert (bench.clone / JOURNAL).exists()
    assert bench.recover().exit_code == 0
    assert bench.status() == []


def test_a_crash_after_the_journal_is_cleared_is_completed_by_the_retry(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    real = batch_apply.receipt_of

    def dies(*_: Any) -> Any:
        raise OSError("killed before the receipt")

    monkeypatch.setattr(batch_apply, "receipt_of", dies)
    first = bench.apply(sha, request)
    monkeypatch.setattr(batch_apply, "receipt_of", real)

    assert first.exit_code != 0
    assert bench.status() == WRITTEN
    assert bench.records("batch_releases") == []
    retry = bench.apply(sha, request)
    assert retry.exit_code == 0, retry.output
    assert said(f"UNCHANGED plan {sha}", retry.output)
    assert len(bench.records("batch_releases")) == 1
    assert len(bench.prompts) == 1


def test_a_killed_process_leaves_its_lock_which_only_recovery_takes_over(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, 3)
    real_remove = release_lock._remove
    monkeypatch.setattr(release_lock, "_remove", lambda *_: None)

    bench.apply(sha, request)
    monkeypatch.setattr(release_lock, "_remove", real_remove)
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)

    assert (bench.clone / LOCK).exists()
    locked = bench.apply(sha, request)
    assert said("refused: corpus_locked: checkout", locked.output), locked.output
    recovered = bench.recover()
    assert recovered.exit_code == 0, recovered.output
    assert not (bench.clone / LOCK).exists()
    assert bench.status() == []


def test_recovery_never_overwrites_a_file_edited_by_hand(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, 4)
    bench.apply(sha, request)
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    (bench.clone / PATH1).write_text("edited by hand\n")

    result = bench.recover()

    assert said("refused: corpus_tree_mismatch: forarbeider", result.output), result.output
    assert (bench.clone / PATH1).read_text() == "edited by hand\n"
    assert (bench.clone / JOURNAL).exists()


def test_recovery_refuses_a_journal_no_stored_decision_names(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, 3)
    bench.apply(sha, request)
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    for record in bench.records("batch_confirmations"):
        record.unlink()
    status = bench.status()

    result = bench.recover()

    assert said("refused: corpus_tree_mismatch: journal", result.output), result.output
    assert bench.status() == status


def test_a_journal_edited_to_escape_the_dataset_is_refused(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    crash = Crash(monkeypatch, 3)
    bench.apply(sha, request)
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    journal = bench.clone / JOURNAL
    payload = json.loads(journal.read_text())
    payload["operations"][0]["path"] = "forarbeider/../lover/README.md"
    journal.write_text(json.dumps(payload))
    readme = (bench.clone / "lover" / "README.md").read_bytes()

    result = bench.recover()

    assert said("refused: corpus_tree_mismatch: journal", result.output), result.output
    assert (bench.clone / "lover" / "README.md").read_bytes() == readme


def test_recovery_on_another_branch_or_a_clean_clone(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    _batch(bench, httpx_mock)
    assert said("RECOVERED nothing pending", bench.recover().output)
    result = bench.recover("forarbeider/other")
    assert said("refused: checkout_branch_mismatch: branch", result.output)
    assert said("refused: target_branch_invalid: branch", bench.recover("main").output)


def test_an_interrupted_withdrawal_is_finished_by_recovery_never_undone(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    sha, request = _batch(bench, httpx_mock)
    assert bench.apply(sha, request).exit_code == 0
    commit_all(bench.clone, "owner: release batch")
    crash = Crash(monkeypatch, 3)

    first = bench.withdraw(DOC1, "2,3")

    assert said("refused: output_write_failed: forarbeider", first.output), first.output
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    recovered = bench.recover()
    assert said(f"RECOVERED finished withdrawal of {DOC1}", recovered.output), recovered.output
    manifest = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())
    assert manifest.documents[0].release_status == "withdrawn"
    assert not (bench.clone / PATH1).exists()
    assert len(bench.records("release_withdrawals")) == 1
    again = bench.withdraw(DOC1, "2,3")
    assert said("refused: document_not_released: document", again.output), again.output


def test_finishing_a_withdrawal_never_overwrites_a_file_edited_by_hand(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forward replay, like a rollback, touches only a file holding its old or new bytes."""
    sha, request = _batch(bench, httpx_mock)
    assert bench.apply(sha, request).exit_code == 0
    commit_all(bench.clone, "owner: release batch")
    crash = Crash(monkeypatch, 2)
    bench.withdraw(DOC1, "2")
    monkeypatch.setattr(corpus_transaction, "replace", crash.real)
    index = bench.clone / "forarbeider" / "INDEX.md"
    index.write_bytes(b"edited by hand\n")

    result = bench.recover()

    assert said("refused: corpus_tree_mismatch: forarbeider", result.output), result.output
    assert index.read_bytes() == b"edited by hand\n"
    assert (bench.clone / JOURNAL).exists()
    assert bench.records("release_withdrawals") == []
