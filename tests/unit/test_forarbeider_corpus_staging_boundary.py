"""The synthetic simulator must keep staging writes inside its sandbox (#738)."""

from pathlib import Path

import pytest

from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK, STAGING
from tests.unit.cli_output import said
from tests.unit.forarbeider_promotion_support import planned, sandbox, simulate, tree
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401


@pytest.mark.usefixtures("at_a_terminal")
def test_simulate_refuses_symlinked_staging_without_writing_outside_sandbox(tmp_path: Path) -> None:
    """operations.md: simulate refuses a symlink and writes only its disposable sandbox."""
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "sandbox"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "sentinel").write_bytes(b"unrelated private data")
    (box / STAGING).symlink_to(outside, target_is_directory=True)
    before_box = tree(box)
    before_outside = tree(outside)

    result = simulate(desk, sha, box)

    # Check the filesystem first: a refusal after writing is still a contract violation.
    assert tree(outside) == before_outside, "simulation wrote outside its sandbox through staging"
    assert tree(box) == before_box
    assert (box / STAGING).is_symlink()
    assert not (box / JOURNAL).exists()
    assert not (box / LOCK).exists()
    assert result.exit_code == 1, result.output
    assert said("refused: unsafe_output_path: staging", result.output)


@pytest.mark.usefixtures("at_a_terminal")
def test_a_symlinked_journal_is_refused_not_read(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    box = tmp_path / "sandbox"
    assert sandbox(desk, box).exit_code == 0
    sha = planned(desk, box)
    (tmp_path / "elsewhere.json").write_text("{}")
    (box / JOURNAL).symlink_to(tmp_path / "elsewhere.json")

    result = simulate(desk, sha, box)

    assert result.exit_code == 1
    assert said("refused: corpus_tree_mismatch: journal", result.output)
    assert not (box / "forarbeider").exists()
