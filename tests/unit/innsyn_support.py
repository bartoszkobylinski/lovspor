"""Shared setup for the innsyn command tests (#509): a workspace and the real CLI."""

from datetime import date
from pathlib import Path

from click.testing import Result
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.innsyn.population import (
    FYLKESKOMMUNE_CLASSIFICATION,
    KOMMUNE_CLASSIFICATION,
    codes_url,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "innsyn"
KLASS_DAY = date(2026, 10, 12)

runner = CliRunner()


def innsyn(workspace: Path, *args: str) -> Result:
    """Run ``lovspor observatory innsyn --dir <workspace> <args>``."""
    return runner.invoke(app, ["observatory", "innsyn", "--dir", str(workspace), *args])


def mock_klass(httpx_mock: HTTPXMock) -> None:
    """The two KLASS reads, answered with the responses captured on 2026-10-12."""
    for classification in (KOMMUNE_CLASSIFICATION, FYLKESKOMMUNE_CLASSIFICATION):
        body = (FIXTURES / f"klass-{classification}-codesAt-2026-10-12.json").read_bytes()
        httpx_mock.add_response(url=codes_url(classification, KLASS_DAY), content=body)


def fetched(workspace: Path, httpx_mock: HTTPXMock) -> Path:
    """A workspace holding the 2026-10-12 population, fetched by the command."""
    mock_klass(httpx_mock)
    result = innsyn(workspace, "fetch-population", "--date", KLASS_DAY.isoformat())
    assert result.exit_code == 0, result.output
    return workspace
