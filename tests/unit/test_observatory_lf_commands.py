"""`observatory lf-ledger`, `observatory lf-first-seen` and the nightly's post-sweep step (#509).

The operator's question: can the ledger be built, kept current and read using
only the CLI? Every test drives the real command against a real archive root.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.observatory import commands as observatory_commands
from lovspor.observatory.heartbeat import ENV_HEARTBEAT_URL
from lovspor.observatory.lf_commands import refresh_after_sweep
from lovspor.observatory.lf_ledger import ledger_path, read_ledger
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, RetrievalProvenance
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.observatory.sweeps import SweepRun

runner = CliRunner()

#: 22:30 UTC on 1 October is 00:30 on 2 October in Oslo.
LATE = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
EARLY = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
PAGE = "https://kommune.example.invalid/forskrifter"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    monkeypatch.delenv(ENV_HEARTBEAT_URL, raising=False)
    return observatory


def _log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _capture(root: Path, lf_id: str, when: datetime, authority_id: str = "3201") -> None:
    payload = f'<html><a href="https://lovdata.no/dokument/LF/forskrift/{lf_id}">x</a></html>'
    body = payload.encode()
    record = ArtifactObservation(
        authority_id=authority_id,
        url=PAGE,
        observed_at=when,
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="test-agent",
            rate_limit_seconds=1.0,
        ),
        sha256=hashlib.sha256(body).hexdigest(),
        content_type="text/html",
        http_status=200,
    )
    _log(root).append_artifact(record, body)


def _tear(root: Path) -> None:
    with _log(root).log_path.open("ab") as handle:
        handle.write(b'{"torn":')


class TestLfLedger:
    def test_it_builds_the_ledger_and_says_what_it_holds(self, root: Path) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        _capture(root, "2021-01-05-12", LATE, authority_id="4601")

        result = runner.invoke(app, ["observatory", "lf-ledger"])

        assert result.exit_code == 0, result.output
        assert "2 new; ledger holds 2 entries across 2 authorities" in result.stdout
        assert "read 2 html records from byte 0 (2 blobs, 0 missing)" in result.stdout
        assert len(read_ledger(_log(root))) == 2

    def test_a_second_run_reads_from_the_cursor(self, root: Path) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        runner.invoke(app, ["observatory", "lf-ledger"])
        size = _log(root).log_path.stat().st_size

        result = runner.invoke(app, ["observatory", "lf-ledger"])

        assert f"read 0 html records from byte {size}" in result.stdout
        assert "0 new; ledger holds 1 entries across 1 authorities" in result.stdout

    def test_rebuild_reads_from_the_start(self, root: Path) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        runner.invoke(app, ["observatory", "lf-ledger"])

        result = runner.invoke(app, ["observatory", "lf-ledger", "--rebuild"])

        assert "read 1 html records from byte 0" in result.stdout
        assert "0 new" in result.stdout

    def test_a_damaged_log_is_refused(self, root: Path) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        _tear(root)

        result = runner.invoke(app, ["observatory", "lf-ledger"])

        assert result.exit_code == 1
        assert "Refused:" in result.stderr
        assert read_ledger(_log(root)) == []


class TestLfFirstSeen:
    @pytest.fixture
    def built(self, root: Path) -> Path:
        _capture(root, "2020-11-19-2630", EARLY)
        _capture(root, "2021-01-05-12", LATE, authority_id="4601")
        runner.invoke(app, ["observatory", "lf-ledger"])
        return root

    def test_one_day_on_the_oslo_calendar(self, built: Path) -> None:
        result = runner.invoke(app, ["observatory", "lf-first-seen", "--day", "2026-10-02"])

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines() == [
            "\t".join(
                ("2026-10-02", LATE.isoformat(), "4601", "LF", "2021-01-05-12", PAGE),
            )
        ]
        assert "1 entries first seen 2026-10-02 .. 2026-10-02" in result.stderr

    def test_since_includes_the_day_and_everything_after_oldest_first(self, built: Path) -> None:
        result = runner.invoke(app, ["observatory", "lf-first-seen", "--since", "2026-09-30"])

        assert result.exit_code == 0, result.output
        assert [line.split("\t")[4] for line in result.stdout.splitlines()] == [
            "2020-11-19-2630",
            "2021-01-05-12",
        ]

    def test_a_day_with_nothing_lists_nothing(self, built: Path) -> None:
        result = runner.invoke(app, ["observatory", "lf-first-seen", "--day", "2026-10-01"])

        assert (result.exit_code, result.stdout) == (0, "")
        assert "0 entries" in result.stderr

    @pytest.mark.parametrize(
        "arguments",
        [[], ["--day", "2026-10-02", "--since", "2026-10-01"], ["--day", "02.10.2026"]],
    )
    def test_it_asks_for_exactly_one_iso_day(self, built: Path, arguments: list[str]) -> None:
        result = runner.invoke(app, ["observatory", "lf-first-seen", *arguments])

        assert result.exit_code == 2

    def test_a_damaged_ledger_is_refused(self, root: Path) -> None:
        ledger_path(_log(root)).write_text("garbage\n", encoding="utf-8")

        result = runner.invoke(app, ["observatory", "lf-first-seen", "--since", "2026-01-01"])

        assert result.exit_code == 1
        assert "Refused:" in result.stderr


class TestTheNightlyStep:
    def test_it_updates_the_ledger_and_reports(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _capture(root, "2020-11-19-2630", EARLY)

        refresh_after_sweep(ObservatoryRoot(root, []))

        assert "1 new; ledger holds 1 entries" in capsys.readouterr().out
        assert len(read_ledger(_log(root))) == 1

    def test_a_failure_is_loud_and_never_raised(
        self, root: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        _tear(root)

        refresh_after_sweep(ObservatoryRoot(root, []))

        assert "lf-ledger not updated:" in capsys.readouterr().err

    def test_nightly_runs_it_after_the_sweep_and_keeps_the_sweeps_exit_code(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        monkeypatch.setattr(observatory_commands, "_preflight", lambda _root: None)
        monkeypatch.setattr(observatory_commands, "_sweep", lambda _root, _limit: _run("degraded"))

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "1 new; ledger holds 1 entries" in result.stdout
        assert len(read_ledger(_log(root))) == 1

    def test_a_ledger_failure_does_not_fail_a_good_night(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _capture(root, "2020-11-19-2630", EARLY)
        _tear(root)
        monkeypatch.setattr(observatory_commands, "_preflight", lambda _root: None)
        monkeypatch.setattr(observatory_commands, "_sweep", lambda _root, _limit: _run("success"))

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        assert "lf-ledger not updated:" in result.stderr


def _run(status: str) -> SweepRun:
    return SweepRun(
        run_id="r1",
        started_at=EARLY,
        finished_at=LATE,
        active_sources=1,
        sources_completed=1 if status == "success" else 0,
        sources_refused=0 if status == "success" else 1,
        captured=1,
        failed_fetches=0,
        unchanged=0,
        status=status,  # type: ignore[arg-type]
    )
