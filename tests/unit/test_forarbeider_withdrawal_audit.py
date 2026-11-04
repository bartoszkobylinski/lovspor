"""A withdrawal's private audit is written ahead and always completed (#738, PR3B-2).

Review of PR #773 (P2): the sandbox was changed first and the receipt written
after. When the receipt write failed the document was gone, nothing recorded
it, and a retry refused because the document was already withdrawn.

Now an intent, tied to the owner's decision, the document and the manifest
digests before and after, is stored before the sandbox changes. A retry
finishes an interrupted change and writes the receipt the intent promised,
once, without undoing the takedown.
"""

import json
from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_transaction
from lovspor.forarbeider.corpus_transaction import JOURNAL
from lovspor.forarbeider.corpus_tree import read_dataset
from lovspor.forarbeider.release_models import digest
from tests.unit.cli_output import said
from tests.unit.forarbeider_promotion_support import (
    WITHDRAWN,
    planned,
    sandbox,
    simulate,
    tree,
    withdraw,
)
from tests.unit.forarbeider_release_support import Desk, at_a_terminal, said_digest  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")

DOCUMENT_ID = "regjeringen-9000001"
DOCUMENT = "forarbeider/2099-2100/prop-l/9000001.md"
MANIFEST = "forarbeider/manifest.json"


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path / "desk")


@pytest.fixture
def box(desk: Desk, tmp_path: Path) -> Path:
    """A sandbox with the synthetic Prop. 901 L released into it."""
    path = tmp_path / "sandbox"
    assert sandbox(desk, path).exit_code == 0
    assert simulate(desk, planned(desk, path), path).exit_code == 0
    return path


def _records(desk: Desk, folder: str) -> dict[str, dict[str, object]]:
    directory = desk.workspace / folder
    if not directory.exists():
        return {}
    return {path.stem: json.loads(path.read_text()) for path in directory.glob("*.json")}


def _withdrawn(box: Path) -> bool:
    member = read_dataset(box).manifest.documents[0]
    return member.release_status == "withdrawn" and not (box / DOCUMENT).exists()


def _collide(desk: Desk, folder: str) -> Path:
    """A file where the private folder must go: the write there fails."""
    blocker = desk.workspace / folder
    blocker.write_text("not a folder")
    return blocker


def test_the_intent_is_stored_before_the_sandbox_changes(desk: Desk, box: Path) -> None:
    before = tree(box)
    _collide(desk, "withdrawal_intents")

    result = withdraw(desk, box, DOCUMENT_ID)

    assert result.exit_code == 1, result.output
    assert tree(box) == before, "the sandbox changed although no intent was recorded"
    assert _records(desk, "withdrawals") == {}


def test_a_receipt_that_fails_after_the_change_is_completed_by_the_retry(
    desk: Desk, box: Path
) -> None:
    base = digest((box / MANIFEST).read_bytes())
    blocker = _collide(desk, "withdrawals")

    failed = withdraw(desk, box, DOCUMENT_ID)

    assert failed.exit_code == 1, failed.output
    assert _withdrawn(box), "the takedown must stand although its receipt failed"
    result_manifest = digest((box / MANIFEST).read_bytes())
    blocker.unlink()

    retried = withdraw(desk, box, DOCUMENT_ID, "2100-05-01T00:00:00Z")

    assert retried.exit_code == 0, retried.output
    assert digest((box / MANIFEST).read_bytes()) == result_manifest, "the retry changed nothing"
    intents = _records(desk, "withdrawal_intents")
    receipts = _records(desk, "withdrawals")
    assert list(intents) == [result_manifest]
    intent = intents[result_manifest]
    assert intent["base_manifest_sha256"] == base
    assert intent["result_manifest_sha256"] == result_manifest
    assert intent["document_id"] == DOCUMENT_ID
    assert intent["withdrawn_at"] == WITHDRAWN, "the original decision, not the retry's"
    intent_sha = digest(
        (desk.workspace / "withdrawal_intents" / f"{result_manifest}.json").read_bytes()
    )
    ((name, receipt),) = receipts.items()
    assert name == intent_sha, "the receipt is stored under its intent: one per decision"
    receipt_bytes = (desk.workspace / "withdrawals" / f"{name}.json").read_bytes()
    assert said_digest(retried.output, "record") == digest(receipt_bytes)
    assert receipt["intent_sha256"] == intent_sha
    assert receipt["manifest_sha256"] == result_manifest
    assert receipt["withdrawn_at"] == WITHDRAWN


def test_a_withdrawal_with_its_receipt_is_not_repeated(desk: Desk, box: Path) -> None:
    first = withdraw(desk, box, DOCUMENT_ID)
    assert first.exit_code == 0, first.output
    after = tree(box)
    receipts = _records(desk, "withdrawals")

    again = withdraw(desk, box, DOCUMENT_ID)

    assert again.exit_code == 1, again.output
    assert said("refused: document_not_released: document", again.output)
    assert tree(box) == after
    assert _records(desk, "withdrawals") == receipts
    assert len(_records(desk, "withdrawal_intents")) == 1


@pytest.mark.parametrize("stop_after", [DOCUMENT, "forarbeider/INDEX.md", MANIFEST])
def test_an_interrupted_withdrawal_is_finished_and_recorded_by_the_retry(
    desk: Desk, box: Path, monkeypatch: pytest.MonkeyPatch, stop_after: str
) -> None:
    real = corpus_transaction._perform

    def interrupt(root: Path, operation: corpus_transaction.Operation) -> None:
        real(root, operation)
        if operation.path == stop_after:
            raise OSError("interrupted")

    monkeypatch.setattr(corpus_transaction, "_perform", interrupt)
    assert withdraw(desk, box, DOCUMENT_ID).exit_code == 1
    assert (box / JOURNAL).exists()
    assert _records(desk, "withdrawals") == {}
    monkeypatch.setattr(corpus_transaction, "_perform", real)

    retried = withdraw(desk, box, DOCUMENT_ID)

    assert retried.exit_code == 0, retried.output
    assert _withdrawn(box)
    assert not (box / JOURNAL).exists()
    (receipt,) = _records(desk, "withdrawals").values()
    assert receipt["manifest_sha256"] == digest((box / MANIFEST).read_bytes())


def test_an_intent_for_another_document_does_not_answer_for_this_one(
    tmp_path: Path,
) -> None:
    first = Desk(tmp_path / "l")
    second = Desk(tmp_path / "ls", kind="ls")
    path = tmp_path / "sandbox"
    assert sandbox(first, path).exit_code == 0
    assert simulate(first, planned(first, path), path).exit_code == 0
    assert simulate(second, planned(second, path), path).exit_code == 0
    assert withdraw(first, path, DOCUMENT_ID).exit_code == 0

    other = withdraw(first, path, "regjeringen-9000003")

    assert other.exit_code == 0, other.output
    assert len(_records(first, "withdrawals")) == 2
    assert "refused" not in other.output


def test_withdrawing_from_an_empty_sandbox_records_nothing(desk: Desk, tmp_path: Path) -> None:
    path = tmp_path / "empty"
    assert sandbox(desk, path).exit_code == 0
    before = tree(path)

    result = withdraw(desk, path, DOCUMENT_ID)

    assert result.exit_code == 1, result.output
    assert said("refused: document_not_released: document", result.output)
    assert tree(path) == before
    assert _records(desk, "withdrawal_intents") == {}
    assert _records(desk, "withdrawals") == {}
