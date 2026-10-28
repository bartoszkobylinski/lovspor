"""Observable innsyn contracts at the surviving mutation boundaries."""

import csv
import hashlib
import json
import locale
import os
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import typer
from pytest_httpx import HTTPXMock

from lovspor.errors import ConfigError, InnsynRecordError, InnsynRefusedError
from lovspor.innsyn import app, ledger_commands, population_commands
from lovspor.innsyn.app import instant
from lovspor.innsyn.candidates import _Scan, mailbox_candidates
from lovspor.innsyn.directory import Contact, Evidence, RecipientRecorded, directory_journal
from lovspor.innsyn.held import HeldIds, file_held_ids
from lovspor.innsyn.letters import LetterRequest, render
from lovspor.innsyn.pilot_import import read_register
from lovspor.innsyn.population import (
    Authority,
    PopulationSnapshot,
    _KlassCode,
    klass_client,
    kommuner,
    latest_snapshot,
    read_codes,
    write_snapshot,
)
from lovspor.innsyn.sender import Sender, read_sender
from lovspor.innsyn.waves import WaveInputs, _letter, read_manifest
from lovspor.innsyn.workdays import easter_sunday
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, RetrievalProvenance
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.sources.lovdata import DEFAULT_USER_AGENT
from tests.unit.cli_output import said
from tests.unit.innsyn_support import fetched, innsyn, lovverk_checkout

MOMENT = datetime(2026, 10, 12, 22, 30, tzinfo=UTC)
REQUEST = "4646-20261013T080000Z"
PAGE = "https://www.fjaler.kommune.no/"


@pytest.fixture
def non_oslo_timezone(monkeypatch: pytest.MonkeyPatch):
    previous = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    yield
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def test_default_dates_use_oslo_clock(monkeypatch: pytest.MonkeyPatch, non_oslo_timezone) -> None:

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return MOMENT.astimezone(tz)

    # Replace the clock only, not either function whose behaviour is tested.
    monkeypatch.setattr(app, "datetime", Clock)
    assert population_commands._valid_at(None) == date(2026, 10, 13)
    assert ledger_commands._today(None) == date(2026, 10, 13)


def test_instant_returns_utc_and_names_invalid_option(non_oslo_timezone) -> None:
    parsed = instant("2026-10-13T10:00:00+02:00", "--at")
    assert parsed.isoformat() == "2026-10-13T08:00:00+00:00"
    assert instant("2026-10-13T08:00:00Z", "--at").isoformat() == parsed.isoformat()
    for raw, message in [
        ("bad", "not an ISO-8601 instant: bad"),
        ("2026-10-13T08:00:00", "give an offset or Z"),
    ]:
        with pytest.raises(typer.BadParameter) as error:
            instant(raw, "--at")
        assert str(error.value).startswith(f"--at: {message}")


@pytest.mark.parametrize(
    ("command", "option"), [("fetch-population", "--date"), ("due", "--today")]
)
def test_invalid_dates_name_option_and_input(tmp_path: Path, command: str, option: str) -> None:
    result = innsyn(tmp_path, command, option, "bad")
    assert result.exit_code == 2
    assert said(f"{option}: not an ISO date (YYYY-MM-DD): bad", result.stderr)


@pytest.fixture
def prepared(tmp_path: Path, httpx_mock: HTTPXMock) -> tuple[Path, Path]:
    workspace = fetched(tmp_path / "innsyn", httpx_mock)
    commands = [
        ("set-sender", "--name", "Åse Ødegård", "--email", "ase@example.no"),
        (
            "set-recipient",
            "--id",
            "4646",
            "--address",
            "postmottak@fjaler.kommune.no",
            "--evidence-url",
            PAGE,
            "--read-at",
            "2026-10-12T08:00:00Z",
        ),
    ]
    for args in commands:
        result = innsyn(workspace, *args)
        assert result.exit_code == 0, result.output
    ledger = tmp_path / "lf.jsonl"
    ledger.write_text("", encoding="utf-8")
    out = tmp_path / "wave"
    result = innsyn(workspace, "letters", "--out", str(out), "--lf-ledger", str(ledger))
    assert result.exit_code == 0, result.output
    return workspace, out / "manifest.json"


