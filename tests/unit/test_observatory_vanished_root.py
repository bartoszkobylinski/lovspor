"""An archive root that vanishes mid-run is a typed storage failure (issue #534).

On 2026-10-04 the archive volume went away during the nightly sweep. The blob
writer's ``mkdir(parents=True)`` walked up and tried to create ``/Volumes/T7``
itself, the ``PermissionError`` escaped as a traceback, and no run record was
written — so the crashed night read exactly like a night that never started.

Nothing here is mocked but HTTP. The vanish is the real thing: the root is
removed from under a running sweep, from inside the server's response.
"""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

import lovspor.observatory.commands as observatory_commands
import lovspor.observatory.log as observation_log
from lovspor.cli import app
from lovspor.errors import ObservatoryError, StorageBoundaryError, StorageUnavailableError
from lovspor.observatory.catch_up import catch_up_blocker, skip_catch_up
from lovspor.observatory.commands import OBSERVATORY_WORKLOAD
from lovspor.observatory.heartbeat import ENV_HEARTBEAT_URL, FAIL_SUFFIX
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, RetrievalProvenance
from lovspor.observatory.selection import ENV_CAPTURE_SELECTION
from lovspor.observatory.storage import (
    ENV_CORPUS_ROOT,
    ENV_OBSERVATORY_ROOT,
    ObservatoryRoot,
    ensure_below_root,
)
from lovspor.observatory.sweeps import (
    SweepRun,
    append_sweep_run,
    read_sweep_runs,
    sweeps_path,
)

runner = CliRunner()

BAERUM_ID = "3201"
BAERUM_DOMAIN = "baerum.kommune.no"
ROBOTS_URL = f"https://www.{BAERUM_DOMAIN}/robots.txt"
SITEMAP_URL = f"https://www.{BAERUM_DOMAIN}/sitemap.xml"
PAGE_URL = f"https://www.{BAERUM_DOMAIN}/politikk-og-samfunn/ny-forskrift"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
HEARTBEAT = "https://hc.example.invalid/abc123"
OBSERVED_AT = datetime(2026, 10, 4, 7, 30, tzinfo=UTC)
#: 07:00 Oslo time: well clear of the 03:00 trigger, so a load is a load.
BOOT = datetime(2026, 10, 4, 5, 0, tzinfo=UTC)


class _BootClock(datetime):
    @classmethod
    def now(cls, tz: object = None) -> "_BootClock":  # type: ignore[override]
        return cls.fromtimestamp(BOOT.timestamp(), UTC)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    monkeypatch.delenv(ENV_CAPTURE_SELECTION, raising=False)
    monkeypatch.delenv(ENV_HEARTBEAT_URL, raising=False)
    return observatory


def _checked(root: Path) -> ObservatoryRoot:
    return ObservatoryRoot(root, [])


def _provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=2.0,
    )


def _observation(payload: bytes) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id="9999",
        url="https://example.invalid/f",
        observed_at=OBSERVED_AT,
        provenance=_provenance(),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


def _failure() -> FetchFailure:
    return FetchFailure(
        authority_id="9999",
        url="https://example.invalid/missing",
        observed_at=OBSERVED_AT,
        provenance=_provenance(),
        outcome="timeout",
    )


def _run(started: datetime, *, failed: bool = False) -> SweepRun:
    return SweepRun(
        run_id=started.isoformat(),
        started_at=started,
        finished_at=started + timedelta(minutes=5),
        active_sources=0 if failed else 1,
        sources_completed=0 if failed else 1,
        sources_refused=0,
        captured=0,
        failed_fetches=0,
        unchanged=0,
        status="failed" if failed else "success",
        failure_reason="storage_write_failed" if failed else None,
    )


