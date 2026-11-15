"""Exercise #622's budget arithmetic without invoking any mutation tooling."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("inherited_total", [None, "17", "invalid"])
@pytest.mark.parametrize(
    ("count", "setup", "per_file", "expected_setup", "expected_file", "expected_total"),
    [
        (1, None, None, 2400, 1200, 3600),
        (2, None, None, 2400, 1200, 4800),
        (3, "7", "11", 7, 11, 40),
        (2, "0", "11", 0, 11, 22),
        (2, "7", "0", 7, 0, 7),
        (1, "7", None, 7, 1200, 1207),
        (1, None, "11", 2400, 11, 2411),
    ],
)
def test_setup_is_added_once_to_the_changed_files_budget(
    count: int,
    setup: str | None,
    per_file: str | None,
    expected_setup: int,
    expected_file: int,
    expected_total: int,
    inherited_total: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enforce the default and override formula documented in decisions.md §9a."""
    # Reproduce the nightly environment even when pytest runs outside that job.
    if inherited_total is None:
        monkeypatch.delenv("MUTMUT_PR_TOTAL_BUDGET_SECONDS", raising=False)
    else:
        monkeypatch.setenv("MUTMUT_PR_TOTAL_BUDGET_SECONDS", inherited_total)
    script = Path(__file__).resolve().parents[2] / "scripts" / "mutmut-pr.sh"
    body = script.read_text(encoding="utf-8")
    # Execute only assignments and the budget notice, never the runner or cleanup.
    start = body.index('file_budget="')
    end = body.index('max_children="', start)
    budget_block = body[start:end]
    env = os.environ.copy()
    # The nightly job exports a whole-run wall-clock budget, which replaces this formula
    # in mutmut-pr.sh; mutmut's baseline pytest run inherits it, so drop it here.
    env.pop("MUTMUT_PR_TOTAL_BUDGET_SECONDS", None)
    for name, value in (
        ("MUTMUT_PR_SETUP_BUDGET_SECONDS", setup),
        ("MUTMUT_PR_FILE_BUDGET_SECONDS", per_file),
    ):
        env.pop(name, None)
        if value is not None:
            env[name] = value

    result = subprocess.run(
        ["bash", "-eu", "-c", f"count={count}\n{budget_block}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )

    assert result.stdout == (
        f"budget: {expected_setup}s for mutmut's setup + {expected_file}s "
        f"x {count} changed file(s) = {expected_total}s\n"
    )
    assert result.stderr == ""
