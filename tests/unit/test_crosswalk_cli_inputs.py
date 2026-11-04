"""Capture input refusals reach the CLI before disk or network side effects."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from tests.unit.cli_output import said

LAW = b"LOV-2022-06-17-63\n"
COMMIT = "a" * 40


@pytest.mark.parametrize(
    ("body", "diagnostic"),
    [
        (None, "law list unreadable"),
        (b"\xff", "law list is not UTF-8"),
        (b"# no laws\n\n", "not a list of Lovdata law ids"),
        (LAW + b"not-a-law\n", "not a list of Lovdata law ids"),
    ],
)
def test_cli_refuses_invalid_law_lists_before_creating_cache(
    tmp_path: Path, httpx_mock: HTTPXMock, body: bytes | None, diagnostic: str
) -> None:
    """Enforce read_law_ids/checked_scope's refusals through the command boundary."""
    laws = tmp_path / "laws.txt"
    if body is not None:
        laws.write_bytes(body)
    cache = tmp_path / "new-parent" / "capture"

    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "capture",
            "--cache",
            str(cache),
            "--laws",
            str(laws),
            "--lovverk-commit",
            COMMIT,
        ],
    )

    assert result.exit_code == 1
    assert said(f"refused: {diagnostic}", result.output)
    assert not cache.parent.exists()
    assert not httpx_mock.get_requests()
    if body is not None:
        assert laws.read_bytes() == body
    else:
        assert not laws.exists()


@pytest.mark.parametrize("missing", ["--cache", "--laws", "--lovverk-commit"])
def test_cli_requires_each_capture_option_before_side_effects(
    tmp_path: Path, httpx_mock: HTTPXMock, missing: str
) -> None:
    laws = tmp_path / "laws.txt"
    laws.write_bytes(LAW)
    cache = tmp_path / "capture"
    options = {"--cache": str(cache), "--laws": str(laws), "--lovverk-commit": COMMIT}
    args = ["ops", "crosswalk", "capture"]
    for option, value in options.items():
        if option != missing:
            args.extend([option, value])

    result = CliRunner().invoke(app, args)

    assert result.exit_code == 2
    assert said(f"Missing option '{missing}'", result.output)
    assert not cache.exists()
    assert laws.read_bytes() == LAW
    assert not httpx_mock.get_requests()
