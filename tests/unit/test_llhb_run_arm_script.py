"""Stage 9: the frozen-run mode of the run_arm driver.

Ruling #25 kept the frozen dataset untouchable through every pilot;
Stage 9 is the one door in, and it is explicit: ``--frozen`` runs the
whole verified frozen set, refuses the pilot-only knobs, and labels the
run as the frozen evaluation. The pilot path is byte-for-byte the old
behavior.

The script is loaded via importlib (the agentic-ci scripts precedent):
these tests exercise argument validation, case selection and metadata
composition — never the CLI spawn.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner" / "run_arm.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_arm_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_arm_script = _load()


def args_for(*argv: str) -> object:
    return run_arm_script.parse_args(list(argv))


FROZEN_ARGS = ("--condition", "control", "--suffix", "frozen1", "--model", "claude-opus-5")


class TestArgumentContract:
    def test_frozen_selects_the_whole_frozen_dataset(self) -> None:
        args = args_for(*FROZEN_ARGS, "--frozen")

        cases, lock = run_arm_script.load_inputs(args)

        assert len(cases) == lock["case_count"] == 250
        assert [c["case_id"] for c in cases] == sorted(c["case_id"] for c in cases)

    def test_frozen_refuses_a_limit(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS, "--frozen", "--limit", "10")

    def test_frozen_refuses_a_candidates_override(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS, "--frozen", "--candidates", "somewhere.jsonl")

    def test_a_pilot_still_requires_a_limit(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS)

    def test_the_pilot_path_is_unchanged(self) -> None:
        args = args_for(*FROZEN_ARGS, "--limit", "10")

        cases, _ = run_arm_script.load_inputs(args)

        assert len(cases) == 10
        frozen_ids = {
            c["case_id"] for c in run_arm_script.load_cases_jsonl(run_arm_script.FROZEN_JSONL)
        }
        assert not frozen_ids & {c["case_id"] for c in cases}


class TestFrozenMetadata:
    def test_the_checksum_is_the_lock_checksum_and_the_note_says_frozen(self) -> None:
        """A frozen run's metadata must pass check_fairness --frozen by
        construction: same checksum as the lock, same pin, and a note
        that no longer disclaims the dataset."""
        args = args_for(*FROZEN_ARGS, "--frozen")
        cases, lock = run_arm_script.load_inputs(args)

        metadata = run_arm_script.compose(args, cases, lock, None)

        assert metadata["dataset_checksum"] == lock["dataset_sha256"]
        assert metadata["lovverk_commit"] == lock["corpus_pin"]["lovverk_commit"]
        assert "FROZEN dataset llhb-v1" in metadata["notes"]
        assert "NOT the frozen dataset" not in metadata["notes"]

    def test_a_pilot_note_still_disclaims_the_dataset(self) -> None:
        args = args_for(*FROZEN_ARGS, "--limit", "10")
        cases, lock = run_arm_script.load_inputs(args)

        metadata = run_arm_script.compose(args, cases, lock, None)

        assert "NOT the frozen dataset" in metadata["notes"]


class TestDryRunSummary:
    def test_the_frozen_summary_names_the_frozen_pool(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """codex-tests: the human-facing summary must not describe the
        frozen evaluation's cases as pilot drops."""
        monkeypatch.setattr(sys, "argv", ["run_arm.py", *FROZEN_ARGS, "--frozen"])

        run_arm_script.main()

        out = capsys.readouterr().out
        assert "drops only" not in out
        assert "(frozen llhb-v1)" in out

    def test_the_pilot_summary_still_says_drops(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["run_arm.py", *FROZEN_ARGS, "--limit", "3"])

        run_arm_script.main()

        assert "(drops only)" in capsys.readouterr().out
