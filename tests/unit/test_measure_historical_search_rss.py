"""Tests for ``scripts/measure_historical_search_rss.py`` (#223).

The measurement itself needs the real ``lovverk`` corpus and is run by hand;
what is pinned here is the arithmetic a wrong reading would come from — the
platform unit of ``ru_maxrss`` above all, which differs by a factor of 1024
between the Mac it was measured on and the droplet it is compared against.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

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