class TestTheRootIsNeverCreated:
    def test_directories_below_an_existing_root_are_created(self, tmp_path: Path) -> None:
        ensure_below_root(_checked(tmp_path), tmp_path / "blobs" / "d8")

        assert (tmp_path / "blobs" / "d8").is_dir()

    def test_a_missing_root_is_a_storage_failure_and_stays_missing(self, tmp_path: Path) -> None:
        root = tmp_path / "Volumes" / "T7" / "lovspor-observatory"

        with pytest.raises(StorageUnavailableError, match="lovspor-observatory"):
            ensure_below_root(_checked(root), root / "blobs" / "d8")

        assert not (tmp_path / "Volumes").exists()

    def test_the_root_itself_is_checked_not_made(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        with pytest.raises(StorageUnavailableError):
            ensure_below_root(_checked(root), root)

        assert not root.exists()

    def test_a_directory_outside_the_root_is_refused(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"
        root.mkdir()

        with pytest.raises(StorageBoundaryError):
            ensure_below_root(_checked(root), tmp_path / "elsewhere")

        assert not (tmp_path / "elsewhere").exists()

    @pytest.mark.parametrize("parts", [("..", "elsewhere"), ("blobs", "..", "..", "elsewhere")])
    def test_a_parent_step_out_of_the_root_is_refused_before_any_mkdir(
        self, tmp_path: Path, parts: tuple[str, ...]
    ) -> None:
        """``relative_to`` is lexical: ``root/../elsewhere`` passed it, and the
        walk made ``elsewhere`` beside the root (Codex test on PR #544)."""
        root = tmp_path / "observatory"
        root.mkdir()

        with pytest.raises(StorageBoundaryError):
            ensure_below_root(_checked(root), root.joinpath(*parts))

        assert sorted(p.name for p in tmp_path.iterdir()) == ["observatory"]
        assert list(root.iterdir()) == []

    def test_the_failure_is_an_observatory_error(self) -> None:
        assert issubclass(StorageUnavailableError, ObservatoryError)


class TestTheLogRefusesAVanishedRoot:
    @pytest.mark.parametrize("vanish_after", ["mkdir", "before_write"])
    def test_a_root_vanishing_during_blob_setup_is_not_recreated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vanish_after: str
    ) -> None:
        """Issue #534 requires safety between the root check and the write too."""
        root = tmp_path / "observatory"
        root.mkdir()
        log = ObservationLog(_checked(root))
        if vanish_after == "mkdir":
            original_mkdir = Path.mkdir

            def mkdir_then_vanish(
                self: Path, mode: int = 0o777, parents: bool = False, exist_ok: bool = False
            ) -> None:
                original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)
                if self == root / "blobs":
                    shutil.rmtree(root)

            monkeypatch.setattr(Path, "mkdir", mkdir_then_vanish)
        else:
            original_ensure = ensure_below_root

            def ensure_then_vanish(checked: ObservatoryRoot, directory: Path) -> None:
                original_ensure(checked, directory)
                shutil.rmtree(root)

            monkeypatch.setattr(observation_log, "ensure_below_root", ensure_then_vanish)

        with pytest.raises(StorageUnavailableError, match="is gone") as caught:
            log.append_artifact(_observation(b"x"), b"x")

        assert isinstance(caught.value.__cause__, FileNotFoundError)
        assert not root.exists()

    def test_a_blob_write_after_the_root_vanished_raises_and_recreates_nothing(
        self, tmp_path: Path
    ) -> None:
        """The #534 traceback: blobs/d8 missing, then blobs, then the root."""
        root = tmp_path / "T7" / "lovspor-observatory"
        root.mkdir(parents=True)
        log = ObservationLog(_checked(root))
        log.append_artifact(_observation(b"first"), b"first")
        shutil.rmtree(tmp_path / "T7")

        with pytest.raises(StorageUnavailableError, match="is gone"):
            log.append_artifact(_observation(b"second"), b"second")

        assert not (tmp_path / "T7").exists()

    def test_a_record_append_after_the_root_vanished_raises_and_recreates_nothing(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "observatory"
        root.mkdir()
        log = ObservationLog(_checked(root))
        root.rmdir()

        with pytest.raises(StorageUnavailableError, match="is gone"):
            log.append(_failure())

        assert not root.exists()

    def test_a_failed_write_under_a_present_root_is_a_storage_failure(self, tmp_path: Path) -> None:
        """Not only a vanish: a write the archive refuses ends the run typed too."""
        (tmp_path / "blobs").write_bytes(b"not a directory")
        log = ObservationLog(_checked(tmp_path))

        with pytest.raises(StorageUnavailableError, match="cannot write"):
            log.append_artifact(_observation(b"x"), b"x")

        assert not log.log_path.exists()


class TestTheRunRecordRefusesAVanishedRoot:
    @pytest.mark.parametrize("writer", ["observation", "sweep"])
    def test_a_sync_failure_is_typed_and_preserves_its_cause(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, writer: str
    ) -> None:
        """The PR promises typed errors for failed writes with the root present."""
        failure = OSError("archive fsync failed")

        def fail_sync(descriptor: int) -> None:
            raise failure

        monkeypatch.setattr(observation_log.os, "fsync", fail_sync)
        checked = _checked(tmp_path)
        with pytest.raises(StorageUnavailableError, match="cannot write") as caught:
            if writer == "observation":
                ObservationLog(checked).append(_failure())
            else:
                append_sweep_run(checked, _run(BOOT))

        assert caught.value.__cause__ is failure
        assert tmp_path.is_dir()

    def test_a_run_is_not_appended_to_a_vanished_root(self, tmp_path: Path) -> None:
        root = tmp_path / "observatory"

        with pytest.raises(StorageUnavailableError):
            append_sweep_run(_checked(root), _run(BOOT, failed=True))

        assert not root.exists()


def _setup_source(root: Path, httpx_mock: HTTPXMock) -> None:
    """Register and activate Bærum through the CLI, the way an operator does."""
    robots = f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n"
    httpx_mock.add_response(url=ROBOTS_URL, text=robots, is_reusable=True)
    registered = runner.invoke(
        app,
        [
            *["observatory", "register-source", "--id", BAERUM_ID],
            *["--name", "Bærum", "--domain", BAERUM_DOMAIN],
        ],
    )
    assert registered.exit_code == 0, registered.output
    check = root / "check.json"
    check.write_text(json.dumps(_check_document()), encoding="utf-8")
    activated = runner.invoke(
        app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
    )
    assert activated.exit_code == 0, activated.output


def _check_document() -> dict[str, object]:
    return {
        "checked_at": "2026-08-18T17:00:00Z",
        "robots_txt_url": ROBOTS_URL,
        "robots_allows": True,
        "terms_reviewed": True,
        "terms_permit_capture": True,
        "rate_limit_seconds": 0.001,
        "user_agent": USER_AGENT,
        "reviewed_by": "Bartosz Kobyliński",
        "note": "test source",
    }


def _serve_site(httpx_mock: HTTPXMock) -> None:
    body = f'<urlset xmlns="{SITEMAP_NS}"><url><loc>{PAGE_URL}</loc></url></urlset>'
    httpx_mock.add_response(url=SITEMAP_URL, content=body.encode())


def _vanish_on_page_fetch(httpx_mock: HTTPXMock, root: Path) -> None:
    """The volume goes away while the page is in flight, as on 2026-10-04."""

    def respond(_: httpx.Request) -> httpx.Response:
        shutil.rmtree(root)
        return httpx.Response(200, content=b"<html>forskrift</html>")

    httpx_mock.add_callback(respond, url=PAGE_URL)


def _break_blob_store(httpx_mock: HTTPXMock, root: Path) -> None:
    """The archive stays, but the write under it fails."""

    def respond(_: httpx.Request) -> httpx.Response:
        shutil.rmtree(root / "blobs", ignore_errors=True)
        (root / "blobs").write_bytes(b"not a directory")
        return httpx.Response(200, content=b"<html>forskrift</html>")

    httpx_mock.add_callback(respond, url=PAGE_URL)


class TestANightlyThatLosesItsArchive:
    def test_a_root_vanishing_mid_sweep_ends_typed_and_creates_nothing(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _setup_source(root, httpx_mock)
        _serve_site(httpx_mock)
        _vanish_on_page_fetch(httpx_mock, root)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), result.exception
        assert "OBSERVATORY SWEEP FAILED" in result.stderr
        assert "reason: storage_unavailable" in result.stderr
        assert "run record not written" in result.stderr
        assert not root.exists()

    def test_the_vanished_night_still_reaches_the_dead_man_switch(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _setup_source(root, httpx_mock)
        _serve_site(httpx_mock)
        _vanish_on_page_fetch(httpx_mock, root)
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        httpx_mock.add_response(url=f"{HEARTBEAT}{FAIL_SUFFIX}", method="POST")

        runner.invoke(app, ["observatory", "nightly"])

        pinged = httpx_mock.get_request(url=f"{HEARTBEAT}{FAIL_SUFFIX}")
        assert pinged is not None
        assert json.loads(pinged.content)["failure_reason"] == "storage_unavailable"

    def test_a_failed_write_under_a_present_root_leaves_a_failed_record(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _setup_source(root, httpx_mock)
        _serve_site(httpx_mock)
        _break_blob_store(httpx_mock, root)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), result.exception
        assert "reason: storage_write_failed" in result.stderr
        runs = read_sweep_runs(sweeps_path(_checked(root)))
        assert [(run.status, run.failure_reason) for run in runs] == [
            ("failed", "storage_write_failed")
        ]

    def test_capture_all_ends_typed_too(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _setup_source(root, httpx_mock)
        _serve_site(httpx_mock)
        _vanish_on_page_fetch(httpx_mock, root)

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert isinstance(result.exception, SystemExit), result.exception
        assert "storage" in result.stderr
        assert not root.exists()


class TestAFailedNightIsAMissedNight:
    """The --catch-up guard (#356/#453) must still sweep after a crashed night."""

    def test_a_failed_run_an_hour_ago_does_not_block_catch_up(self) -> None:
        assert catch_up_blocker([_run(BOOT, failed=True)], None, BOOT + timedelta(hours=1)) is None

    def test_a_storage_failed_nightly_lets_the_next_load_sweep(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(observatory_commands, "datetime", _BootClock)
        _setup_source(root, httpx_mock)
        _serve_site(httpx_mock)
        _break_blob_store(httpx_mock, root)
        runner.invoke(app, ["observatory", "nightly"])
        [failed] = read_sweep_runs(sweeps_path(_checked(root)))
        assert (failed.started_at, failed.status) == (BOOT, "failed")

        assert (
            skip_catch_up(_checked(root), OBSERVATORY_WORKLOAD, BOOT + timedelta(hours=1)) is False
        )

    def test_a_recorded_sweep_at_the_same_moment_would_block_it(self, root: Path) -> None:
        """The control: the guard is live, and only the failure lets the load through."""
        root.mkdir()
        append_sweep_run(_checked(root), _run(BOOT))

        assert (
            skip_catch_up(_checked(root), OBSERVATORY_WORKLOAD, BOOT + timedelta(hours=1)) is True
        )