def test_population_counts_and_snapshot_bytes(prepared: tuple[Path, Path]) -> None:
    workspace, _ = prepared
    snapshot = latest_snapshot(InnsynWorkspace(workspace, []))
    kinds = [a.kind for a in snapshot.authorities]
    result = innsyn(workspace, "population")
    assert (
        f"{kinds.count('kommune')} kommuner + {kinds.count('fylkeskommune')} fylkeskommuner"
        in result.stderr
    )
    fixed = snapshot.model_copy(update={"read_at": MOMENT})
    path = write_snapshot(InnsynWorkspace(workspace, []), fixed)
    assert path.name == "population-2026-10-12-read-20261012T223000.000000Z.json"
    assert (
        path.read_text(encoding="utf-8")
        == json.dumps(json.loads(path.read_bytes()), ensure_ascii=False, indent=2) + "\n"
    )


def test_wave_files_and_letter_wording(prepared: tuple[Path, Path]) -> None:
    _, path = prepared
    manifest = read_manifest(path)
    assert (
        path.read_text(encoding="utf-8")
        == json.dumps(json.loads(path.read_bytes()), ensure_ascii=False, indent=2) + "\n"
    )
    payload = (path.parent / "4646.txt").read_bytes()
    body = payload.decode("utf-8")
    assert "\n\nHei,\n\nJeg ber om innsyn" in body
    assert "Fjaler kommune har fastsatt." in body
    assert "men om kommunens egne arkivdokumenter" in body
    assert "Åse Ødegård" in body
    assert hashlib.sha256(payload).hexdigest() == manifest.letters[0].letter_sha256


def test_known_ids_have_one_line_each() -> None:

    request = LetterRequest(
        authority=Authority(authority_id="4646", kind="kommune", name="Fjaler", klass_code="4646"),
        maalform="nb",
        recipient="post@fjaler.kommune.no",
        lf_ids=("2009-02-16-306", "2024-09-19-2196"),
        sender=Sender(name="Åse", email="ase@example.no", recorded_at=MOMENT),
    )
    body = render(request).body
    assert (
        "- FOR-2009-02-16-306 (dato 16.02.2009, nr. 306)\n"
        "- FOR-2024-09-19-2196 (dato 19.09.2024, nr. 2196)" in body
    )


def test_held_comments_start_at_first_hash(tmp_path: Path) -> None:
    path = tmp_path / "held.txt"
    path.write_text("2009-02-16-306 # café # another comment\n\n# comment\n", encoding="utf-8")
    assert file_held_ids(path) == frozenset({"2009-02-16-306"})


@pytest.mark.parametrize("raw", ["bad", "2026-10-13T08:00:00"])
def test_sent_time_errors_name_option(prepared: tuple[Path, Path], raw: str) -> None:
    workspace, manifest = prepared
    result = innsyn(
        workspace, "record-sent", "--manifest", str(manifest), "--id", "4646", "--sent-at", raw
    )
    assert result.exit_code != 0
    assert said("--sent-at:", result.stderr)
    assert not said("XX", result.stderr)
    assert not said("--SENT-AT", result.stderr)
    assert not (workspace / "requests.jsonl").exists()


def test_sent_output_duplicate_and_listed_fields(prepared: tuple[Path, Path]) -> None:
    workspace, manifest = prepared
    args = (
        "record-sent",
        "--manifest",
        str(manifest),
        "--id",
        "4646",
        "--sent-at",
        "2026-10-13T08:00:00Z",
    )
    result = innsyn(workspace, *args)
    assert result.stdout == f"{REQUEST}: sent to postmottak@fjaler.kommune.no recorded\n"
    before = (workspace / "requests.jsonl").read_bytes()
    duplicate = innsyn(workspace, *args)
    assert duplicate.stderr == f"Refused: {REQUEST} is already in the ledger\n"
    assert (workspace / "requests.jsonl").read_bytes() == before
    for command, extra, at in [
        ("record-outcome", ("--outcome", "partial"), "2026-10-14T08:00:00Z"),
        ("record-klage", (), "2026-10-15T08:00:00Z"),
    ]:
        result = innsyn(workspace, command, "--request", REQUEST, *extra, "--at", at)
        event = "outcome" if command == "record-outcome" else "klage"
        assert result.stdout == f"{REQUEST}: {event} recorded\n"
    row = innsyn(workspace, "requests").stdout.strip("\n").split("\t")
    assert row[5] == "general"
    assert row[8] == "2026-10-14T08:00:00+00:00"
    assert row[11] == "2026-10-15T08:00:00+00:00"


