"""A journaled change finishes only under the authority that started it (#738, PR3B-2).

Review of PR #773 (P1): ``simulate`` ran ``recover`` before it re-checked the
review, evidence, checkpoints, revocation and the machine clock, so a release
interrupted after its journal and revoked meanwhile was finished by the next
run, and an applied plan retried after a revocation still answered
``UNCHANGED``.

Now a journal names its action and the plan or withdrawal intent behind it.
``simulate`` re-checks the plan before anything else, finishes only its own
journal, and rolls an interrupted release back when the plan no longer
holds. ``sandbox-withdraw`` needs no grant: it rolls back any interrupted
release and finishes an interrupted withdrawal, so a takedown never waits.
"""

import json
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_commands, corpus_transaction
from lovspor.forarbeider.corpus_plan import release_draft
from lovspor.forarbeider.corpus_transaction import (
    JOURNAL,
    STAGING,
    Binding,
    apply,
    pending,
    roll_back,
)
from lovspor.forarbeider.corpus_tree import create_sandbox, read_dataset
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.release_models import digest
from tests.unit.cli_output import said
from tests.unit.forarbeider_promotion_support import (
    eligible,
    plan,
    planned,
    release_options,
    sandbox,
    simulate,
    tree,
    withdraw,
)
from tests.unit.forarbeider_release_support import (
    OWNER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)
from tests.unit.test_forarbeider_corpus_golden import partial

pytestmark = pytest.mark.usefixtures("at_a_terminal")

DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"
INDEX = "forarbeider/INDEX.md"
MANIFEST = "forarbeider/manifest.json"
STOPS = [DOCUMENT, INDEX, MANIFEST]
DECISION = Binding("release", "a" * 64)


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "desk")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    return path


def _planned_with_report(desk: Desk, box: Path) -> tuple[str, str]:
    report, checkpoints = eligible(desk)
    result = plan(desk, report, checkpoints, box)
    assert result.exit_code == 0, result.output
    return said_digest(result.output, "plan"), report


def _revoke(desk: Desk, report: str) -> None:
    stored = json.loads((desk.workspace / "reports" / f"{report}.json").read_text())
    revoke = ["revoke", "--authorization", stored["authorization_sha256"]]
    result = desk.invoke(
        [*release_options(desk), *revoke, "--decided", f"{OWNER}@2100-04-09T00:00:00Z"]
    )
    assert result.exit_code == 0, result.output


def _expire(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        corpus_commands,
        "_now",
        lambda: corpus_commands.datetime(2101, 1, 1, tzinfo=corpus_commands.UTC),
    )


def _interrupt_after(monkeypatch: pytest.MonkeyPatch, stop_after: str) -> None:
    real = corpus_transaction._perform

    def interrupt(root: Path, operation: corpus_transaction.Operation) -> None:
        real(root, operation)
        if operation.path == stop_after:
            raise OSError("interrupted")

    monkeypatch.setattr(corpus_transaction, "_perform", interrupt)


def _crash(desk: Desk, box: Path, sha: str, stop_after: str) -> None:
    with pytest.MonkeyPatch.context() as patch:
        _interrupt_after(patch, stop_after)
        crashed = simulate(desk, sha, box)
    assert crashed.exit_code == 1, crashed.output
    assert (box / JOURNAL).exists()


def _clean(box: Path) -> dict[str, bytes]:
    """The sandbox outside the change machinery: what a reader of the tree would see."""
    return {
        path: payload
        for path, payload in tree(box).items()
        if not path.startswith(".git/lovspor-forarbeider")
    }


@pytest.mark.parametrize("stop_after", STOPS)
@pytest.mark.parametrize("lapse", ["revoked", "expired"])
def test_an_interrupted_release_whose_grant_lapsed_is_rolled_back_not_finished(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch, stop_after: str, lapse: str
) -> None:
    sha, report = _planned_with_report(desk, box)
    before = _clean(box)
    _crash(desk, box, sha, stop_after)
    if lapse == "revoked":
        _revoke(desk, report)
    else:
        _expire(monkeypatch)

    result = simulate(desk, sha, box)

    assert result.exit_code == 2, result.output
    assert said("refused: promotion_denied", result.output)
    assert _clean(box) == before, "a lapsed release was left on disk"
    assert read_dataset(box).manifest_sha256 is None
    assert not (box / JOURNAL).exists()
    assert not (box / STAGING).exists()


