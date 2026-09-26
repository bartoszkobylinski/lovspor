"""Tests for ``scripts/measure_historical_search_rss.py`` (#223).

The measurement itself needs the real ``lovverk`` corpus and is run by hand;
what is pinned here is the arithmetic a wrong reading would come from — the
platform unit of ``ru_maxrss`` above all, which differs by a factor of 1024
between the Mac it was measured on and the droplet it is compared against.
"""

import importlib.util
import sys
from argparse import Namespace
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "measure_historical_search_rss.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_historical_search_rss", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rss = _load_script()


def _step(step: str, peak: float) -> object:
    return rss.Step(
        scenario="four_states",
        step=step,
        seconds=1.25,
        peak_mib=peak,
        rss_mib=peak - 10,
        child_peak_mib=3.0,
        detail="abc123",
    )


class TestMaxrssUnits:
    def test_macos_reports_bytes(self) -> None:
        assert rss.maxrss_to_mib(512 * 1024 * 1024, "darwin") == 512.0

    def test_linux_reports_kibibytes(self) -> None:
        assert rss.maxrss_to_mib(512 * 1024, "linux") == 512.0

    def test_the_same_number_differs_by_1024_between_platforms(self) -> None:
        raw = 1_048_576
        assert rss.maxrss_to_mib(raw, "linux") == 1024 * rss.maxrss_to_mib(raw, "darwin")


class TestParseSteps:
    def test_reads_one_step_per_json_line(self) -> None:
        lines = [_step("a", 100.0).model_dump_json(), _step("b", 200.0).model_dump_json()]
        steps = rss.parse_steps("\n".join(lines) + "\n")
        assert [s.step for s in steps] == ["a", "b"]
        assert steps[1].peak_mib == 200.0

    def test_refuses_a_line_that_is_not_a_step(self) -> None:
        with pytest.raises(ValueError):
            rss.parse_steps('{"scenario": "x"}\n')

    def test_empty_output_is_refused_rather_than_read_as_no_cost(self) -> None:
        with pytest.raises(ValueError, match="no steps"):
            rss.parse_steps("\n")


class TestRenderTable:
    def test_one_row_per_step_with_headroom_against_both_limits(self) -> None:
        table = rss.render_table([_step("reader", 300.0), _step("state 1", 1500.0)])
        rows = [line for line in table.splitlines() if line.startswith("| four_states")]
        assert len(rows) == 2
        assert "| 1500 |" in rows[1]
        assert "| -100 |" in rows[1]  # over MemoryHigh 1400M
        assert "| 200 |" in rows[1]  # under MemoryMax 1700M


class TestScenarioPlans:
    DATES = [date(2026, month, 15) for month in range(6, 10)]  # noqa: RUF012

    @pytest.mark.parametrize(
        ("scenario", "expected_steps"),
        [
            ("cold_one_state", ["recorded_at=2026-06-15"]),
            (
                "four_states",
                [
                    "recorded_at=2026-06-15",
                    "recorded_at=2026-07-15",
                    "recorded_at=2026-08-15",
                    "recorded_at=2026-09-15",
                ],
            ),
            (
                "live_warm_four_states",
                [
                    "live search_body",
                    "recorded_at=2026-06-15",
                    "recorded_at=2026-07-15",
                    "recorded_at=2026-08-15",
                    "recorded_at=2026-09-15",
                ],
            ),
            (
                "embeddings_warm_four_states",
                [
                    "embedding index",
                    "live search_body",
                    "recorded_at=2026-06-15",
                    "recorded_at=2026-07-15",
                    "recorded_at=2026-08-15",
                    "recorded_at=2026-09-15",
                ],
            ),
        ],
    )
    def test_plan_has_the_documented_warmup_and_historical_steps(
        self, scenario: str, expected_steps: list[str]
    ) -> None:
        plan = rss._plan(scenario, Mock(), self.DATES, "arbeidsgiver")

        assert [name for name, _action in plan] == expected_steps


def test_parent_runs_each_scenario_in_a_fresh_interpreter_and_combines_steps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    scenarios = ("cold_one_state", "four_states")
    outputs = [
        _step("cold", 100.0).model_dump_json() + "\n",
        _step("four", 200.0).model_dump_json() + "\n",
    ]
    run = Mock(side_effect=[Namespace(stdout=output) for output in outputs])
    monkeypatch.setattr(rss.subprocess, "run", run)
    args = Namespace(
        scenarios=",".join(scenarios),
        corpus=tmp_path,
        dates="2026-06-15,2026-07-15,2026-08-15,2026-09-15",
        query="arbeidsgiver",
    )

    rss.run_parent(args)

    assert run.call_count == 2
    for call, scenario in zip(run.call_args_list, scenarios, strict=True):
        command = call.args[0]
        assert command[:4] == [sys.executable, str(_SCRIPT), "--child", scenario]
        assert call.kwargs == {"stdout": rss.subprocess.PIPE, "check": True, "text": True}
    table = capsys.readouterr().out
    assert "| four_states | cold |" in table
    assert "| four_states | four |" in table


@pytest.mark.parametrize(
    "dates",
    [
        "2026-06-15,2026-07-15,2026-08-15",
        "2026-06-15,2026-07-15,2026-08-15,2026-08-15",
    ],
    ids=["only-three", "duplicate"],
)
def test_four_state_child_refuses_dates_that_are_not_four_distinct_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dates: str
) -> None:
    run_child = Mock()
    monkeypatch.setattr(rss, "run_child", run_child)

    with pytest.raises((SystemExit, ValueError)):
        rss.main(
            [
                "--corpus",
                str(tmp_path),
                "--dates",
                dates,
                "--child",
                "four_states",
            ]
        )

    run_child.assert_not_called()