def test_missing_letter_names_manifest_and_authority(prepared: tuple[Path, Path]) -> None:
    workspace, manifest = prepared
    result = innsyn(
        workspace,
        "record-sent",
        "--manifest",
        str(manifest),
        "--id",
        "1134",
        "--sent-at",
        "2026-10-13T08:00Z",
    )
    assert result.stderr == f"Refused: {manifest} has no letter for 1134\n"


def test_malformed_lf_ledger_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "lf.jsonl"
    path.write_text("{bad}\n", encoding="utf-8")
    corpus = ("--corpus", str(lovverk_checkout(tmp_path / "lovverk")))
    args = ("letters", "--out", str(tmp_path / "wave"), "--lf-ledger", str(path))
    result = innsyn(tmp_path, *corpus, *args)
    assert result.exit_code == 1
    assert str(path) in result.stderr
    assert "Refused: None" not in result.stderr


def test_directory_errors_and_unresolved_empty_evidence(prepared: tuple[Path, Path]) -> None:
    workspace, _ = prepared
    args = (
        "set-recipient",
        "--id",
        "4646",
        "--address",
        "bad",
        "--evidence-url",
        PAGE,
        "--read-at",
    )
    invalid = innsyn(workspace, *args, "2026-10-12T08:00Z")
    assert "address" in invalid.stderr
    assert "Refused: None" not in invalid.stderr
    naive = innsyn(workspace, *args, "2026-10-12T08:00")
    assert said("--read-at: give an offset or Z", naive.stderr)
    assert not said("XX", naive.stderr)
    assert not said("--READ-AT", naive.stderr)
    rows = innsyn(workspace, "authorities").stdout.splitlines()
    row = next(line for line in rows if line.startswith("1134\t"))
    assert row.split("\t")[4] == ""


def test_invalid_event_reports_validation(prepared: tuple[Path, Path]) -> None:
    workspace, _ = prepared
    result = innsyn(workspace, "record-case-number", "--request", REQUEST, "--number", " ")
    assert result.exit_code == 1
    assert "saksnummer" in result.stderr
    assert "Refused: None" not in result.stderr


def test_journal_parse_error_explains_evidence(tmp_path: Path) -> None:
    workspace = InnsynWorkspace(tmp_path, [])
    workspace.directory_log.write_text("{bad}\n", encoding="utf-8")
    with pytest.raises(InnsynRecordError) as error:
        directory_journal(workspace).records()
    assert str(error.value).endswith(
        "the file is append-only evidence, so it is refused rather than read past"
    )


def test_missing_configuration_has_exact_instructions(tmp_path: Path) -> None:
    workspace = InnsynWorkspace(tmp_path, [])
    with pytest.raises(InnsynRefusedError) as error:
        read_sender(workspace)
    assert str(error.value) == "no sender; run `innsyn set-sender --name … --email …` first"
    with pytest.raises(InnsynRefusedError) as error:
        latest_snapshot(workspace)
    assert str(error.value) == "no population snapshot; run `innsyn fetch-population` first"
    with pytest.raises(ConfigError, match="no default"):
        InnsynWorkspace(Path("   "), [])


