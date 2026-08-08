"""Stage 5 control-arm orchestrator: hermetic env, ordering, CLI execution."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.claude_cli import RunIdentity
from lovspor.llhb.orchestrator import (
    ControlRunConfig,
    OrchestratorError,
    case_order,
    execute_argv,
    hermetic_env,
    run_control_arm,
)
from lovspor.llhb.results import ResultsStore

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "benchmarks" / "llhb" / "schema"

RUN_ID = "llhb-v1-run-20260808-pilot1"

IDENTITY = RunIdentity(
    run_id=RUN_ID,
    provider="anthropic",
    model_id="claude-opus-5",
    condition="control",
)

SUCCESS_PAYLOAD = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "Svar fra modellen.",
        "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
)


def make_metadata() -> dict[str, Any]:
    return {
        "run_id": RUN_ID,
        "llhb_version": "1.0",
        "dataset_checksum": "a" * 64,
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "system_prompt_sha256": "b" * 64,
        "sampling": {"temperature": None},
        "started_at": "2026-08-08T12:00:00Z",
        "lovspor_commit": "0123abc",
        "lovverk_commit": "0" * 40,
        "runner_commit": "4567def",
        "case_order_seed": 42,
    }


def make_case(case_id: str) -> dict[str, Any]:
    return {"case_id": case_id, "question": "Hva sier loven?"}


def fake_claude(tmp_path: Path, script_body: str) -> Path:
    """Install a fake `claude` executable and return its bin dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(f"#!/bin/sh\n{script_body}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def make_config(tmp_path: Path, bin_dir: Path, timeout_s: int = 30) -> ControlRunConfig:
    sandbox = tmp_path / "sandbox-home"
    sandbox.mkdir(exist_ok=True)
    return ControlRunConfig(
        identity=IDENTITY,
        system_prompt="SYSTEM",
        case_order_seed=42,
        timeout_s=timeout_s,
        sandbox_home=sandbox,
        extra_env={"PATH": str(bin_dir)},
    )


class TestControlRunConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        config = ControlRunConfig(
            identity=IDENTITY,
            system_prompt="SYSTEM",
            case_order_seed=42,
            sandbox_home=tmp_path,
        )

        assert config.timeout_s == 600
        assert config.extra_env == {}


class TestCaseOrder:
    def test_deterministic_for_seed(self) -> None:
        ids = [f"llhb-v1-C1-{i:03d}" for i in range(1, 9)]

        assert case_order(ids, 42) == case_order(list(reversed(ids)), 42)

    def test_seed_actually_shuffles(self) -> None:
        ids = [f"llhb-v1-C1-{i:03d}" for i in range(1, 9)]

        orders = {tuple(case_order(ids, seed)) for seed in range(5)}
        assert len(orders) > 1

    def test_rejects_duplicate_ids(self) -> None:
        with pytest.raises(OrchestratorError, match="duplicate"):
            case_order(["llhb-v1-C1-001", "llhb-v1-C1-001"], 42)


class TestHermeticEnv:
    def test_never_inherits_anthropic_api_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")

        env = hermetic_env(tmp_path, {})

        assert "ANTHROPIC_API_KEY" not in env
        assert env["HOME"] == str(tmp_path)

    def test_rejects_api_key_in_extra_env(self, tmp_path: Path) -> None:
        with pytest.raises(OrchestratorError, match="ANTHROPIC_API_KEY"):
            hermetic_env(tmp_path, {"ANTHROPIC_API_KEY": "sk-explicit"})

    def test_extra_env_overrides_path(self, tmp_path: Path) -> None:
        env = hermetic_env(tmp_path, {"PATH": "/fake/bin"})

        assert env["PATH"] == "/fake/bin"

    def test_rejects_home_and_config_dir_in_extra_env(self, tmp_path: Path) -> None:
        with pytest.raises(OrchestratorError, match="HOME"):
            hermetic_env(tmp_path, {"HOME": "/leaked/user/home"})
        with pytest.raises(OrchestratorError, match="CLAUDE_CONFIG_DIR"):
            hermetic_env(tmp_path, {"CLAUDE_CONFIG_DIR": "/leaked/config"})
        with pytest.raises(OrchestratorError, match="ANTHROPIC_AUTH_TOKEN"):
            hermetic_env(tmp_path, {"ANTHROPIC_AUTH_TOKEN": "tok"})

    def test_whitelist_is_exact(self, tmp_path: Path) -> None:
        env = hermetic_env(tmp_path, {})

        assert set(env) == {"HOME", "PATH", "TERM"}
        assert env["TERM"] == "dumb"
        assert env["PATH"] == os.environ["PATH"]


class TestExecuteArgv:
    def test_captures_stdout_and_exit(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")

        env = hermetic_env(tmp_path, {"PATH": str(bin_dir)})

        result = execute_argv(["claude", "-p", "q"], env, 30)

        assert result.returncode == 0
        assert result.timed_out is False
        assert json.loads(result.stdout)["result"] == "Svar fra modellen."
        assert result.duration_ms >= 0

    def test_flags_timeout(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "exec /bin/sleep 5")

        result = execute_argv(["claude"], hermetic_env(tmp_path, {"PATH": str(bin_dir)}), 1)

        assert result.timed_out is True
        assert result.returncode == -1
        assert 500 <= result.duration_ms <= 30_000

    def test_survives_non_utf8_stdout(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "printf '\\377\\376 not utf8'")

        result = execute_argv(["claude"], hermetic_env(tmp_path, {"PATH": str(bin_dir)}), 30)

        assert result.returncode == 0
        assert "�" in result.stdout

    def test_missing_executable_is_a_result(self, tmp_path: Path) -> None:
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()

        result = execute_argv(["claude"], hermetic_env(tmp_path, {"PATH": str(empty_bin)}), 5)

        assert result.timed_out is False
        assert result.returncode == 127
        assert "cannot execute claude" in result.stderr


class TestRunControlArm:
    def test_end_to_end_with_fake_cli(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C2-001")]

        summary = run_control_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)

        records = store.read_records(RUN_ID)
        assert len(records) == 2
        assert all(record["completed"] for record in records)
        assert summary["cases_completed"] == 2
        assert summary["errors_total"] == 0

    def test_retains_raw_stdout_per_case(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_control_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["raw_response_ref"] == "raw/llhb-v1-C1-001.json"
        raw_path = tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        assert raw["returncode"] == 0
        assert json.loads(raw["stdout"])["result"] == "Svar fra modellen."

    def test_cli_failure_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "echo boom >&2; exit 2")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_control_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert record["final_answer"] is None
        assert summary["errors_total"] == 1

    def test_finalizes_metadata(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_control_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        metadata_path = tmp_path / "runs" / RUN_ID / "run-metadata.json"
        written = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert written["cases_total"] == 1
        assert written["cases_completed"] == 1
        assert written["errors_total"] == 0
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", written["finished_at"])

    def test_rejects_duplicate_cases_before_any_write(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-001")]

        with pytest.raises(OrchestratorError, match="duplicate"):
            run_control_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)
        assert not (tmp_path / "runs").exists()

    def test_rejects_traversal_case_id_before_any_write(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, f"echo '{SUCCESS_PAYLOAD}'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("../../outside")]

        with pytest.raises(OrchestratorError, match="invalid case ids"):
            run_control_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "outside.json").exists()

    def test_non_utf8_stdout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "printf '\\377\\376 not utf8'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_control_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert summary["errors_total"] == 1

    def test_timeout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "exec /bin/sleep 3")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_control_arm(
            make_config(tmp_path, bin_dir, timeout_s=1),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert "timed out" in record["errors"][0]["message"]
        assert summary["errors_total"] == 1

    def test_missing_executable_becomes_error_record(self, tmp_path: Path) -> None:
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_control_arm(
            make_config(tmp_path, empty_bin), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert record["errors"][0]["stage"] == "request"
        assert summary["errors_total"] == 1
        raw_path = tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.json"
        assert raw_path.is_file()

    def test_cli_env_is_hermetic(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-leak")
        probe = (
            'printf \'{"type":"result","subtype":"success","is_error":false,'
            '"num_turns":1,"result":"HOME=%s KEY=%s"}\' "$HOME" "${ANTHROPIC_API_KEY-unset}"'
        )
        bin_dir = fake_claude(tmp_path, probe)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_control_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        answer = store.read_records(RUN_ID)[0]["final_answer"]
        assert f"HOME={tmp_path / 'sandbox-home'}" in answer
        assert "KEY=unset" in answer
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-leak"
