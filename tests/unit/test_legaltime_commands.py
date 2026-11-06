"""Operator reachability: the pilot's states are reachable from the two commands only."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.legaltime.commands import ReplayRequest, fetch_impl, replay_impl
from lovspor.sources.lovdata import LovdataClient
from tests.unit.test_legaltime_archive import make_archive

runner = CliRunner()


@pytest.fixture
def archives(tmp_path: Path) -> tuple[Path, Path]:
    lovtidend = make_archive(tmp_path / "lovtidend-avd1-2001-2025.tar.bz2")
    oracle = make_archive(
        tmp_path / "gjeldende-lover.tar.bz2", {"nl/nl-20190614-099.xml": "consolidated.xml"}
    )
    return lovtidend, oracle


def test_replay_command_writes_the_report_to_stdout_and_the_summary_to_stderr(
    archives: tuple[Path, Path],
) -> None:
    lovtidend, oracle = archives
    result = runner.invoke(
        app,
        [
            "ops",
            "legal-time",
            "replay",
            "--act",
            "lov/2019-06-14-99",
            "--archive",
            str(lovtidend),
            "--at",
            "2021-01-01",
            "--at",
            "2022-01-01",
            "--oracle",
            str(oracle),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["act"] == "lov/2019-06-14-99"
    assert [s["at"] for s in report["states"]] == ["2021-01-01", "2022-01-01"]
    assert report["states"][0]["status"] == "replayed"
    assert report["verification"][1]["counts"] == {
        "exact_normalized": 2,
        "exact_raw": 2,
        "text_differs": 1,
    }
    assert report["inputs"][0]["filename"] == lovtidend.name
    assert "2022-01-01: replayed, applied 5, future 0, pending 0, unresolved 0" in result.stderr


def test_replay_command_is_byte_deterministic(archives: tuple[Path, Path]) -> None:
    lovtidend, oracle = archives
    request = ReplayRequest(act="lov/2019-06-14-99", archives=(lovtidend,), dates=(), oracle=oracle)
    with pytest.raises(Exception, match="at least one --at"):
        replay_impl(request)
    request = request.model_copy(update={"dates": (date(2023, 1, 1),)})
    assert replay_impl(request) == replay_impl(request)


def test_replay_command_refuses_bad_act_and_bad_date(archives: tuple[Path, Path]) -> None:
    lovtidend, _ = archives
    bad_act = runner.invoke(
        app,
        [
            "ops",
            "legal-time",
            "replay",
            "--act",
            "arveloven",
            "--archive",
            str(lovtidend),
            "--at",
            "2021-01-01",
        ],
    )
    assert bad_act.exit_code != 0
    assert "refid" in bad_act.output
    bad_date = runner.invoke(
        app,
        [
            "ops",
            "legal-time",
            "replay",
            "--act",
            "lov/2019-06-14-99",
            "--archive",
            str(lovtidend),
            "--at",
            "yesterday",
        ],
    )
    assert bad_date.exit_code != 0
    assert "isoformat" in bad_date.output


def test_replay_command_reports_an_unknown_act_as_a_refusal(archives: tuple[Path, Path]) -> None:
    lovtidend, _ = archives
    result = runner.invoke(
        app,
        [
            "ops",
            "legal-time",
            "replay",
            "--act",
            "lov/1999-01-01-1",
            "--archive",
            str(lovtidend),
            "--at",
            "2021-01-01",
        ],
    )
    assert result.exit_code == 1
    assert "refused: no enacting member" in result.output


def test_fetch_downloads_only_the_pilot_archives_and_pins_them(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    body = b"not really a tarball"
    httpx_mock.add_response(
        url="https://api.lovdata.no/v1/publicData/list",
        json=[
            {
                "filename": "lovtidend-avd1-2001-2025.tar.bz2",
                "description": "x",
                "sizeBytes": len(body),
                "lastModified": "2026-11-03T02:31:00Z",
            },
            {
                "filename": "gjeldende-lover.tar.bz2",
                "description": "x",
                "sizeBytes": len(body),
                "lastModified": "2026-11-04T02:32:00Z",
            },
            {
                "filename": "lovtidend-avd2-2026.tar.bz2",
                "description": "x",
                "sizeBytes": 5,
                "lastModified": "2026-11-06T02:31:00Z",
            },
        ],
    )
    for name in ("lovtidend-avd1-2001-2025.tar.bz2", "gjeldende-lover.tar.bz2"):
        httpx_mock.add_response(
            url=f"https://api.lovdata.no/v1/publicData/get/{name}", content=body
        )
    dest = tmp_path / "cache"
    with LovdataClient() as client:
        summary = fetch_impl(dest, client)
    pins = json.loads((dest / "pins.json").read_text())
    assert [p["filename"] for p in pins["archives"]] == [
        "lovtidend-avd1-2001-2025.tar.bz2",
        "gjeldende-lover.tar.bz2",
    ]
    assert all(len(p["sha256"]) == 64 for p in pins["archives"])
    assert "gjeldende-lover.tar.bz2 sha256=" in summary
    assert not (dest / "lovtidend-avd2-2026.tar.bz2").exists()


def test_fetch_refuses_when_the_catalogue_names_no_pilot_archive(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="https://api.lovdata.no/v1/publicData/list", json=[])
    with LovdataClient() as client, pytest.raises(Exception, match="no pilot archive"):
        fetch_impl(tmp_path, client)


def test_fetch_command_reports_network_refusals(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"), is_reusable=True)
    result = runner.invoke(app, ["ops", "legal-time", "fetch", "--dest", str(tmp_path / "c")])
    assert result.exit_code == 1
    assert "fetch refused" in result.output
