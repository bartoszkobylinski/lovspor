"""LLHB run orchestrator: hermetic env and cwd, ordering, CLI execution."""

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from lovspor.llhb.claude_cli import RunIdentity, ToolAccess
from lovspor.llhb.orchestrator import (
    OrchestratorError,
    RunConfig,
    case_order,
    execute_argv,
    hermetic_env,
    run_arm,
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

ACCESS = ToolAccess(
    mcp_config_json='{"mcpServers":{"lovverk":{"command":"/bin/lovspor"}}}',
    allowed_tools=("mcp__lovverk__get_section",),
)


def init_event(tools: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "tools": tools or [],
        "mcp_servers": [],
        "model": "claude-opus-5",
    }


def result_event(answer: str = "Svar fra modellen.") -> dict[str, Any]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": answer,
        "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "permission_denials": [],
    }


def emit(*events: dict[str, Any]) -> str:
    """A shell command emitting these events as a stream-json transcript."""
    lines = " ".join(f"'{json.dumps(event)}'" for event in events)
    return f"printf '%s\\n' {lines}"


SUCCESS_STREAM = emit(init_event(), result_event())


def make_metadata(**overrides: Any) -> dict[str, Any]:
    metadata = {
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
    metadata.update(overrides)
    return metadata


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


def make_config(tmp_path: Path, bin_dir: Path, timeout_s: int = 30, **overrides: Any) -> RunConfig:
    sandbox = tmp_path / "sandbox-home"
    sandbox.mkdir(exist_ok=True)
    fields: dict[str, Any] = {
        "identity": IDENTITY,
        "system_prompt": "SYSTEM",
        "case_order_seed": 42,
        "timeout_s": timeout_s,
        "sandbox_home": sandbox,
        "extra_env": {"PATH": str(bin_dir)},
    }
    fields.update(overrides)
    return RunConfig(**fields)


def treatment_config(tmp_path: Path, bin_dir: Path) -> RunConfig:
    return make_config(
        tmp_path,
        bin_dir,
        identity=IDENTITY.model_copy(update={"condition": "lovspor"}),
        tool_access=ACCESS,
    )


class TestRunConfig:
    def test_defaults(self, tmp_path: Path) -> None:
        config = RunConfig(
            identity=IDENTITY,
            system_prompt="SYSTEM",
            case_order_seed=42,
            sandbox_home=tmp_path,
        )

        assert config.timeout_s == 600
        assert config.extra_env == {}
        assert config.tool_access is None


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
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)

        env = hermetic_env(tmp_path, {"PATH": str(bin_dir)})

        result = execute_argv(["claude", "-p", "q"], env, 30, tmp_path)

        assert result.returncode == 0
        assert result.timed_out is False
        assert json.loads(result.stdout.splitlines()[-1])["result"] == "Svar fra modellen."
        assert result.duration_ms >= 0

    def test_flags_timeout(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "exec /bin/sleep 5")

        result = execute_argv(
            ["claude"], hermetic_env(tmp_path, {"PATH": str(bin_dir)}), 1, tmp_path
        )

        assert result.timed_out is True
        assert result.returncode == -1
        assert 500 <= result.duration_ms <= 30_000

    def test_survives_non_utf8_stdout(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "printf '\\377\\376 not utf8'")

        result = execute_argv(
            ["claude"], hermetic_env(tmp_path, {"PATH": str(bin_dir)}), 30, tmp_path
        )

        assert result.returncode == 0
        assert "�" in result.stdout

    def test_missing_executable_is_a_result(self, tmp_path: Path) -> None:
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()

        result = execute_argv(
            ["claude"], hermetic_env(tmp_path, {"PATH": str(empty_bin)}), 5, tmp_path
        )

        assert result.timed_out is False
        assert result.returncode == 127
        assert "cannot execute claude" in result.stderr


