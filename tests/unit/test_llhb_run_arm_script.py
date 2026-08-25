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
import json
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

    def test_frozen_never_reads_the_default_candidate_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Stage 9 input path is limited to the locked dataset."""
        args = args_for(*FROZEN_ARGS, "--frozen")
        original_load = run_arm_script.load_cases_jsonl

        def load_unless_candidates(path: Path) -> list[dict[str, object]]:
            assert path != run_arm_script.DEFAULT_CANDIDATES
            return original_load(path)

        monkeypatch.setattr(run_arm_script, "load_cases_jsonl", load_unless_candidates)

        cases, _ = run_arm_script.load_inputs(args)

        assert len(cases) == 250

    def test_frozen_fails_closed_when_lock_verification_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        args = args_for(*FROZEN_ARGS, "--frozen")

        def reject_dataset(cases: object, lock: object) -> None:
            raise ValueError("frozen lock mismatch")

        monkeypatch.setattr(run_arm_script, "verify_frozen_against_lock", reject_dataset)

        with pytest.raises(ValueError, match="frozen lock mismatch"):
            run_arm_script.load_inputs(args)

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


STABILITY_ARGS = (*FROZEN_ARGS, "--stability", "--repeat", "2")


class TestStabilityMode:
    def test_stability_selects_exactly_the_committed_subset(self) -> None:
        args = args_for(*STABILITY_ARGS)

        cases, _ = run_arm_script.load_inputs(args)

        committed = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        assert [c["case_id"] for c in cases] == sorted(committed["case_ids"])
        assert len(cases) == 30

    def test_stability_refuses_every_pilot_knob_and_frozen(self) -> None:
        for extra in (("--limit", "10"), ("--candidates", "x.jsonl"), ("--frozen",)):
            with pytest.raises(SystemExit):
                args_for(*STABILITY_ARGS, *extra)

    def test_stability_and_repeat_go_together(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS, "--stability")
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS, "--repeat", "1")

    def test_repeat_index_is_bounded_to_the_ruled_five(self) -> None:
        for bad in ("0", "6"):
            with pytest.raises(SystemExit):
                args_for(*FROZEN_ARGS, "--stability", "--repeat", bad)

    def test_stability_fails_closed_on_subset_dataset_drift(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A subset drawn from a different frozen dataset must never run."""
        stale = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        stale["dataset_sha256"] = "0" * 64
        stale_path = tmp_path / "llhb-v1-stability30.json"
        stale_path.write_text(json.dumps(stale), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", stale_path)

        with pytest.raises(run_arm_script.LovsporError, match="different frozen dataset"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_fails_closed_on_missing_subset_ids(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        broken = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        broken["case_ids"][0] = "llhb-v1-C1-999999"
        broken_path = tmp_path / "llhb-v1-stability30.json"
        broken_path.write_text(json.dumps(broken), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", broken_path)

        with pytest.raises(run_arm_script.LovsporError, match="missing from the frozen dataset"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_fails_closed_on_subset_checksum_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The checksum must authenticate the exact cases passed to the runner."""
        tampered = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        tampered["case_ids"][0] = "llhb-v1-C1-101"
        tampered_path = tmp_path / "llhb-v1-stability30.json"
        tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", tampered_path)

        with pytest.raises(run_arm_script.LovsporError, match=r"subset.*checksum|checksum.*subset"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_fails_closed_on_duplicate_subset_ids(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Duplicate IDs must not silently turn the ruled 30 cases into 29."""
        duplicated = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        duplicated["case_ids"][0] = duplicated["case_ids"][1]
        duplicated_path = tmp_path / "llhb-v1-stability30.json"
        duplicated_path.write_text(json.dumps(duplicated), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", duplicated_path)

        with pytest.raises(run_arm_script.LovsporError, match=r"duplicate|30"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_fails_closed_on_declared_size_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The artifact must not claim a different sample size than its IDs."""
        broken = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        broken["size"] = 29
        broken_path = tmp_path / "llhb-v1-stability30.json"
        broken_path.write_text(json.dumps(broken), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", broken_path)

        with pytest.raises(run_arm_script.LovsporError, match=r"size|30"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_fails_closed_on_declared_allocation_mismatch(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The ruled category allocation must describe the cases actually run."""
        broken = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        broken["allocation"]["C1"] -= 1
        broken["allocation"]["C2"] += 1
        broken_path = tmp_path / "llhb-v1-stability30.json"
        broken_path.write_text(json.dumps(broken), encoding="utf-8")
        monkeypatch.setattr(run_arm_script, "STABILITY_SUBSET", broken_path)

        with pytest.raises(run_arm_script.LovsporError, match=r"allocation|categor"):
            run_arm_script.load_inputs(args_for(*STABILITY_ARGS))

    def test_stability_notes_name_the_subset_and_the_repeat(self) -> None:
        args = args_for(*STABILITY_ARGS)
        cases, lock = run_arm_script.load_inputs(args)

        metadata = run_arm_script.compose(args, cases, lock, None)

        assert "STABILITY subset 30x5 (ruling #26), repeat 2/5" in metadata["notes"]
        assert "NOT the frozen dataset" not in metadata["notes"]

    def test_stability_checksum_is_over_the_subset_actually_run(self) -> None:
        """dataset_checksum stays honest: the 30 cases run, not the 250."""
        args = args_for(*STABILITY_ARGS)
        cases, lock = run_arm_script.load_inputs(args)

        metadata = run_arm_script.compose(args, cases, lock, None)

        committed = json.loads(run_arm_script.STABILITY_SUBSET.read_text(encoding="utf-8"))
        assert metadata["dataset_checksum"] == committed["subset_sha256"]
        assert metadata["dataset_checksum"] != lock["dataset_sha256"]

    def test_the_stability_summary_names_the_subset_pool(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["run_arm.py", *STABILITY_ARGS])

        run_arm_script.main()

        out = capsys.readouterr().out
        assert "(stability-30 of frozen llhb-v1)" in out
        assert "drops only" not in out


CHAT_ARGS = (
    "--condition",
    "control",
    "--suffix",
    "nmctrl1",
    "--model",
    "NorMistral-11b-thinking:latest",
    "--driver",
    "openai-chat",
    "--provider",
    "norallm",
)


class TestChatDriverArguments:
    def test_the_chat_driver_is_control_only(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*CHAT_ARGS[2:], "--condition", "lovspor", "--limit", "10")

    def test_the_chat_driver_requires_a_provider(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*CHAT_ARGS[:8], "--limit", "10")

    def test_the_cli_driver_refuses_a_foreign_provider(self) -> None:
        with pytest.raises(SystemExit):
            args_for(*FROZEN_ARGS, "--frozen", "--provider", "norallm")

    def test_the_cli_driver_defaults_to_anthropic(self) -> None:
        args = args_for(*FROZEN_ARGS, "--frozen")

        assert args.provider == "anthropic"

    def test_chat_metadata_records_provider_and_sampling(self) -> None:
        args = args_for(*CHAT_ARGS, "--frozen", "--temperature", "0.0")
        cases, lock = run_arm_script.load_inputs(args)

        metadata = run_arm_script.compose(args, cases, lock, None)

        assert metadata["provider"] == "norallm"
        assert metadata["sampling"] == {"temperature": 0.0}
        assert metadata["tool_config"] is None
        assert "driver=openai-chat base_url=https://chat.llm.sigma2.no/api" in metadata["notes"]

    def test_chat_dry_run_needs_no_key_and_does_not_execute(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LLHB_OPENAI_CHAT_API_KEY", raising=False)
        monkeypatch.setattr(sys, "argv", ["run_arm.py", *CHAT_ARGS, "--limit", "1"])

        assert run_arm_script.main() == 0

        out = capsys.readouterr().out
        assert "DRY RUN - first-case request" in out
        assert '"tools"' not in out
        assert "re-run with --execute to post the requests" in out

    def test_the_chat_driver_needs_no_claude_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLHB_CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setattr(run_arm_script, "load_dotenv", lambda path: None)
        args = args_for(*CHAT_ARGS, "--frozen")

        assert run_arm_script.child_env(args) == {}

    def test_the_chat_endpoint_fails_closed_without_a_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LLHB_OPENAI_CHAT_API_KEY", raising=False)
        monkeypatch.setattr(run_arm_script, "load_dotenv", lambda path: None)
        args = args_for(*CHAT_ARGS, "--frozen")

        with pytest.raises(run_arm_script.LovsporError, match="LLHB_OPENAI_CHAT_API_KEY"):
            run_arm_script.chat_endpoint(args)

    def test_the_chat_endpoint_carries_base_url_key_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLHB_OPENAI_CHAT_API_KEY", "sk-test")
        monkeypatch.setattr(run_arm_script, "load_dotenv", lambda path: None)
        args = args_for(
            *CHAT_ARGS, "--frozen", "--base-url", "http://vllm.local/v1", "--timeout", "90"
        )

        endpoint = run_arm_script.chat_endpoint(args)

        assert endpoint is not None
        assert (endpoint.base_url, endpoint.api_key, endpoint.timeout_s) == (
            "http://vllm.local/v1",
            "sk-test",
            90,
        )