def test_unresolved_letter_refuses_with_authority() -> None:
    authority = Authority(authority_id="4646", kind="kommune", name="Fjaler", klass_code="4646")
    inputs = WaveInputs(
        population=PopulationSnapshot(
            valid_at=MOMENT.date(),
            read_at=MOMENT,
            sources=(),
            authorities=(authority,),
            excluded=(),
        ),
        contacts={"4646": Contact()},
        requests={},
        lf_entries=(),
        held=HeldIds(),
        sender=Sender(name="Åse", email="ase@example.no", recorded_at=MOMENT),
    )
    with pytest.raises(InnsynRefusedError, match="4646 has no recorded recipient"):
        _letter(inputs, authority, (), True)


def _register(tmp_path: Path, fields: dict[str, str]) -> Path:
    row = {
        "authority_id": "4646",
        "sent_at": "2026-10-13T08:00:00Z",
        "recipient": "post@fjaler.kommune.no",
        "n_ids": "1",
        **fields,
    }
    path = tmp_path / "sent.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    (tmp_path / "innsyn-4646.txt").write_text("Åse\n- FOR-2009-02-16-306\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"outcome": "partial; note; more"},
        {"outcome": "refused", "refusal_basis": " § 13 "},
        {"klage_sent_at": "2026-10-20T08:00:00Z"},
        {"kommune_saksnummer": "first\r\nsecond", "acknowledged_at": "2026-10-14T08:00:00Z"},
    ],
)
def test_sparse_pilot_register_preserves_events(
    tmp_path: Path, fields: dict[str, str], non_oslo_timezone
) -> None:
    path = _register(tmp_path, fields)
    (row,) = read_register(path, tmp_path, MOMENT)
    events = [event.model_dump(mode="json") for event in row.events]
    assert events[0]["sent_at"] == "2026-10-13T08:00:00Z"
    assert events[0]["lf_ids"] == ["2009-02-16-306"]
    expected = ["sent"]
    if "acknowledged_at" in fields:
        expected.append("acknowledged")
    if "kommune_saksnummer" in fields:
        expected.append("case_number")
        assert events[-1]["saksnummer"] == "first\r\nsecond"
    if "outcome" in fields:
        expected.append("outcome")
        assert events[-1]["outcome"] == fields["outcome"].split(";")[0]
        assert events[-1]["at"] is None
        assert events[-1]["refusal_basis"] == ("§ 13" if "refusal_basis" in fields else None)
    if "klage_sent_at" in fields:
        expected.append("klage")
        assert events[-1]["klage_sent_at"] == "2026-10-20T08:00:00Z"
    assert [event["event"] for event in events] == expected


def test_missing_sent_time_identifies_authority(tmp_path: Path) -> None:
    path = _register(tmp_path, {"sent_at": " "})
    with pytest.raises(InnsynRecordError, match="4646: no sent_at"):
        read_register(path, tmp_path, MOMENT)


def test_missing_register_columns_are_all_named(tmp_path: Path) -> None:
    path = tmp_path / "sent.csv"
    path.write_text("authority_id\n4646\n", encoding="utf-8")
    with pytest.raises(InnsynRecordError) as error:
        read_register(path, tmp_path, MOMENT)
    assert str(error.value) == f"{path}: missing columns sent_at, recipient, n_ids"


def test_unspecified_codes_do_not_stop_or_overexclude() -> None:
    codes = [_KlassCode(code=code, name=code) for code in ("9909", "9910", "4646")]
    kept, excluded = kommuner(codes)
    assert [a.authority_id for a in kept] == ["9910", "4646"]
    assert [a.code for a in excluded] == ["9909"]


def test_klass_http_contract(httpx_mock: HTTPXMock) -> None:

    httpx_mock.add_response(json={"codes": []})
    with klass_client() as client:
        assert read_codes(client, 131, MOMENT.date()) == []
    request = httpx_mock.get_request()
    assert request is not None
    assert request.headers["user-agent"] == DEFAULT_USER_AGENT
    assert request.headers["accept"] == "application/json"
    assert request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 30.0
    )


def _observation(payload: bytes, url: str, media: str = "text/html") -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="4646",
        url=url,
        observed_at=MOMENT,
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="test",
            rate_limit_seconds=1.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type=media,
        http_status=200,
    )