class TestRunArm:
    def test_end_to_end_with_fake_cli(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C2-001")]

        summary = run_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)

        records = store.read_records(RUN_ID)
        assert len(records) == 2
        assert all(record["completed"] for record in records)
        assert summary["cases_completed"] == 2
        assert summary["errors_total"] == 0

    def test_control_records_prove_an_empty_tool_surface(self, tmp_path: Path) -> None:
        """Ruling #25 invalidates a control run that showed tool activity;
        the transcript is what makes that checkable after the fact."""
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["tool_calls"] == []
        assert record["harness"]["exposed_tools"] == []

    def test_runs_the_cli_inside_the_sandbox(self, tmp_path: Path) -> None:
        """CLAUDE.md discovery walks up from the working directory, so a run
        started in the repository answers with project instructions in
        context (measured 2026-08-09). The sandbox has nothing to find."""
        probe = (
            emit(init_event())
            + "; "
            + (
                'printf \'{"type":"result","subtype":"success","is_error":false,'
                '"num_turns":1,"result":"PWD=%s"}\\n\' "$PWD"'
            )
        )
        bin_dir = fake_claude(tmp_path, probe)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        answer = store.read_records(RUN_ID)[0]["final_answer"]
        assert answer == f"PWD={tmp_path / 'sandbox-home'}"

    def test_treatment_records_the_tool_trace(self, tmp_path: Path) -> None:
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-1",
                            "name": "mcp__lovverk__get_section",
                            "input": {"slug": "testloven"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "tu-1", "content": "kort svar"}
                    ]
                },
            },
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(
            condition="lovspor",
            tool_config={"transport": "native-mcp", "tools": ["mcp__lovverk__get_section"]},
        )

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        call = store.read_records(RUN_ID)[0]["tool_calls"][0]
        assert call["name"] == "mcp__lovverk__get_section"
        assert call["result"] == "kort svar"
        assert len(call["result_sha256"]) == 64
        assert "result_ref" not in call

    def test_unanswered_tool_call_is_stored_without_a_payload_hash(self, tmp_path: Path) -> None:
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-1",
                            "name": "mcp__lovverk__get_section",
                            "input": {"slug": "testloven"},
                        }
                    ]
                },
            },
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        call = store.read_records(RUN_ID)[0]["tool_calls"][0]
        assert call["result"] is None
        assert "result_sha256" not in call
        assert not (tmp_path / "runs" / RUN_ID / "tools").exists()

    def test_large_tool_payload_is_spilled_beside_the_run(self, tmp_path: Path) -> None:
        payload = "paragraf " * 1000
        stream = emit(
            init_event(["mcp__lovverk__get_law"]),
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu-1",
                            "name": "mcp__lovverk__get_law",
                            "input": {"slug": "testloven"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": payload}]
                },
            },
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        call = store.read_records(RUN_ID)[0]["tool_calls"][0]
        assert call["result"] is None
        assert call["result_ref"] == "tools/llhb-v1-C1-001-000.json"
        spilled = (tmp_path / "runs" / RUN_ID / call["result_ref"]).read_text(encoding="utf-8")
        assert json.loads(spilled) == payload

    def test_retains_raw_stdout_per_case(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["raw_response_ref"] == "raw/llhb-v1-C1-001.json"
        raw_path = tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        assert raw["returncode"] == 0
        assert json.loads(raw["stdout"].splitlines()[-1])["result"] == "Svar fra modellen."

    def test_cli_failure_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "echo boom >&2; exit 2")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert record["final_answer"] is None
        assert summary["errors_total"] == 1

    def test_truncated_transcript_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, emit(init_event()))
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert "result event" in record["errors"][0]["message"]
        assert summary["errors_total"] == 1

    def test_finalizes_metadata(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        metadata_path = tmp_path / "runs" / RUN_ID / "run-metadata.json"
        written = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert written["cases_total"] == 1
        assert written["cases_completed"] == 1
        assert written["errors_total"] == 0
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", written["finished_at"])

    def test_rejects_duplicate_cases_before_any_write(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("llhb-v1-C1-001"), make_case("llhb-v1-C1-001")]

        with pytest.raises(OrchestratorError, match="duplicate"):
            run_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)
        assert not (tmp_path / "runs").exists()

    def test_rejects_traversal_case_id_before_any_write(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, SUCCESS_STREAM)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        cases = [make_case("../../outside")]

        with pytest.raises(OrchestratorError, match="invalid case ids"):
            run_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "outside.json").exists()

    def test_non_utf8_stdout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "printf '\\377\\376 not utf8'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert summary["errors_total"] == 1

    def test_timeout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "exec /bin/sleep 3")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
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

        summary = run_arm(
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
            emit(init_event())
            + "; "
            + (
                'printf \'{"type":"result","subtype":"success","is_error":false,'
                '"num_turns":1,"result":"HOME=%s KEY=%s"}\\n\' "$HOME" "${ANTHROPIC_API_KEY-unset}"'
            )
        )
        bin_dir = fake_claude(tmp_path, probe)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        answer = store.read_records(RUN_ID)[0]["final_answer"]
        assert f"HOME={tmp_path / 'sandbox-home'}" in answer
        assert "KEY=unset" in answer
        assert os.environ["ANTHROPIC_API_KEY"] == "sk-leak"
