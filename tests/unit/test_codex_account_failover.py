from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "ci" / "codex_account_failover.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_account_failover", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


failover = _load_script()
_WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"


def _fake_codex(tmp_path: Path, usages: dict[str, float]) -> Path:
    executable = tmp_path / "codex"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"usages = {json.dumps(usages)}\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if message.get('id') == 0:\n"
        "        print(json.dumps({'id': 0, 'result': {}}), flush=True)\n"
        "    elif message.get('id') == 1:\n"
        "        name = os.path.basename(os.environ['CODEX_HOME'])\n"
        "        value = usages[name]\n"
        "        result = {'rateLimits': {'primary': {'usedPercent': value}}}\n"
        "        print(json.dumps({'id': 1, 'result': result}), flush=True)\n"
    )
    executable.chmod(0o755)
    return executable


class TestRateLimitReading:
    def test_reads_real_app_server_process(self, tmp_path: Path) -> None:
        codex = _fake_codex(tmp_path, {"primary": 42.5})

        usage = failover.read_rate_limit(
            tmp_path / "primary", codex_command=str(codex), timeout_seconds=2
        )

        assert usage == 42.5

    def test_uses_highest_window_across_all_buckets(self) -> None:
        result = {
            "rateLimitsByLimitId": {
                "codex": {
                    "primary": {"usedPercent": 20},
                    "secondary": {"usedPercent": 94.5},
                },
                "codex_other": {"primary": {"usedPercent": 61}},
            }
        }

        assert failover._maximum_used_percent(result) == 94.5

    def test_rejects_response_without_usage_percentage(self) -> None:
        with pytest.raises(failover.RateLimitError, match="no usage percentage"):
            failover._maximum_used_percent({"rateLimits": {"primary": None}})


class TestAccountSelection:
    def test_keeps_primary_below_threshold(self, tmp_path: Path) -> None:
        codex = _fake_codex(tmp_path, {"primary": 94.9, "secondary": 1})

        selected, usage = failover.choose_home(
            tmp_path / "primary",
            tmp_path / "secondary",
            threshold=95,
            codex_command=str(codex),
            timeout_seconds=2,
        )

        assert selected == tmp_path / "primary"
        assert usage == 94.9

    def test_switches_to_secondary_at_threshold(self, tmp_path: Path) -> None:
        codex = _fake_codex(tmp_path, {"primary": 95, "secondary": 12})

        selected, usage = failover.choose_home(
            tmp_path / "primary",
            tmp_path / "secondary",
            threshold=95,
            codex_command=str(codex),
            timeout_seconds=2,
        )

        assert selected == tmp_path / "secondary"
        assert usage == 12

    def test_refuses_to_run_when_both_accounts_reached_threshold(self, tmp_path: Path) -> None:
        codex = _fake_codex(tmp_path, {"primary": 95, "secondary": 99})

        with pytest.raises(failover.RateLimitError, match="No Codex account is available"):
            failover.choose_home(
                tmp_path / "primary",
                tmp_path / "secondary",
                threshold=95,
                codex_command=str(codex),
                timeout_seconds=2,
            )


class TestWorkflowConfiguration:
    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_codex_homes_come_from_repository_variables(self, workflow_name: str) -> None:
        workflow = (_WORKFLOWS / workflow_name).read_text()

        assert "CODEX_PRIMARY_HOME: ${{ vars.CODEX_PRIMARY_HOME }}" in workflow
        assert "CODEX_SECONDARY_HOME: ${{ vars.CODEX_SECONDARY_HOME }}" in workflow
        assert '--primary-home "$CODEX_PRIMARY_HOME"' in workflow
        assert '--secondary-home "$CODEX_SECONDARY_HOME"' in workflow
        assert "/home/runner/.codex-lovspor" not in workflow

    @pytest.mark.parametrize("workflow_name", ["pr-pipeline.yml", "mutation-remediation.yml"])
    def test_missing_repository_variables_fail_before_codex_runs(self, workflow_name: str) -> None:
        workflow = (_WORKFLOWS / workflow_name).read_text()

        assert "${CODEX_PRIMARY_HOME:?Set repository variable CODEX_PRIMARY_HOME}" in workflow
        assert "${CODEX_SECONDARY_HOME:?Set repository variable CODEX_SECONDARY_HOME}" in workflow
