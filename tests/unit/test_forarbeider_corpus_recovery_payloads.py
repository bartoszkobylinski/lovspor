"""Interrupted promotion must not trust missing or corrupted staged bytes (#738)."""

from pathlib import Path

import pytest

from lovspor.forarbeider import corpus_transaction as transaction
from lovspor.forarbeider.corpus_tree import read_dataset
from tests.unit.cli_output import said
from tests.unit.forarbeider_promotion_support import planned, sandbox, simulate, tree
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401


@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize(
    "boundary",
    [
        "forarbeider/2099-2100/prop-l/9000001.md",
        "forarbeider/INDEX.md",
        "forarbeider/manifest.json",
    ],
)
@pytest.mark.parametrize(
    "damage,diagnostic",
    [
        ("corrupted", "corpus_tree_mismatch: staging"),
        ("missing", "output_write_failed: forarbeider"),
    ],
)
def test_recovery_refuses_damaged_payload_and_keeps_the_change_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str, diagnostic: str, boundary: str
) -> None:
    """Enforce operations.md: staged digests, verified recovery, journal kept on failure."""
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "sandbox"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    replace = transaction.replace

    def interrupt(source: Path, target: Path) -> None:
        if target == box / boundary:
            raise OSError("interrupted before installing the selected payload")
        replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(transaction, "replace", interrupt)
        result = simulate(desk, sha, box)
    assert result.exit_code == 1, result.output
    assert not (box / boundary).exists()
    journal_path = box / transaction.JOURNAL
    journal_bytes = journal_path.read_bytes()
    journal = transaction.Journal.model_validate_json(journal_bytes)
    if boundary == journal.operations[0].path:
        # Parent directories exist, but the first payload was never installed.
        assert not any(path.is_file() for path in (box / "forarbeider").rglob("*"))
    operation = next(item for item in journal.operations if item.path == boundary)
    assert operation.sha256 is not None
    staged = box / transaction.STAGING / operation.sha256
    original = staged.read_bytes()
    if damage == "missing":
        staged.unlink()
    else:
        staged.write_bytes(b"corrupted staged payload")
    before = tree(box)

    refused = simulate(desk, sha, box)

    assert refused.exit_code == 1, refused.output
    assert said(f"refused: {diagnostic}", refused.output)
    assert tree(box) == before
    assert journal_path.read_bytes() == journal_bytes
    assert not (box / transaction.LOCK).exists()

    staged.write_bytes(original)
    recovered = simulate(desk, sha, box)

    assert recovered.exit_code == 0, recovered.output
    assert "recovered_interrupted_change" in recovered.output
    assert read_dataset(box).manifest_sha256 == journal.target_manifest_sha256
    assert (box / operation.path).read_bytes() == original
    assert not journal_path.exists()
    assert not (box / transaction.STAGING).exists()
    assert not (box / transaction.LOCK).exists()