def test_candidate_media_parameters_and_equal_length_citations(tmp_path: Path) -> None:
    log = ObservationLog(ObservatoryRoot(tmp_path, []))
    payload = b"post@fjaler.kommune.no"
    first = _observation(payload, PAGE + "a", "text/plain;comment=html;charset=utf-8")
    log.append_artifact(first, payload)
    assert mailbox_candidates(log, {"4646": "fjaler.kommune.no"}, None) == []
    first = _observation(payload, PAGE + "b")
    log.append_artifact(first, payload)
    log.append_artifact(_observation(payload, PAGE + "c"), payload)
    (candidate,) = mailbox_candidates(log, {"4646": "fjaler.kommune.no"}, None)
    assert candidate.url == PAGE + "b"
    assert candidate.pages == 1


def test_candidate_cache_survives_blob_removal(tmp_path: Path) -> None:
    log = ObservationLog(ObservatoryRoot(tmp_path, []))
    payload = b"post@fjaler.kommune.no"
    record = _observation(payload, PAGE + "long")
    log.append_artifact(record, payload)
    scan = _Scan(log, {"4646": "fjaler.kommune.no"}, None)
    scan(record)
    log.blob_path(record.sha256).unlink()
    scan(_observation(payload, PAGE))
    assert scan.found[("4646", "post@fjaler.kommune.no")].url == PAGE
    assert scan.found[("4646", "post@fjaler.kommune.no")].pages == 1
    missing = _observation(b"missing", PAGE)
    scan(missing)
    assert len(scan.found) == 1


def test_utf8_files_under_ascii_locale(tmp_path: Path) -> None:
    path = _register(tmp_path, {"kommune_saksnummer": "Åse"})
    held = tmp_path / "held.txt"
    held.write_text("2009-02-16-306 # Åse\n", encoding="utf-8")
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        assert file_held_ids(held) == frozenset({"2009-02-16-306"})
        (row,) = read_register(path, tmp_path, MOMENT)
        assert row.events[-1].saksnummer == "Åse"
        journal = directory_journal(InnsynWorkspace(tmp_path, []))
        record = RecipientRecorded(
            authority_id="4646",
            address="post@example.no",
            recorded_at=MOMENT,
            evidence=Evidence(url="https://example.no/Åse", read_at=MOMENT),
        )
        journal.append([record])
        assert "Åse".encode() in journal.path.read_bytes()
        assert journal.records() == [record]
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


@pytest.mark.parametrize(
    "year", [83, 1700, 1818, 2106, 1954, 1981, 2049, 2076, 2100, 2199, 2500, 3165, 5700]
)
def test_easter_against_independent_gauss_computus(year: int) -> None:
    # Gauss's Gregorian computus, including the two exceptional corrections.
    # Independent of the anonymous algorithm in workdays.easter_sunday.
    century = year // 100
    lunar = (15 + century - century // 4 - (13 + 8 * century) // 25) % 30
    solar = (4 + century - century // 4) % 7
    golden = year % 19
    epact = (19 * golden + lunar) % 30
    shift = (2 * (year % 4) + 4 * (year % 7) + 6 * epact + solar) % 7
    expected = date(year, 3, 22) + timedelta(days=epact + shift)
    if (epact == 29 and shift == 6) or (epact == 28 and shift == 6 and golden > 10):
        expected -= timedelta(days=7)
    assert easter_sunday(year) == expected


@pytest.mark.parametrize("suffix", ["z", "XXZXX"])
def test_instant_rejects_non_iso_zone_suffix(suffix: str) -> None:
    with pytest.raises(typer.BadParameter, match="not an ISO-8601 instant"):
        instant("2026-10-13T08:00:00" + suffix, "--at")


@pytest.mark.parametrize("suffix", ["z", "XXZXX"])
def test_pilot_rejects_non_iso_zone_suffix(tmp_path: Path, suffix: str) -> None:
    path = _register(tmp_path, {"sent_at": "2026-10-13T08:00:00" + suffix})
    with pytest.raises(InnsynRecordError, match="Invalid isoformat string"):
        read_register(path, tmp_path, MOMENT)
