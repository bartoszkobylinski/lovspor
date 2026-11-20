"""AR4a: crash recovery of a publication preparation (#738; §9.3 recovery table, §9.3a).

A crash is a ``BaseException`` raised from inside the step it interrupts, so no
handler of the code under test runs, as when the process dies. Recovery must
either reconstruct and verify the exact proposal or roll it back; it never
publishes. Synthetic only, in pytest's temporary directory.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import publication_clone, publication_stage
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.publication_models import PublicationTransaction
from lovspor.forarbeider.publication_prepare import (
    PreparationRequest,
    clone_path,
    prepare_publication,
    recover_preparation,
)
from tests.unit.forarbeider_publication_support import (
    git,
    plan_of,
    quarantine_of,
    sealed_plan,
    sealed_quarantine,
    snapshot,
    source_repository,
    synthetic_row,
    workdir,
)
from tests.unit.forarbeider_release_plan_support import Store


class Crash(BaseException):
    """The process dying: not an ``Exception``, so no handler under test catches it."""


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        tmp_path.mkdir(mode=0o700, exist_ok=True)
        store = Store()
        rows = [synthetic_row(store, 1), synthetic_row(store, 2)]
        self.source, self.base = source_repository(tmp_path)
        found = quarantine_of()
        plan = plan_of(store, rows, [1, 2], found)
        self.work = workdir(tmp_path)
        self.request = PreparationRequest(
            plan=sealed_plan(plan),
            quarantine=sealed_quarantine(found),
            batch_id=plan.batches[0].batch_id,
            rows=tuple(rows),
            artifacts=dict(store.artifacts),
            path="A",
            base_commit=self.base,
        )

    @property
    def clone(self) -> Path:
        return clone_path(self.work, self.request)

    def prepare(self, request: PreparationRequest | None = None) -> Any:
        return prepare_publication(request or self.request, self.source, self.work)

    def status(self) -> list[str]:
        raw = git(self.clone, "status", "--porcelain=v1", "--untracked-files=all", "--ignored")
        return sorted(raw.splitlines())

    def clean(self) -> bool:
        staging = self.clone / publication_stage.STAGING
        return (
            self.status() == []
            and git(self.clone, "rev-parse", "HEAD") == self.base
            and not (self.clone / publication_stage.JOURNAL).exists()
            and not staging.exists()
        )


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def crash_on(predicate: Callable[..., bool], original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any) -> Any:
        if predicate(*args):
            raise Crash
        return original(*args)

    return wrapped


def crash_in_replace(monkeypatch: pytest.MonkeyPatch, test: Callable[[Path], bool]) -> None:
    monkeypatch.setattr(
        publication_stage,
        "replace",
        crash_on(lambda _s, target: test(target), publication_stage.replace),
    )


def crash_on_state(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    monkeypatch.setattr(
        publication_stage,
        "save",
        crash_on(lambda _clone, journal: journal.state == state, publication_stage.save),
    )


def journal_state(rig: Rig) -> str:
    return str(json.loads((rig.clone / publication_stage.JOURNAL).read_text())["state"])


def prepared_transaction(tmp_path: Path) -> PublicationTransaction:
    other = Rig(tmp_path / "reference")
    outcome = other.prepare()
    assert outcome.transaction is not None
    return outcome.transaction


# --- every crash boundary ------------------------------------------------------------------


def test_a_crash_before_staging_leaves_nothing_to_undo(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_on_state(monkeypatch, "staging")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()

    assert recover_preparation(rig.clone, None).status == "nothing_pending"
    assert rig.clean()


def test_a_crash_after_partial_staging_rolls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    def second(*_args: Any) -> bool:
        calls["n"] += 1
        return calls["n"] == 2

    monkeypatch.setattr(
        publication_stage, "write_staged", crash_on(second, publication_stage.write_staged)
    )
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    assert journal_state(rig) == "staging"

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def test_a_crash_after_the_document_writes_rolls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    assert journal_state(rig) == "documents_written"
    assert (rig.clone / "documents").is_dir()

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def test_a_crash_after_the_index_write_rolls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.parent.name == "releases")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    assert journal_state(rig) == "index_written"

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def test_a_crash_after_the_manifest_write_rolls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_on_state(monkeypatch, "manifest_written")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    assert journal_state(rig) == "index_written"
    assert (rig.clone / "releases").is_dir()

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def _crash_in_read_back(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        publication_clone,
        "changed_paths",
        crash_on(lambda *_a: True, publication_clone.changed_paths),
    )
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    assert journal_state(rig) == "reading_back"


def test_a_crash_during_read_back_is_reconstructed_for_the_same_transaction(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _crash_in_read_back(rig, monkeypatch)
    expected = prepared_transaction(tmp_path)

    recovered = recover_preparation(rig.clone, expected)

    assert recovered.status == "reconstructed"
    assert recovered.transaction_id == expected.transaction_id
    assert journal_state(rig) == "prepared"
    assert git(rig.clone, "rev-parse", "HEAD") == rig.base


def test_a_crash_during_read_back_without_an_expectation_rolls_back(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _crash_in_read_back(rig, monkeypatch)

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def test_a_prepared_proposal_is_reconstructed_before_authorization(
    rig: Rig, tmp_path: Path
) -> None:
    outcome = rig.prepare()
    expected = prepared_transaction(tmp_path)
    assert outcome.transaction == expected

    recovered = recover_preparation(rig.clone, expected)

    assert recovered.status == "reconstructed"
    assert rig.status() == sorted(f"?? {item.path}" for item in expected.content.files)


def test_a_prepared_proposal_for_another_transaction_is_rolled_back(
    rig: Rig, tmp_path: Path
) -> None:
    rig.prepare()
    other = Rig(tmp_path / "other")
    expected = other.prepare(other.request._replace(path="B")).transaction

    assert recover_preparation(rig.clone, expected).status == "rolled_back"
    assert rig.clean()


def test_a_prepared_proposal_whose_bytes_changed_is_rolled_back(rig: Rig, tmp_path: Path) -> None:
    outcome = rig.prepare()
    assert outcome.transaction is not None
    target = rig.clone / "INDEX.md"
    target.write_bytes(target.read_bytes())  # same bytes: still reconstructed
    assert recover_preparation(rig.clone, outcome.transaction).status == "reconstructed"
    (rig.clone / "documents" / "regjeringen-9000001.md").write_bytes(b"edited\n")

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover_preparation(rig.clone, outcome.transaction)
    assert raised.value.reason is RefusalReason.CORPUS_TREE_MISMATCH
    assert (rig.clone / publication_stage.JOURNAL).exists()


# --- rollback is resumable, never forward ---------------------------------------------------


def test_a_crash_during_rollback_is_resumed_by_recovery(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.parent.name == "releases")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    monkeypatch.setattr(
        publication_stage,
        "unlink",
        crash_on(lambda path: path.name == "INDEX.md", publication_stage.unlink),
    )
    with pytest.raises(Crash):
        recover_preparation(rig.clone, None)
    monkeypatch.undo()
    assert journal_state(rig) == "rolling_back"

    assert recover_preparation(rig.clone, None).status == "rolled_back"
    assert rig.clean()


def test_a_file_edited_after_the_crash_is_never_overwritten(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    edited = rig.clone / "documents" / "regjeringen-9000001.md"
    edited.write_bytes(b"someone else's bytes\n")

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover_preparation(rig.clone, None)
    assert raised.value.reason is RefusalReason.CORPUS_TREE_MISMATCH
    assert edited.read_bytes() == b"someone else's bytes\n"
    assert (rig.clone / publication_stage.JOURNAL).exists()


def test_a_preparation_refuses_a_clone_with_a_pending_journal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()

    with pytest.raises(ForarbeiderRefusedError) as raised:
        rig.prepare()
    assert raised.value.reason is RefusalReason.CHECKOUT_PENDING_CHANGE


def test_after_recovery_the_same_request_prepares_the_same_transaction(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    recover_preparation(rig.clone, None)

    outcome = rig.prepare()

    assert outcome.status == "prepared"
    assert outcome.transaction == prepared_transaction(tmp_path)


# --- forged journals ------------------------------------------------------------------------


def _forge(rig: Rig, edit: Callable[[dict[str, Any]], None]) -> None:
    path = rig.clone / publication_stage.JOURNAL
    raw = json.loads(path.read_text())
    edit(raw)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def _set_id(raw: dict[str, Any]) -> None:
    raw["transaction"]["transaction_id"] = "0" * 64


def _outside(raw: dict[str, Any]) -> None:
    raw["operations"][0]["path"] = "README.md"


def _traversal(raw: dict[str, Any]) -> None:
    raw["operations"][0]["path"] = "documents/../../outside.md"


def _unknown_state(raw: dict[str, Any]) -> None:
    raw["state"] = "published"


def _dropped(raw: dict[str, Any]) -> None:
    raw["operations"] = raw["operations"][1:]


def _content(raw: dict[str, Any]) -> None:
    raw["transaction"]["content"]["base_commit_note"] = "x"


@pytest.mark.parametrize(
    "edit", [_set_id, _outside, _traversal, _unknown_state, _dropped, _content]
)
def test_a_forged_journal_is_refused_and_nothing_is_touched(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, edit: Callable[[dict[str, Any]], None]
) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    before = rig.status()
    _forge(rig, edit)

    with pytest.raises(ForarbeiderRefusedError) as raised:
        recover_preparation(rig.clone, None)
    assert raised.value.reason is RefusalReason.CORPUS_TREE_MISMATCH
    assert rig.status() == before


def test_a_journal_that_is_a_symlink_is_refused(rig: Rig, monkeypatch: pytest.MonkeyPatch) -> None:
    crash_in_replace(monkeypatch, lambda target: target.name == "INDEX.md")
    with pytest.raises(Crash):
        rig.prepare()
    monkeypatch.undo()
    journal = rig.clone / publication_stage.JOURNAL
    moved = journal.with_name("elsewhere.json")
    journal.rename(moved)
    journal.symlink_to(moved)

    with pytest.raises(ForarbeiderRefusedError):
        recover_preparation(rig.clone, None)


# --- recovery never publishes ---------------------------------------------------------------


def test_recovery_never_commits_pushes_or_touches_the_source(
    rig: Rig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before = snapshot(rig.source)
    _crash_in_read_back(rig, monkeypatch)
    recover_preparation(rig.clone, prepared_transaction(tmp_path))
    recover_preparation(rig.clone, None)

    assert snapshot(rig.source) == before
    assert git(rig.clone, "rev-list", "--count", "HEAD") == "1"
    assert git(rig.clone, "remote") == ""