@pytest.mark.parametrize("lapse", ["revoked", "expired"])
def test_an_applied_plan_retried_after_its_grant_lapsed_is_denied(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch, lapse: str
) -> None:
    sha, report = _planned_with_report(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    after = tree(box)
    if lapse == "revoked":
        _revoke(desk, report)
    else:
        _expire(monkeypatch)

    result = simulate(desk, sha, box)

    assert result.exit_code == 2, result.output
    assert said("refused: promotion_denied", result.output)
    assert "UNCHANGED" not in result.output
    assert tree(box) == after, "a denial changes nothing; taking it down is a withdrawal"


@pytest.mark.parametrize("stop_after", STOPS)
def test_an_interrupted_release_still_authorized_is_finished(
    desk: Desk, box: Path, stop_after: str
) -> None:
    sha = planned(desk, box)
    _crash(desk, box, sha, stop_after)
    planned_manifest = json.loads((desk.workspace / "plans" / f"{sha}.json").read_text())
    target = next(item["sha256"] for item in planned_manifest["writes"] if item["path"] == MANIFEST)

    result = simulate(desk, sha, box)

    assert result.exit_code == 0, result.output
    assert "recovered_interrupted_change" in result.output
    assert digest((box / MANIFEST).read_bytes()) == target
    assert not (box / JOURNAL).exists()


def test_another_plans_journal_is_refused_and_left_alone(tmp_path: Path) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    stale_free = planned(second, path)
    _crash(first, path, planned(first, path), INDEX)
    before = tree(path)

    result = simulate(second, stale_free, path)

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert tree(path) == before


def test_a_pending_withdrawal_journal_blocks_a_release_simulate(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    with pytest.MonkeyPatch.context() as patch:
        _interrupt_after(patch, INDEX)
        assert withdraw(desk, box, "regjeringen-9000001").exit_code == 1
    assert (box / JOURNAL).exists()
    before = tree(box)

    result = simulate(desk, sha, box)

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert tree(box) == before, "a withdrawal's journal is left for a withdrawal to finish"
    assert (box / JOURNAL).exists()


def test_a_journal_naming_an_unknown_plan_is_refused_by_withdrawal(desk: Desk, box: Path) -> None:
    sha = planned(desk, box)
    _crash(desk, box, sha, INDEX)
    journal = json.loads((box / JOURNAL).read_text())
    journal["decision_sha256"] = "f" * 64
    (box / JOURNAL).write_text(json.dumps(journal))
    before = tree(box)

    result = withdraw(desk, box, "regjeringen-9000001")

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert tree(box) == before


def test_withdrawal_rolls_back_an_interrupted_release_of_another_document(
    tmp_path: Path,
) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    second.workspace = first.workspace  # one owner, one private workspace
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    assert simulate(first, planned(first, path), path).exit_code == 0
    released = read_dataset(path).manifest_sha256
    _crash(second, path, planned(second, path), INDEX)

    result = withdraw(first, path, "regjeringen-9000001")

    assert result.exit_code == 0, result.output
    documents = read_dataset(path).manifest.documents
    assert [(item.document.id, item.release_status) for item in documents] == [
        ("regjeringen-9000001", "withdrawn")
    ], "the interrupted release was finished by a withdrawal"
    assert not (path / "forarbeider/2099-2100/prop-ls").exists()
    assert not (path / JOURNAL).exists()
    intents = list((first.workspace / "withdrawal_intents").glob("*.json"))
    assert json.loads(intents[0].read_text())["base_manifest_sha256"] == released


@pytest.mark.parametrize("stop_after", STOPS)
def test_withdrawal_takes_down_an_interrupted_release_of_the_same_document(
    desk: Desk, box: Path, stop_after: str
) -> None:
    before = _clean(box)
    sha, report = _planned_with_report(desk, box)
    _crash(desk, box, sha, stop_after)
    _revoke(desk, report)

    result = withdraw(desk, box, "regjeringen-9000001")

    assert _clean(box) == before, "the interrupted release is gone"
    assert not (box / JOURNAL).exists()
    assert result.exit_code == 1, result.output
    assert said("refused: document_not_released: document", result.output)


def test_a_release_whose_grant_was_revoked_can_still_be_withdrawn(desk: Desk, box: Path) -> None:
    sha, report = _planned_with_report(desk, box)
    assert simulate(desk, sha, box).exit_code == 0
    _revoke(desk, report)

    result = withdraw(desk, box, "regjeringen-9000001")

    assert result.exit_code == 0, result.output
    assert read_dataset(box).manifest.documents[0].release_status == "withdrawn"
    assert not (box / DOCUMENT).exists()


def test_a_release_journal_from_another_workspace_is_refused_by_withdrawal(
    tmp_path: Path,
) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    assert simulate(first, planned(first, path), path).exit_code == 0
    _crash(second, path, planned(second, path), INDEX)
    before = tree(path)

    result = withdraw(first, path, "regjeringen-9000001")

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert tree(path) == before


def test_rolling_back_a_replacement_restores_the_member_it_replaced(tmp_path: Path) -> None:
    box = create_sandbox(tmp_path / "box", ())
    first = partial(tmp_path)
    with corpus_transaction.locked(box):
        apply(box, release_draft(read_dataset(box), first), DECISION)
    before = tree(box)
    replacement = release_draft(read_dataset(box), first._replace(chapters=first.chapters[:1]))
    with pytest.MonkeyPatch.context() as patch:
        _interrupt_after(patch, MANIFEST)
        with corpus_transaction.locked(box), pytest.raises(ForarbeiderRefusedError):
            apply(box, replacement, DECISION)
    journal = pending(box)
    assert journal is not None
    assert tree(box)[DOCUMENT] != before[DOCUMENT]

    with corpus_transaction.locked(box):
        roll_back(box, journal)

    assert tree(box) == before


def test_a_file_this_change_did_not_write_stops_the_rollback(desk: Desk, box: Path) -> None:
    sha, report = _planned_with_report(desk, box)
    _crash(desk, box, sha, INDEX)
    (box / DOCUMENT).write_bytes(b"edited by someone else")
    _revoke(desk, report)

    result = simulate(desk, sha, box)

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: forarbeider", result.output)
    assert (box / DOCUMENT).read_bytes() == b"edited by someone else"
    assert (box / JOURNAL).exists(), "nothing is guessed: the owner sees the journal"


def test_a_journal_without_its_manifest_is_not_rolled_back(tmp_path: Path) -> None:
    box = create_sandbox(tmp_path / "box", ())
    operation = corpus_transaction.Operation(path=INDEX, sha256=None, previous_sha256=None)
    journal = corpus_transaction.Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        action="release",
        decision_sha256="a" * 64,
        target_manifest_sha256="a" * 64,
        operations=(operation, operation),
    )

    with pytest.raises(ForarbeiderRefusedError) as caught:
        roll_back(box, journal)

    assert (caught.value.reason, caught.value.detail) == (
        RefusalReason.CORPUS_TREE_MISMATCH,
        "journal",
    )


def test_a_rollback_that_cannot_write_keeps_its_journal(desk: Desk, box: Path) -> None:
    sha, report = _planned_with_report(desk, box)
    _crash(desk, box, sha, MANIFEST)
    _revoke(desk, report)
    folder = (box / DOCUMENT).parent
    folder.chmod(0o500)
    try:
        result = simulate(desk, sha, box)
    finally:
        folder.chmod(0o700)

    assert result.exit_code == 1, result.output
    assert said("refused: output_write_failed: forarbeider", result.output)
    assert (box / JOURNAL).exists()
    assert (box / DOCUMENT).exists()


@pytest.mark.parametrize("damage", ["decision", "intent"])
def test_a_withdrawal_journal_must_name_its_stored_intent(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    assert simulate(desk, planned(desk, box), box).exit_code == 0
    with pytest.MonkeyPatch.context() as patch:
        _interrupt_after(patch, INDEX)
        assert withdraw(desk, box, "regjeringen-9000001").exit_code == 1
    if damage == "decision":
        journal = json.loads((box / JOURNAL).read_text())
        journal["decision_sha256"] = "f" * 64
        (box / JOURNAL).write_text(json.dumps(journal))
    else:
        for path in (desk.workspace / "withdrawal_intents").iterdir():
            path.unlink()
    before = tree(box)

    result = withdraw(desk, box, "regjeringen-9000001")

    assert result.exit_code == 1, result.output
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert tree(box) == before
