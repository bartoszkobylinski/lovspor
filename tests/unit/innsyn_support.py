"""Shared setup for the innsyn command tests (#509): a workspace and the real CLI."""

from datetime import UTC, date, datetime
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
from lovspor.observatory.lf_ledger import LedgerEntry

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


PAGE = "https://www.example.kommune.no/forskrifter"


def ok(workspace: Path, command: str, *args: str) -> None:
    result = innsyn(workspace, command, *args)
    assert result.exit_code == 0, result.output


def set_recipient(workspace: Path, authority_id: str, address: str) -> None:
    evidence = ("--evidence-url", PAGE, "--read-at", "2026-10-03T10:00:00Z")
    ok(workspace, "set-recipient", "--id", authority_id, "--address", address, *evidence)


def lf_entry(authority_id: str, lf_id: str, kind: str = "LF") -> str:
    """One LF-ledger line, as the observatory writes it."""
    return LedgerEntry(
        authority_id=authority_id,
        lf_id=lf_id,
        kind=kind,  # type: ignore[arg-type]
        first_seen=datetime(2026, 10, 1, tzinfo=UTC),
        url=PAGE,
        sha256="a" * 64,
    ).model_dump_json()


def sample_lf_ledger(path: Path) -> Path:
    """Fjaler with three requestable ids and one short-form link, Oslo with one id."""
    lines = [
        lf_entry("4646", "2024-09-19-2196"),
        lf_entry("4646", "2009-02-16-306", "LTII"),
        lf_entry("4646", "2022-12-15-2677"),
        lf_entry("4646", "2020-01-01-1", "forskrift"),
        lf_entry("0301", "2019-03-20-400"),
    ]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def letters_workspace(workspace: Path, httpx_mock: HTTPXMock) -> Path:
    """The 2026-10-12 population with a sender, three recipients and Fjaler in nynorsk."""
    target = fetched(workspace, httpx_mock)
    ok(target, "set-sender", "--name", "Test Requester", "--email", "kontakt@example.no")
    set_recipient(target, "4646", "postmottak@fjaler.kommune.no")
    set_recipient(target, "0301", "postmottak@oslobystyre.no")
    set_recipient(target, "46", "post@vlfk.no")
    evidence = ("--evidence-url", PAGE, "--read-at", "2026-10-03T10:00:00Z")
    ok(target, "set-maalform", "--id", "4646", "--form", "nn", *evidence)
    return target
