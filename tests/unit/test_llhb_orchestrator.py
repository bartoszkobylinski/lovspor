"""LLHB run orchestrator: hermetic env and cwd, ordering, CLI execution."""

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.llhb import orchestrator
from lovspor.llhb.claude_cli import RunIdentity, ToolAccess
from lovspor.llhb.openai_chat import ChatEndpoint
from lovspor.llhb.orchestrator import (
    OrchestratorError,
    RunConfig,
    case_order,
    execute_argv,
    hermetic_env,
    run_arm,
)
from lovspor.llhb.results import ResultsStore
from lovspor.llhb.schema import load_schema, validate_case

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
    """A shell command emitting these events as a stream-json transcript.

    ``ensure_ascii=False`` because the real CLI emits UTF-8: escaping here
    would hide whether the retention path keeps Norwegian letters literal.
    """
    lines = " ".join(f"'{json.dumps(event, ensure_ascii=False)}'" for event in events)
    return f"printf '%s\\n' {lines}"


def tool_use_event(use_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "id": use_id, "name": name, "input": arguments}]
        },
    }


def tool_result_event(use_id: str, content: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": use_id, "content": content}]
        },
    }


SUCCESS_STREAM = emit(init_event(), result_event())


def make_metadata(**overrides: Any) -> dict[str, Any]:
    metadata = {
        "run_id": RUN_ID,
        "llhb_version": "1.0",
        "dataset_checksum": "a" * 64,
        "provider": "anthropic",
        "model_id": "claude-opus-5",
        "condition": "control",
        "analysis_plan_sha256": "a" * 64,
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


def stub_retry_sleep(monkeypatch: pytest.MonkeyPatch, sleep: Any) -> None:
    """Replace orchestrator's time reference without patching subprocess.time."""
    monkeypatch.setattr(orchestrator, "time", SimpleNamespace(monotonic=monotonic, sleep=sleep))


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
        assert config.case_attempts == 3

    def test_cli_invocation_defaults_to_not_timed_out(self) -> None:
        invocation = orchestrator.CliInvocation(stdout="", stderr="", returncode=0, duration_ms=1)

        assert invocation.timed_out is False


class TestCaseOrder:
    def test_deterministic_for_seed(self) -> None:
        ids = [f"llhb-v1-C1-{i:03d}" for i in range(1, 9)]

        assert case_order(ids, 42) == case_order(list(reversed(ids)), 42)

    def test_seed_actually_shuffles(self) -> None:
        ids = [f"llhb-v1-C1-{i:03d}" for i in range(1, 9)]

        orders = {tuple(case_order(ids, seed)) for seed in range(5)}
        assert len(orders) > 1

    def test_rejects_duplicate_ids(self) -> None:
        with pytest.raises(OrchestratorError, match=r"^duplicate case ids: \['llhb-v1-C1-001'\]$"):
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
        with pytest.raises(
            OrchestratorError,
            match=(
                r"^extra_env keys \['ANTHROPIC_API_KEY'\] are banned: credentials flip the run "
                r"onto per-token billing, HOME/CLAUDE_CONFIG_DIR would defeat the sandbox$"
            ),
        ):
            hermetic_env(tmp_path, {"ANTHROPIC_API_KEY": "sk-explicit"})

    def test_path_has_a_safe_default_when_parent_path_is_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PATH", raising=False)

        assert hermetic_env(tmp_path, {})["PATH"] == "/usr/bin:/bin"

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
        assert result.stdout == ""
        assert result.stderr.startswith("cannot execute claude: ")


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
        assert call["arguments"] == {"slug": "testloven"}
        assert len(call["result_sha256"]) == 64
        assert call["result_ref"] == "tools/llhb-v1-C1-001-000.json"

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
        assert "result" not in call
        assert "result_sha256" not in call
        assert not (tmp_path / "runs" / RUN_ID / "tools").exists()

    def test_an_unanswered_call_does_not_drop_the_calls_after_it(self, tmp_path: Path) -> None:
        """The unanswered branch skips one call, not the rest of the trace.
        Turning that skip into an early exit would silently shorten the
        tool history of every case where one call went unanswered."""
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            tool_use_event("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_use_event("tu-2", "mcp__lovverk__get_section", {"slug": "b"}),
            tool_result_event("tu-2", "svar-b"),
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        calls = store.read_records(RUN_ID)[0]["tool_calls"]
        assert [call["index"] for call in calls] == [0, 1]
        assert "result_sha256" not in calls[0]
        assert calls[1]["result_ref"] == "tools/llhb-v1-C1-001-001.json"

    def test_payload_hash_pins_the_canonical_form(self, tmp_path: Path) -> None:
        """Key order, separators and non-ASCII escaping are part of the
        hash contract: a payload rehashed under different settings would
        stop matching the bytes the pinned corpus regenerates."""
        payload = {"b": "æøå", "a": 1}
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            tool_use_event("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result_event("tu-1", payload),
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        canonical = '{"a":1,"b":"æøå"}'
        call = store.read_records(RUN_ID)[0]["tool_calls"][0]
        assert call["result_sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        spilled = tmp_path / "runs" / RUN_ID / call["result_ref"]
        assert spilled.read_text(encoding="utf-8") == canonical + "\n"

    def test_no_tool_payload_is_kept_inside_the_record(self, tmp_path: Path) -> None:
        """A lovverk payload is statutory text, which does not live in this
        repo; the record keeps the hash and the pin regenerates the bytes."""
        payload = "kort svar"
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
        assert "result" not in call
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

    def test_raw_retention_pins_its_own_written_form(self, tmp_path: Path) -> None:
        """The raw file is the evidence a rerun is diffed against, so its
        key order, indentation and non-ASCII escaping are part of the
        artifact rather than incidental json.dumps defaults."""
        bin_dir = fake_claude(tmp_path, emit(init_event(), result_event("Svar på nynorsk: æøå.")))
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        text = (tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.json").read_text(
            encoding="utf-8"
        )
        assert "æøå" in text
        assert text.startswith('{\n  "duration_ms"')
        assert list(json.loads(text)) == sorted(json.loads(text))

    def test_duration_is_recorded_in_whole_milliseconds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_ms is published run metadata; the seconds-to-milliseconds
        conversion has to be exactly that, not merely the right order."""
        # A whole second: x1000 and x1001 differ here, x1000 and x1001 on a
        # quarter second both floor to 250 and would prove nothing.
        ticks = iter([100.0, 101.0])
        monkeypatch.setattr(orchestrator.time, "monotonic", lambda: next(ticks))

        result = execute_argv(
            ["claude"], hermetic_env(tmp_path, {"PATH": str(tmp_path)}), 5, tmp_path
        )

        assert result.duration_ms == 1000

    def test_cli_failure_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "echo boom >&2; exit 2")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        # case_attempts=1: this test is about the error record's shape, not
        # the retry path (TestCaseRetry), and must not sleep through backoff.
        summary = run_arm(
            make_config(tmp_path, bin_dir, case_attempts=1),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert record["final_answer"] is None
        assert summary["errors_total"] == 1

    def test_truncated_transcript_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, emit(init_event()))
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir, case_attempts=1),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
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

        with pytest.raises(OrchestratorError, match=r"^invalid case ids: \['\.\./\.\./outside'\]$"):
            run_arm(make_config(tmp_path, bin_dir), cases, make_metadata(), store)
        assert not (tmp_path / "runs").exists()
        assert not (tmp_path / "outside.json").exists()

    def test_reports_a_missing_case_id_as_empty_before_any_write(self, tmp_path: Path) -> None:
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        with pytest.raises(OrchestratorError, match=r"^invalid case ids: \[''\]$"):
            run_arm(
                make_config(tmp_path, tmp_path), [{"question": "Uten id"}], make_metadata(), store
            )
        assert not (tmp_path / "runs").exists()

    def test_non_utf8_stdout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "printf '\\377\\376 not utf8'")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir, case_attempts=1),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert summary["errors_total"] == 1

    def test_timeout_becomes_error_record(self, tmp_path: Path) -> None:
        bin_dir = fake_claude(tmp_path, "exec /bin/sleep 3")
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir, timeout_s=1, case_attempts=1),
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


class TestToolCallReconciliation:
    """Undercounting tool calls is the one error this pipeline cannot make,
    and it has made it three times, each in a different part of the parser.
    A second count taken without walking events means a fourth occurrence
    stops the run instead of becoming a number in a published result."""

    def test_a_parser_that_misses_a_call_stops_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            tool_use_event("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result_event("tu-1", "svar"),
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})
        # A parser that silently drops the call — the shape of all three bugs.
        real = orchestrator.parse_stream_json

        def blinded(stdout: str, returncode: int) -> Any:
            return real(stdout, returncode).model_copy(update={"tool_calls": []})

        monkeypatch.setattr(orchestrator, "parse_stream_json", blinded)

        expected = (
            "case llhb-v1-C1-001: the parser found 0 tool call(s) but the transcript contains 1; "
            "the run is stopped rather than reported, because a miscounted trace is the one "
            "result this benchmark must not publish"
        )
        with pytest.raises(OrchestratorError, match=f"^{re.escape(expected)}$"):
            run_arm(
                treatment_config(tmp_path, bin_dir),
                [make_case("llhb-v1-C1-001")],
                metadata,
                store,
            )

    def test_an_honest_trace_passes_reconciliation(self, tmp_path: Path) -> None:
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            tool_use_event("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result_event("tu-1", "svar"),
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        assert len(store.read_records(RUN_ID)[0]["tool_calls"]) == 1


class TestPayloadNeverInlined:
    """The schema is the gate, not the writer: writers change, and a future
    one inlining a payload would put regenerable corpus material into a
    versioned record the day after ruling #27 (owner, 2026-08-10)."""

    def test_the_schema_refuses_an_inlined_payload(self) -> None:
        record = {
            "run_id": RUN_ID,
            "case_id": "llhb-v1-C1-001",
            "provider": "anthropic",
            "model_id": "claude-opus-5",
            "condition": "lovspor",
            "final_answer": "Svar.",
            "tool_calls": [
                {
                    "index": 0,
                    "name": "mcp__lovverk__get_section",
                    "arguments": {"slug": "testloven"},
                    "result": "§ 1. Formål — the corpus text itself",
                }
            ],
            "timing": {"started_at": "2026-08-10T09:00:01Z", "total_ms": 1},
            "errors": [],
            "completed": True,
        }

        errors = validate_case(record, load_schema(SCHEMA_DIR / "result_record.schema.json"))

        assert any("result" in error and "null" in error for error in errors)

    def test_a_written_call_carries_the_hash_and_no_payload(self, tmp_path: Path) -> None:
        stream = emit(
            init_event(["mcp__lovverk__get_section"]),
            tool_use_event("tu-1", "mcp__lovverk__get_section", {"slug": "a"}),
            tool_result_event("tu-1", "§ 1. Formål"),
            result_event(),
        )
        bin_dir = fake_claude(tmp_path, stream)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(condition="lovspor", tool_config={"transport": "native-mcp"})

        run_arm(treatment_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], metadata, store)

        call = store.read_records(RUN_ID)[0]["tool_calls"][0]
        assert "result" not in call
        assert len(call["result_sha256"]) == 64
        assert "§" not in json.dumps(call, ensure_ascii=False)


class TestCaseRetry:
    """Issue #80: one transient CLI failure must not burn a whole arm.

    The 2026-08-12 frozen evaluation lost two full treatment runs to
    single `529 Overloaded` exits; scoring is fail-closed on incomplete
    records, so an unretried transient turns 3.5 hours of run into an
    unscoreable artifact.
    """

    def fail_once_then_succeed(self, tmp_path: Path) -> Path:
        """A fake CLI that fails its first invocation and succeeds after."""
        marker = tmp_path / "first-attempt-done"
        # `: >` not `touch`: the hermetic PATH holds only the fake CLI's bin
        # dir, so the script can use shell builtins and nothing else.
        body = (
            f'if [ -f "{marker}" ]; then {SUCCESS_STREAM}; '
            f'else : > "{marker}"; echo "API Error: 529 Overloaded" >&2; exit 1; fi'
        )
        return fake_claude(tmp_path, body)

    def test_transient_failure_is_retried_to_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        stub_retry_sleep(monkeypatch, sleeps.append)
        bin_dir = self.fail_once_then_succeed(tmp_path)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is True
        assert summary["cases_completed"] == 1
        assert summary["errors_total"] == 0
        assert sleeps == [orchestrator._RETRY_BACKOFF_S[0]]

    def test_retry_is_recorded_not_hidden(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retried case must say so in its record: silent self-repair is
        the same provenance failure as silent loss, just inverted."""
        stub_retry_sleep(monkeypatch, lambda _s: None)
        bin_dir = self.fail_once_then_succeed(tmp_path)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is True
        [note] = record["errors"]
        assert note["stage"] == "other"
        assert note["message"] == (
            "attempt 1 failed and was retried: claude exited with exit code 1"
        )
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", note["at"])
        attempt_raw = tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.attempt1.json"
        assert "529 Overloaded" in attempt_raw.read_text(encoding="utf-8")

    def test_retries_are_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stub_retry_sleep(monkeypatch, lambda _s: None)
        calls = tmp_path / "calls"
        bin_dir = fake_claude(tmp_path, f'echo x >> "{calls}"; echo boom >&2; exit 1')
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, bin_dir, case_attempts=2),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        assert len(calls.read_text(encoding="utf-8").splitlines()) == 2
        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert summary["errors_total"] == 1
        stages = [error["stage"] for error in record["errors"]]
        assert stages == ["other", "request"]

    def test_default_budget_retains_every_failed_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleeps: list[float] = []
        stub_retry_sleep(monkeypatch, sleeps.append)
        calls = tmp_path / "calls"
        bin_dir = fake_claude(tmp_path, f'echo attempt >> "{calls}"; echo boom >&2; exit 1')
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, bin_dir),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        assert len(calls.read_text(encoding="utf-8").splitlines()) == 3
        assert sleeps == [30.0, 60.0]
        raw_dir = tmp_path / "runs" / RUN_ID / "raw"
        assert sorted(path.name for path in raw_dir.iterdir()) == [
            "llhb-v1-C1-001.attempt1.json",
            "llhb-v1-C1-001.attempt2.json",
            "llhb-v1-C1-001.json",
        ]
        record = store.read_records(RUN_ID)[0]
        assert [error["stage"] for error in record["errors"]] == [
            "other",
            "other",
            "request",
        ]

    def test_timeout_is_retried_to_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        invocations = iter(
            [
                orchestrator.CliInvocation(
                    stdout="", stderr="", returncode=-1, duration_ms=1, timed_out=True
                ),
                orchestrator.CliInvocation(
                    stdout="\n".join(json.dumps(event) for event in (init_event(), result_event())),
                    stderr="",
                    returncode=0,
                    duration_ms=1,
                ),
            ]
        )
        monkeypatch.setattr(orchestrator, "execute_argv", lambda *_args: next(invocations))
        stub_retry_sleep(monkeypatch, lambda _seconds: None)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            make_config(tmp_path, tmp_path),
            [make_case("llhb-v1-C1-001")],
            make_metadata(),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is True
        assert "timed out" in record["errors"][0]["message"]
        assert record["errors"][0]["message"] == (
            "attempt 1 failed and was retried: CLI timed out before completing the case"
        )
        attempt_raw = tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.attempt1.json"
        assert json.loads(attempt_raw.read_text(encoding="utf-8"))["timed_out"] is True

    def test_cannot_execute_is_not_retried(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit 127 (missing CLI) is permanent; retrying it only burns time."""
        sleeps: list[float] = []
        stub_retry_sleep(monkeypatch, sleeps.append)
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        summary = run_arm(
            make_config(tmp_path, empty_bin), [make_case("llhb-v1-C1-001")], make_metadata(), store
        )

        assert summary["errors_total"] == 1
        assert sleeps == []

    def test_attempts_below_one_are_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="case_attempts"):
            make_config(tmp_path, tmp_path, case_attempts=0)

    def test_retry_note_handles_a_failed_record_without_parser_errors(self) -> None:
        note = orchestrator._retry_note(2, {"errors": []})

        assert note["message"] == "attempt 2 failed and was retried: no error captured"


def test_artifact_retention_rejects_an_invalid_case_id(tmp_path: Path) -> None:
    with pytest.raises(
        OrchestratorError,
        match=r"^invalid case id '\.\./outside' for artifact retention$",
    ):
        orchestrator._checked_dir(tmp_path, "../outside", "raw")


def test_text_converts_absent_process_output_to_empty_text() -> None:
    assert orchestrator._text(None) == ""


CHAT_ENDPOINT = ChatEndpoint(base_url="https://chat.example/api", api_key="sk-test", timeout_s=30)


def chat_config(tmp_path: Path, **overrides: Any) -> RunConfig:
    fields: dict[str, Any] = {
        "driver": "openai-chat",
        "chat_endpoint": CHAT_ENDPOINT,
        "identity": IDENTITY.model_copy(update={"provider": "norallm", "model_id": "NorMistral"}),
        "extra_env": {},
    }
    fields.update(overrides)
    return make_config(tmp_path, tmp_path / "unused-bin", **fields)


def chat_body(content: str, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "NorMistral",
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }
    body.update(overrides)
    return body


class TestChatDriver:
    def test_end_to_end_records_match_the_cli_shape(
        self, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            url="https://chat.example/api/chat/completions",
            json=chat_body("<think>tenker</think>Svar fra modellen."),
        )
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)
        metadata = make_metadata(provider="norallm", model_id="NorMistral")

        summary = run_arm(chat_config(tmp_path), [make_case("llhb-v1-C1-001")], metadata, store)

        record = store.read_records(RUN_ID)[0]
        assert summary["cases_completed"] == 1
        assert record["final_answer"] == "Svar fra modellen."
        assert record["tool_calls"] == []
        assert record["harness"] == {
            "exposed_tools": [],
            "mcp_servers": [],
            "permission_denials": [],
        }
        raw = json.loads((tmp_path / "runs" / RUN_ID / "raw" / "llhb-v1-C1-001.json").read_text())
        assert "<think>tenker</think>" in raw["stdout"]

    def test_sends_no_tools_and_the_bearer_token(
        self, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            url="https://chat.example/api/chat/completions", json=chat_body("Svar.")
        )
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            chat_config(tmp_path),
            [make_case("llhb-v1-C1-001")],
            make_metadata(provider="norallm", model_id="NorMistral"),
            store,
        )

        request = httpx_mock.get_requests()[0]
        sent = json.loads(request.content)
        assert "tools" not in sent
        assert sent["messages"][0] == {"role": "system", "content": "SYSTEM"}
        assert request.headers["Authorization"] == "Bearer sk-test"

    def test_a_rejected_credential_is_not_retried(
        self, tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        httpx_mock.add_response(url="https://chat.example/api/chat/completions", status_code=401)
        slept: list[float] = []
        stub_retry_sleep(monkeypatch, slept.append)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            chat_config(tmp_path),
            [make_case("llhb-v1-C1-001")],
            make_metadata(provider="norallm", model_id="NorMistral"),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is False
        assert record["errors"] == [
            {"stage": "request", "message": "chat endpoint returned HTTP 401"}
        ]
        assert len(httpx_mock.get_requests()) == 1
        assert slept == []

    def test_a_server_error_is_retried_within_the_budget(
        self, tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        httpx_mock.add_response(url="https://chat.example/api/chat/completions", status_code=503)
        httpx_mock.add_response(
            url="https://chat.example/api/chat/completions", json=chat_body("Svar.")
        )
        slept: list[float] = []
        stub_retry_sleep(monkeypatch, slept.append)
        store = ResultsStore(runs_root=tmp_path / "runs", schema_dir=SCHEMA_DIR)

        run_arm(
            chat_config(tmp_path),
            [make_case("llhb-v1-C1-001")],
            make_metadata(provider="norallm", model_id="NorMistral"),
            store,
        )

        record = store.read_records(RUN_ID)[0]
        assert record["completed"] is True
        assert record["errors"][0]["stage"] == "other"
        assert slept == [30.0]

    def test_the_chat_driver_refuses_tool_access(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="control arm only"):
            chat_config(
                tmp_path,
                identity=IDENTITY.model_copy(update={"condition": "lovspor"}),
                tool_access=ACCESS,
            )

    def test_the_chat_driver_refuses_a_treatment_identity_without_tool_access(
        self, tmp_path: Path
    ) -> None:
        """A lovspor-labelled run with no tools would be recorded as treatment
        while measuring control; the config fails closed, not the request."""
        with pytest.raises(ValidationError, match="control arm only"):
            chat_config(
                tmp_path,
                identity=IDENTITY.model_copy(
                    update={"provider": "norallm", "condition": "lovspor"}
                ),
            )

    def test_the_chat_driver_needs_an_endpoint(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="chat_endpoint"):
            make_config(tmp_path, tmp_path / "bin", driver="openai-chat", extra_env={})
