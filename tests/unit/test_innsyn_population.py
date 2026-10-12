"""The authority population from SSB KLASS (#509 point 1).

The operator's question: can the population be fetched, stored with its
evidence, and read back using only the CLI? The responses are SSB's own,
captured on 2026-10-12; only the transport is mocked.
"""

import json
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from lovspor.cli import app
from lovspor.errors import (
    ConfigError,
    InnsynRecordError,
    InnsynRefusedError,
    NetworkError,
    ParseError,
)
from lovspor.innsyn.population import (
    FYLKESKOMMUNE_CLASSIFICATION,
    KOMMUNE_CLASSIFICATION,
    Authority,
    PopulationSnapshot,
    _KlassCode,
    codes_url,
    fylkeskommuner,
    latest_snapshot,
    read_codes,
    write_snapshot,
)
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import FIXTURES, KLASS_DAY, fetched, innsyn, mock_klass, runner


def _klass_codes(classification: int) -> list[dict[str, str]]:
    path = FIXTURES / f"klass-{classification}-codesAt-2026-10-12.json"
    codes: list[dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))["codes"]
    return codes


def _snapshot(workspace: Path) -> PopulationSnapshot:
    return latest_snapshot(InnsynWorkspace(workspace, []))


def test_every_kommune_but_uoppgitt_is_in_the_population(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    snapshot = _snapshot(fetched(tmp_path / "innsyn", httpx_mock))
    kommuner = {a.authority_id for a in snapshot.authorities if a.kind == "kommune"}
    expected = {c["code"] for c in _klass_codes(KOMMUNE_CLASSIFICATION) if c["code"] != "9999"}
    assert kommuner == expected
    assert "0301" in kommuner


def test_fylkeskommuner_are_keyed_by_fylkesnummer_without_oslo(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    snapshot = _snapshot(fetched(tmp_path / "innsyn", httpx_mock))
    county = {a.authority_id: a.klass_code for a in snapshot.authorities if a.kind != "kommune"}
    raw = {c["code"] for c in _klass_codes(FYLKESKOMMUNE_CLASSIFICATION)}
    assert set(county.values()) == raw - {"0300", "9900"}
    assert all(code == f"{authority}00" for authority, code in county.items())
    assert {(e.code, e.classification_id) for e in snapshot.excluded} == {
        ("9999", KOMMUNE_CLASSIFICATION),
        ("0300", FYLKESKOMMUNE_CLASSIFICATION),
        ("9900", FYLKESKOMMUNE_CLASSIFICATION),
    }


def test_the_snapshot_records_where_and_when_it_was_read(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    snapshot = _snapshot(fetched(tmp_path / "innsyn", httpx_mock))
    assert snapshot.valid_at == KLASS_DAY
    assert snapshot.read_at.utcoffset() is not None
    assert [s.url for s in snapshot.sources] == [
        "https://data.ssb.no/api/klass/v1/classifications/131/codesAt.json?date=2026-10-12",
        "https://data.ssb.no/api/klass/v1/classifications/127/codesAt.json?date=2026-10-12",
    ]
    assert [s.codes for s in snapshot.sources] == [
        len(_klass_codes(KOMMUNE_CLASSIFICATION)),
        len(_klass_codes(FYLKESKOMMUNE_CLASSIFICATION)),
    ]


def test_the_population_command_lists_the_latest_snapshot(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    workspace = fetched(tmp_path / "innsyn", httpx_mock)
    result = innsyn(workspace, "population")
    assert result.exit_code == 0, result.output
    assert "0301\tkommune\t0301\tOslo - Oslove" in result.stdout.splitlines()
    assert "46\tfylkeskommune\t4600\tVestland fylkeskommune" in result.stdout.splitlines()
    assert "excluded: 0300" in result.stderr
    assert "source: KLASS 131 https://data.ssb.no/" in result.stderr


def test_a_second_fetch_adds_a_snapshot_and_never_replaces_one(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    workspace = fetched(tmp_path / "innsyn", httpx_mock)
    fetched(workspace, httpx_mock)
    assert len(list((workspace / "population").glob("population-*.json"))) == 2
    for path in (workspace / "population").glob("population-*.json"):
        PopulationSnapshot.model_validate_json(path.read_bytes())


def test_population_before_any_fetch_is_refused(tmp_path: Path) -> None:
    result = innsyn(tmp_path, "population")
    assert result.exit_code == 1
    assert "run `innsyn fetch-population` first" in result.stderr


def test_a_klass_error_status_is_refused_and_nothing_is_written(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=codes_url(KOMMUNE_CLASSIFICATION, KLASS_DAY), status_code=503)
    result = innsyn(tmp_path / "innsyn", "fetch-population", "--date", "2026-10-12")
    assert result.exit_code == 1
    assert "HTTP 503" in result.stderr
    assert not (tmp_path / "innsyn").exists()


def test_a_transport_failure_is_a_network_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_exception(httpx.ConnectError("down"))
    with httpx.Client() as client, pytest.raises(NetworkError, match="KLASS 131"):
        read_codes(client, KOMMUNE_CLASSIFICATION, KLASS_DAY)


def test_a_body_that_is_not_klass_codes_is_a_parse_error(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json={"unexpected": []})
    with httpx.Client() as client, pytest.raises(ParseError, match="unexpected response"):
        read_codes(client, KOMMUNE_CLASSIFICATION, KLASS_DAY)


def test_a_fylkeskommune_code_that_is_not_a_fylkesnummer_is_refused() -> None:
    with pytest.raises(ParseError, match="1101"):
        fylkeskommuner([_KlassCode(code="1101", name="Odd")])


def test_a_bad_date_is_a_usage_error(tmp_path: Path) -> None:
    result = innsyn(tmp_path, "fetch-population", "--date", "12.10.2026")
    assert result.exit_code == 2


def test_a_damaged_snapshot_is_refused(tmp_path: Path) -> None:
    (tmp_path / "population").mkdir()
    (tmp_path / "population" / "population-2026-10-12-read-x.json").write_text("{}")
    with pytest.raises(InnsynRecordError):
        _snapshot(tmp_path)


def test_a_letter_names_the_norwegian_form_of_the_authority() -> None:
    oslo = Authority(authority_id="0301", kind="kommune", name="Oslo - Oslove", klass_code="0301")
    vestland = Authority(
        authority_id="46", kind="fylkeskommune", name="Vestland fylkeskommune", klass_code="4600"
    )
    assert oslo.letter_name == "Oslo kommune"
    assert vestland.letter_name == "Vestland fylkeskommune"


def test_the_workspace_has_no_default_and_refuses_the_engine_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVSPOR_INNSYN_DIR", raising=False)
    with pytest.raises(ConfigError, match="no default"):
        InnsynWorkspace(None, [])
    with pytest.raises(ConfigError, match="absolute"):
        InnsynWorkspace(Path("relative"), [])
    repo = Path(__file__).resolve().parents[2]
    with pytest.raises(ConfigError, match="inside"):
        InnsynWorkspace(repo / "data", [repo])


def test_the_command_without_a_workspace_says_how_to_set_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOVSPOR_INNSYN_DIR", raising=False)
    result = runner.invoke(app, ["observatory", "innsyn", "population"])
    assert result.exit_code == 1
    assert "LOVSPOR_INNSYN_DIR" in result.stderr


def test_the_env_var_names_the_workspace(
    tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOVSPOR_INNSYN_DIR", str(tmp_path / "ws"))
    mock_klass(httpx_mock)
    result = runner.invoke(
        app, ["observatory", "innsyn", "fetch-population", "--date", "2026-10-12"]
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ws" / "population").is_dir()


def test_a_missing_parent_is_not_stood_up(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    mock_klass(httpx_mock)
    result = innsyn(tmp_path / "gone" / "innsyn", "fetch-population", "--date", "2026-10-12")
    assert result.exit_code == 1
    assert "is missing" in result.stderr
    assert not (tmp_path / "gone").exists()


def test_writing_the_same_read_twice_is_refused(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    workspace = InnsynWorkspace(fetched(tmp_path / "innsyn", httpx_mock), [])
    with pytest.raises(InnsynRefusedError, match="never replaced"):
        write_snapshot(workspace, latest_snapshot(workspace))
