"""Registering and activating capture sources through the CLI.

Nothing is mocked. The commands resolve their registry path the way an
operator's shell does — through ``LOVSPOR_OBSERVATORY_ROOT`` and the ADR-0010
§5 boundary check — because the discovery order *is* the behaviour under test:
a registry path handed in by a fixture would prove nothing about where the
real one lands.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from click.testing import Result
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

import lovspor.observatory.commands as observatory_commands
from lovspor.cli import app
from lovspor.exclusive_workload import default_lock_path, exclusive_workload
from lovspor.observatory.commands import (
    _capture_candidates,
    _echo_cadence,
    _echo_last_sweep,
    _echo_sources,
    _entry_points,
    _hm,
    _record_sweep,
    _sweep_one,
    _SweepTotals,
)
from lovspor.observatory.discovery import Candidate
from lovspor.observatory.events import (
    read_source_events,
    record_fingerprint,
    source_events_path,
)
from lovspor.observatory.freshness import CaptureState, FailureHold
from lovspor.observatory.heartbeat import ENV_HEARTBEAT_URL, FAIL_SUFFIX
from lovspor.observatory.listing import LISTING_METHOD
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)
from lovspor.observatory.registry import SourceRegistry, read_registry
from lovspor.observatory.storage import (
    ENV_CORPUS_ROOT,
    ENV_OBSERVATORY_ROOT,
    ObservatoryRoot,
    engine_root,
)
from lovspor.observatory.sweeps import (
    OBSERVATION_SLA,
    SWEEP_DEADLINE,
    CadenceState,
    SweepRun,
    append_sweep_run,
    latest_sweep_run,
    read_sweep_runs,
    sweeps_path,
)

runner = CliRunner()

BAERUM_ID = "3201"
BAERUM_DOMAIN = "baerum.kommune.no"
ROBOTS_URL = f"https://www.{BAERUM_DOMAIN}/robots.txt"
USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"
HEARTBEAT = "https://hc.example.invalid/abc123"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return observatory


def _check_document(
    *,
    robots_allows: bool = True,
    terms_reviewed: bool = True,
    permits: bool = True,
    rate_limit_seconds: float = 7.0,
) -> dict[str, object]:
    return {
        "checked_at": "2026-08-18T17:00:00Z",
        "robots_txt_url": ROBOTS_URL,
        "robots_allows": robots_allows,
        "terms_reviewed": terms_reviewed,
        "terms_permit_capture": permits,
        "rate_limit_seconds": rate_limit_seconds,
        "user_agent": USER_AGENT,
        "reviewed_by": "Bartosz Kobyliński",
        "note": "Crawl-delay: 7 declared in robots.txt.",
    }


def _write_check(path: Path, document: dict[str, object]) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _register() -> None:
    result = runner.invoke(
        app,
        [
            "observatory",
            "register-source",
            "--id",
            BAERUM_ID,
            "--name",
            "Bærum",
            "--domain",
            BAERUM_DOMAIN,
        ],
    )
    assert result.exit_code == 0, result.output


class TestRegisterSource:
    def test_a_registered_source_is_eligible_but_not_active(self, root: Path) -> None:
        """Registering says the authority is a candidate. Nothing may fetch it."""
        _register()

        registry = read_registry(root / "sources.json")
        record = registry.sources[BAERUM_ID]
        assert record.name == "Bærum"
        assert record.canonical_domain == BAERUM_DOMAIN
        assert record.authority_type == "kommune"
        assert record.active is False
        assert record.access_policy is None
        assert registry.active() == ()

    def test_the_registry_lands_under_the_observatory_root(self, root: Path) -> None:
        _register()

        assert (root / "sources.json").exists()

    def test_registering_reports_the_authority_as_inactive(self, root: Path) -> None:
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "Bærum",
                "--domain",
                BAERUM_DOMAIN,
            ],
        )

        assert result.exit_code == 0, result.output
        assert f"Registered {BAERUM_ID} (Bærum) -> {BAERUM_DOMAIN} [inactive]" in result.output
        assert "Capture stays refused until an access-policy check is recorded." in result.output

    def test_a_fylkeskommune_can_be_registered(self, root: Path) -> None:
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                "42",
                "--name",
                "Agder",
                "--domain",
                "agderfk.no",
                "--type",
                "fylkeskommune",
            ],
        )

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources["42"]
        assert record.authority_type == "fylkeskommune"

    def test_a_second_registration_cannot_overwrite_the_first(self, root: Path) -> None:
        """Re-registering an id would silently drop its access-policy check —
        the one record that says a human authorised traffic to that host."""
        _register()
        activated = _write_check(root / "check.json", _check_document())
        assert (
            runner.invoke(
                app,
                ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(activated)],
            ).exit_code
            == 0
        )

        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "Annen",
                "--domain",
                "annen.no",
            ],
        )

        assert result.exit_code == 1
        assert "already registered" in result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.active is True
        assert record.canonical_domain == BAERUM_DOMAIN

    def test_an_unknown_authority_type_is_refused(self, root: Path) -> None:
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "Bærum",
                "--domain",
                BAERUM_DOMAIN,
                "--type",
                "stat",
            ],
        )

        assert result.exit_code != 0
        assert not (root / "sources.json").exists()

    def test_an_empty_name_is_refused_not_crashed(self, root: Path) -> None:
        """Every other malformed register-source input in this class — an
        unknown --type, a duplicate --id — ends in a clean refusal and exit
        code 1, not an unhandled exception. A blank --name fails the same
        SourceRecord validation and should be refused the same way."""
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "",
                "--domain",
                BAERUM_DOMAIN,
            ],
        )

        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert not (root / "sources.json").exists()


class TestActivateSource:
    def test_a_permitting_check_activates_the_source(self, root: Path) -> None:
        _register()
        check = _write_check(root / "check.json", _check_document())

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.active is True
        assert record.access_policy is not None
        assert record.access_policy.reviewed_by == "Bartosz Kobyliński"
        assert record.access_policy.rate_limit_seconds == 7.0
        assert "Bartosz Kobyliński" in result.output
        assert f"Activated {BAERUM_ID} (Bærum) [{BAERUM_DOMAIN}]" in result.output
        assert "Reviewed by Bartosz Kobyliński; rate limit 7.0s" in result.output

    def test_a_check_that_refuses_capture_leaves_the_source_inactive(self, root: Path) -> None:
        _register()
        check = _write_check(root / "check.json", _check_document(permits=False))

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 1
        assert "Refused" in result.output
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_a_verdict_without_a_review_is_refused(self, root: Path) -> None:
        """ "The terms permit it" cannot stand on a review that never happened;
        the document is rejected exactly as the model rejects it in code."""
        _register()
        check = _write_check(
            root / "check.json", _check_document(terms_reviewed=False, permits=True)
        )

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 1
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_robots_disallowing_blocks_activation(self, root: Path) -> None:
        _register()
        check = _write_check(root / "check.json", _check_document(robots_allows=False))

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 1
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_an_unregistered_source_cannot_be_activated(self, root: Path) -> None:
        root.mkdir(parents=True)
        check = _write_check(root / "check.json", _check_document())

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 1
        assert "not registered" in result.output

    def test_a_malformed_check_document_is_refused(self, root: Path) -> None:
        _register()
        check = root / "check.json"
        check.write_text("{ not json", encoding="utf-8")

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert result.exit_code == 1
        assert "Refused" in result.output
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_a_missing_check_document_is_refused(self, root: Path) -> None:
        _register()

        result = runner.invoke(
            app,
            [
                "observatory",
                "activate-source",
                "--id",
                BAERUM_ID,
                "--check",
                str(root / "absent.json"),
            ],
        )

        assert result.exit_code == 1
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_a_check_path_that_is_a_directory_is_refused_not_crashed(self, root: Path) -> None:
        """Every other malformed --check input in this class — missing,
        unreadable JSON, a check that fails validation, a verdict that
        refuses capture — ends in a clean "Refused: ..." message and exit
        code 1, not an unhandled exception. A directory should be refused
        the same way."""
        _register()
        check_dir = root / "a-directory"
        check_dir.mkdir(parents=True)

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check_dir)]
        )

        # Not `result.exception is None`: under click 8.4 every non-zero exit
        # arrives here as SystemExit, so that assertion is unsatisfiable
        # alongside exit_code == 1. What matters is the *kind* — a clean
        # SystemExit rather than the IsADirectoryError this used to raise.
        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert "Refused" in result.output
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False


class TestRefusalsReachStderr:
    """A refusal that lands on stdout is invisible to an operator piping the
    command's output into a file, which is how these run unattended."""

    def test_a_corrupt_registry_refusal_goes_to_stderr(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "sources.json").write_text("{ not json", encoding="utf-8")

        result = runner.invoke(app, ["observatory", "sources"])

        assert "Refused" in result.stderr
        assert result.stdout == ""

    def test_a_rejected_source_record_refusal_goes_to_stderr(self, root: Path) -> None:
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "",
                "--domain",
                BAERUM_DOMAIN,
            ],
        )

        assert "Refused" in result.stderr
        assert result.stdout == ""


class TestNestedRoot:
    def test_a_root_several_levels_deep_is_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An archive path like /Volumes/T7/lovspor/observatory is ordinary,
        and none of its intermediate directories need exist yet."""
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
        deep = tmp_path / "archive" / "lovspor" / "observatory"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(deep))

        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "Bærum",
                "--domain",
                BAERUM_DOMAIN,
            ],
        )

        assert result.exit_code == 0, result.output
        assert read_registry(deep / "sources.json").sources[BAERUM_ID].name == "Bærum"


class TestListSources:
    def test_an_empty_registry_says_so(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "sources"])

        assert result.exit_code == 0
        assert "No sources registered" in result.output

    def test_an_inactive_source_is_listed_as_inactive(self, root: Path) -> None:
        _register()

        result = runner.invoke(app, ["observatory", "sources"])

        assert result.exit_code == 0
        assert BAERUM_ID in result.output
        assert "[inactive]" in result.output

    def test_an_active_source_shows_who_reviewed_it(self, root: Path) -> None:
        _register()
        check = _write_check(root / "check.json", _check_document())
        runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        result = runner.invoke(app, ["observatory", "sources"])

        assert "[active]" in result.output
        assert "Bartosz Kobyliński" in result.output
        assert "7.0s" in result.output
        assert f"{BAERUM_ID}  Bærum  {BAERUM_DOMAIN}  [active]" in result.output
        assert (
            "checked 2026-08-18 by Bartosz Kobyliński; rate limit 7.0s; "
            f"UA {USER_AGENT}" in result.output
        )

    def test_sources_are_listed_sorted_by_authority_id(self, root: Path) -> None:
        for authority_id, name, domain in [
            ("5001", "Trondheim", "trondheim.kommune.no"),
            (BAERUM_ID, "Bærum", BAERUM_DOMAIN),
        ]:
            result = runner.invoke(
                app,
                [
                    "observatory",
                    "register-source",
                    "--id",
                    authority_id,
                    "--name",
                    name,
                    "--domain",
                    domain,
                ],
            )
            assert result.exit_code == 0, result.output

        result = runner.invoke(app, ["observatory", "sources"])

        assert result.exit_code == 0
        assert result.output.index(BAERUM_ID) < result.output.index("5001")


class TestStorageBoundary:
    def test_an_unset_observatory_root_is_an_ordinary_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_OBSERVATORY_ROOT, raising=False)

        result = runner.invoke(app, ["observatory", "sources"])

        assert result.exit_code == 1
        # stderr, not the merged output: an operator piping stdout into a file
        # must not have the failure silently land in the file instead of the
        # terminal.
        assert "Cannot locate the observatory archive" in result.stderr

    def test_a_root_inside_the_engine_repo_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flagless registry path is the point: there is no way to talk the
        CLI into writing access-policy records into a published repository."""
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(engine_root() / "data" / "observatory"))

        result = runner.invoke(app, ["observatory", "sources"])

        assert result.exit_code == 1
        # stderr, not the merged output: an operator piping stdout into a file
        # must not have the failure silently land in the file instead of the
        # terminal.
        assert "Cannot locate the observatory archive" in result.stderr


class TestUnreadableRegistry:
    def test_a_corrupt_registry_file_is_refused_not_crashed(self, root: Path) -> None:
        """Every malformed input this module handles elsewhere — an
        unreadable --check path, an unknown --type, a duplicate --id — is
        refused with a clean message and exit code 1, not an unhandled
        exception. A registry file that fails to parse should be refused the
        same way rather than propagating read_registry's ParseError past the
        CLI boundary."""
        root.mkdir(parents=True)
        (root / "sources.json").write_text("{ not json", encoding="utf-8")

        result = runner.invoke(app, ["observatory", "sources"])

        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1

    def test_register_source_refuses_a_corrupt_registry(self, root: Path) -> None:
        """``_load`` is shared by every command, and the fix that made it
        refuse rather than crash on a corrupt file was general, not specific
        to ``sources``. register-source must not overwrite the corrupt file
        with a fresh one built from an empty registry — that would silently
        destroy whatever access-policy checks it held."""
        root.mkdir(parents=True)
        (root / "sources.json").write_text("{ not json", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                BAERUM_ID,
                "--name",
                "Bærum",
                "--domain",
                BAERUM_DOMAIN,
            ],
        )

        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert "Refused" in result.stderr
        assert (root / "sources.json").read_text(encoding="utf-8") == "{ not json"

    def test_activate_source_refuses_a_corrupt_registry(self, root: Path) -> None:
        root.mkdir(parents=True)
        (root / "sources.json").write_text("{ not json", encoding="utf-8")
        check = _write_check(root / "check.json", _check_document())

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
        )

        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert "Refused" in result.stderr
        assert (root / "sources.json").read_text(encoding="utf-8") == "{ not json"


def _observation(payload: bytes, url: str = "https://baerum.kommune.no/f") -> ArtifactObservation:
    return ArtifactObservation(
        authority_id=BAERUM_ID,
        url=url,
        observed_at=datetime(2026, 8, 18, 10, 30, tzinfo=UTC),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent=USER_AGENT,
            rate_limit_seconds=7.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


def _archive(root: Path, payload: bytes = b"first") -> ObservationLog:
    """A real archive with one stored observation, under the configured root."""
    log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
    log.append_artifact(_observation(payload), payload)
    return log


class TestComposition:
    """Issue #188. The archive files a followed redirect as a `fetch_failure`,
    because that hop returned no bytes, and three quarters of everything it
    calls a failure is one. Every summary the engine prints is derived from
    what a fetch returned, not from the log — so this is the one place a reader
    of the archive itself gets the honest number instead of writing their own
    query and getting the overstated one."""

    def _log(self, root: Path) -> ObservationLog:
        return ObservationLog(ObservatoryRoot(root, forbidden=[]))

    def _fetch_failure(self, outcome: str) -> FetchFailure:
        return FetchFailure(
            authority_id=BAERUM_ID,
            url=PAGE_URL,
            observed_at=datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
            provenance=_observation(b"x").provenance,
            outcome=outcome,
        )

    def test_a_hop_is_reported_apart_from_a_lost_document(self, root: Path) -> None:
        log = _archive(root)
        log.append(self._fetch_failure("redirect_followed"))
        log.append(self._fetch_failure("http_404"))

        result = runner.invoke(app, ["observatory", "composition"])

        assert result.exit_code == 0, result.output
        assert "records:          3" in result.output
        assert "artifacts:      1" in result.output
        assert "redirect hops:  1" in result.output
        assert "lost documents: 1" in result.output

    def test_both_rates_are_printed_so_a_quoted_number_can_be_recognised(self, root: Path) -> None:
        """Somebody has already quoted the overstated figure. A report that
        silently replaces it leaves them unable to tell which one they had."""
        log = _archive(root)
        for _ in range(2):
            log.append(self._fetch_failure("redirect_followed"))
        log.append(self._fetch_failure("http_404"))

        result = runner.invoke(app, ["observatory", "composition"])

        assert "lost documents:   25.00% of all records" in result.output
        assert "counting kind alone: 75.00%" in result.output

    def test_the_outcome_breakdown_keeps_the_hops_visible(self, root: Path) -> None:
        """The breakdown is the evidence for the headline. A hop that stops
        counting as a failure must not stop being counted at all."""
        log = _archive(root)
        log.append(self._fetch_failure("redirect_followed"))

        result = runner.invoke(app, ["observatory", "composition"])

        assert "\nfailures by outcome\n" in result.output
        assert "redirect_followed" in result.output

    def test_the_composition_labels_every_record_kind(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "composition"])

        assert result.exit_code == 0, result.output
        assert "  tombstones:     0\n" in result.output

    def test_an_empty_archive_reports_zero_rather_than_dividing_by_it(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "composition"])

        assert result.exit_code == 0, result.output
        assert "records:          0" in result.output
        assert "lost documents:   0.00% of all records" in result.output

    def test_a_damaged_log_is_refused_rather_than_summarised(self, root: Path) -> None:
        """A composition folded from a log that could not be read to the end
        is a number about part of the archive presented as the whole."""
        log = _archive(root)
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')

        result = runner.invoke(app, ["observatory", "composition"])

        assert result.exit_code == 1
        assert "log is damaged" in result.stderr


class TestVerify:
    """The audit an operator runs after an interrupted run. Its whole value is
    that it answers "how bad is it?" precisely when the archive is damaged."""

    def test_an_intact_archive_passes(self, root: Path) -> None:
        _archive(root)

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 0, result.output
        assert "artifacts checked: 1" in result.output
        assert "snapshot ok" in result.output

    def test_an_empty_archive_passes(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 0, result.output
        assert "artifacts checked: 0" in result.output

    def test_an_unfinished_final_write_is_named_and_recoverable(self, root: Path) -> None:
        log = _archive(root)
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        # Verbatim, not a fragment: this sentence is what tells someone whether
        # they may cut a line out of an evidence file. Its wording is the
        # deliverable, not decoration around it.
        assert (
            "the final record was never finished — an interrupted run leaves exactly this, "
            "and the fetch it describes was never recorded. `observatory repair` removes that "
            "one line and keeps the log as it stood." in result.output
        )
        assert "snapshot NOT ok" in result.output

    def test_a_corrupted_line_says_do_not_truncate(self, root: Path) -> None:
        """The opposite action from the case above, which is why the audit
        separates them: truncating here would destroy a record nobody has
        been told about."""
        log = _archive(root)
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ corrupted", lines[1], b""]))

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert (
            "line(s) 2 are corrupted — an interrupted append cannot produce this, "
            "so the storage itself is suspect. Do not truncate: restore from backup."
            in result.output
        )

    def test_several_corrupted_lines_are_all_named(self, root: Path) -> None:
        """An operator restoring from backup needs to know how far the damage
        goes, not merely that it exists."""
        log = _archive(root)
        for index, path in enumerate(("g", "h"), start=2):
            log.append_artifact(
                _observation(f"body{index}".encode(), f"https://baerum.kommune.no/{path}"),
                f"body{index}".encode(),
            )
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(
            b"\n".join([lines[0], b"{ corrupted", lines[2], b"{ also corrupted", b""])
        )

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "line(s) 2, 4 are corrupted" in result.output

    def test_a_blob_no_record_mentions_is_reported(self, root: Path) -> None:
        log = _archive(root)
        orphan = log.blob_path("bb" * 32)
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"unreferenced")

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 blobs no record mentions" in result.output

    def test_a_blob_gone_with_no_tombstone_is_reported(self, root: Path) -> None:
        payload = b"first"
        log = _archive(root, payload)
        log.blob_path(hashlib.sha256(payload).hexdigest()).unlink()

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 blobs gone with no tombstone" in result.output

    def test_a_tampered_blob_is_reported(self, root: Path) -> None:
        log = _archive(root, b"first")
        log.blob_path(hashlib.sha256(b"first").hexdigest()).write_bytes(b"tampered")

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 blobs that no longer hash to their record" in result.output

    def test_a_tombstone_whose_blob_is_still_present_is_reported(self, root: Path) -> None:
        """A recorded removal that never happened is its own integrity signal:
        the blob was never deleted, so the tombstone lies about the archive."""
        payload = b"first"
        log = _archive(root, payload)
        log.append(
            Tombstone(
                sha256=hashlib.sha256(payload).hexdigest(),
                removed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                basis="privacy request",
                authorised_by="Bartosz Kobyliński",
            )
        )

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 tombstoned blobs still on disk" in result.output

    def test_a_tombstone_for_never_observed_bytes_is_reported(self, root: Path) -> None:
        _archive(root)
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        log.append(
            Tombstone(
                sha256=hashlib.sha256(b"never seen").hexdigest(),
                removed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                basis="privacy request",
                authorised_by="Bartosz Kobyliński",
            )
        )

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 tombstones for hashes never observed" in result.output

    def test_an_observation_after_its_tombstone_is_reported(self, root: Path) -> None:
        """The public API refuses to write this; only a hand-edited log can
        produce it, which is exactly the sort of tampering the audit exists
        to surface."""
        payload = b"first"
        log = _archive(root, payload)
        digest = hashlib.sha256(payload).hexdigest()
        log.blob_path(digest).unlink()
        observation = _observation(payload)
        log.append(
            Tombstone(
                sha256=digest,
                removed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                basis="legal demand",
                authorised_by="Bartosz Kobyliński",
            )
        )
        log.append(observation)

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "1 observations appended after their tombstone" in result.output

    def test_a_sanctioned_removal_is_not_a_defect(self, root: Path) -> None:
        """ADR-0010 §7: a recorded, explained removal is how bytes are allowed
        to disappear. Reporting it as damage would train an operator to ignore
        the report."""
        payload = b"first"
        log = _archive(root, payload)
        digest = hashlib.sha256(payload).hexdigest()
        log.append(
            Tombstone(
                sha256=digest,
                removed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                basis="privacy request",
                authorised_by="Bartosz Kobyliński",
            )
        )
        log.blob_path(digest).unlink()

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 0, result.output
        assert "removed under a tombstone: 1 (sanctioned)" in result.output
        assert "snapshot ok" in result.output

    def test_an_unset_root_is_an_ordinary_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_OBSERVATORY_ROOT, raising=False)

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "Cannot locate the observatory archive" in result.stderr

    def test_several_orphan_blobs_are_all_counted(self, root: Path) -> None:
        """A single orphan always yields a list of length one; this is the
        only case in the suite where the printed count must track more than
        one item, so it is the only case that can catch a `len(found)` that
        got mutated into a constant."""
        log = _archive(root)
        for digest, payload in (("bb" * 32, b"orphan-one"), ("cc" * 32, b"orphan-two")):
            orphan = log.blob_path(digest)
            orphan.parent.mkdir(parents=True, exist_ok=True)
            orphan.write_bytes(payload)

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "2 blobs no record mentions" in result.output

    def test_distinct_defects_are_all_reported_together_and_in_order(self, root: Path) -> None:
        """`_defects` walks a fixed list of (values, label) pairs and must not
        stop at the first non-empty one — an operator triaging a damaged
        archive needs every defect in one run, not one per invocation. The
        order asserted here is the order the pairs are declared in, which is
        also the order most-structural-first that the module's own comment
        says the report must not drift from."""
        payload_one, payload_two = b"first", b"second"
        log = _archive(root, payload_one)
        log.append_artifact(_observation(payload_two, "https://baerum.kommune.no/g"), payload_two)
        log.blob_path(hashlib.sha256(payload_one).hexdigest()).unlink()
        orphan = log.blob_path("dd" * 32)
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"unreferenced")
        log.append(
            Tombstone(
                sha256=hashlib.sha256(payload_two).hexdigest(),
                removed_at=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
                basis="privacy request",
                authorised_by="Bartosz Kobyliński",
            )
        )

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "snapshot NOT ok" in result.output
        out = result.output
        assert "1 blobs gone with no tombstone" in out
        assert "1 blobs no record mentions" in out
        assert "1 tombstoned blobs still on disk" in out
        assert (
            out.index("blobs gone with no tombstone")
            < out.index("blobs no record mentions")
            < out.index("tombstoned blobs still on disk")
        )

    def test_incomplete_final_record_and_a_malformed_line_are_both_reported(
        self, root: Path
    ) -> None:
        """The two log defects are detected independently by `_scan_lines`;
        nothing about surfacing one should suppress the other when a single
        damaged log has both at once."""
        log = _archive(root)
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        log.append_artifact(_observation(b"third", "https://baerum.kommune.no/h"), b"third")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ corrupted", lines[2][:20]]))

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 1
        assert "line(s) 2 are corrupted" in result.output
        assert "the final record was never finished" in result.output


# The www host, like the real source: robots.txt declares `Host: www...`, and
# the registry's canonical_domain covers it as a subdomain.
SITEMAP_URL = f"https://www.{BAERUM_DOMAIN}/sitemap.xml"
NESTED_SITEMAP_URL = f"https://www.{BAERUM_DOMAIN}/sitemap-forskrifter.xml"
PAGE_URL = f"https://www.{BAERUM_DOMAIN}/politikk-og-samfunn/ny-forskrift"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _urlset(*locs: str, lastmod: str | None = None) -> bytes:
    stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
    body = "".join(f"<url><loc>{loc}</loc>{stamp}</url>" for loc in locs)
    return f'<urlset xmlns="{SITEMAP_NS}">{body}</urlset>'.encode()


def _sitemapindex(*locs: str) -> bytes:
    body = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return f'<sitemapindex xmlns="{SITEMAP_NS}">{body}</sitemapindex>'.encode()


def _activate(root: Path, rate_limit_seconds: float = 0.001) -> None:
    """Register and activate Bærum through the CLI, the way an operator does."""
    _register()
    check = _write_check(
        root / "check.json", _check_document(rate_limit_seconds=rate_limit_seconds)
    )
    result = runner.invoke(
        app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(check)]
    )
    assert result.exit_code == 0, result.output


def _write_sweep(root: Path, *, started: datetime, refused: int = 0) -> None:
    """A recorded sweep, written the way capture-all writes one."""
    append_sweep_run(
        ObservatoryRoot(root, ()),
        SweepRun(
            run_id=started.isoformat(),
            started_at=started,
            finished_at=started + timedelta(minutes=76),
            active_sources=1,
            sources_completed=1 - refused,
            sources_refused=refused,
            captured=3,
            failed_fetches=0,
            unchanged=11,
            status="degraded" if refused else "success",
        ),
    )


def _robots(httpx_mock: HTTPXMock, body: str) -> None:
    httpx_mock.add_response(url=ROBOTS_URL, text=body, is_reusable=True)


class TestEntryPoints:
    def test_explicit_entry_points_are_not_marked_as_a_conventional_probe(self) -> None:
        fetcher = Mock()

        starts = _entry_points(fetcher, SimpleNamespace(access_policy=None), ["one", "two"])

        assert starts.urls == ("one", "two")
        assert starts.probed is False
        fetcher.declared_sitemaps.assert_not_called()

    def test_a_source_without_an_access_policy_has_no_entry_points_or_probe(self) -> None:
        fetcher = Mock()

        starts = _entry_points(fetcher, SimpleNamespace(access_policy=None), None)

        assert starts.urls == ()
        assert starts.probed is False
        fetcher.declared_sitemaps.assert_not_called()

    def test_declared_entry_points_are_not_marked_as_a_conventional_probe(self) -> None:
        fetcher = Mock()
        fetcher.declared_sitemaps.return_value = (SITEMAP_URL, NESTED_SITEMAP_URL)
        record = SimpleNamespace(
            access_policy=SimpleNamespace(robots_txt_url=ROBOTS_URL), listing_entry_points=()
        )

        starts = _entry_points(fetcher, record, None)

        assert starts.urls == (SITEMAP_URL, NESTED_SITEMAP_URL)
        assert starts.probed is False
        fetcher.declared_sitemaps.assert_called_once_with(ROBOTS_URL)

    def test_a_declared_listing_is_walked_alongside_the_sitemap(self) -> None:
        """A source can publish both. The sitemap is the machine index and the
        listing is the page a person reads; neither is a fallback for the
        other, so a listing is added rather than substituted."""
        fetcher = Mock()
        fetcher.declared_sitemaps.return_value = (SITEMAP_URL,)
        listing = f"https://www.{BAERUM_DOMAIN}/kunngjoringer/"
        record = SimpleNamespace(
            access_policy=SimpleNamespace(robots_txt_url=ROBOTS_URL),
            listing_entry_points=(listing,),
        )

        starts = _entry_points(fetcher, record, None)

        assert starts.urls == (SITEMAP_URL, listing)
        assert starts.probed is False

    def test_a_listing_is_the_entry_when_there_is_no_sitemap_at_all(self) -> None:
        """The 116 municipalities of #151: without this the source has no entry
        and a capture proposes nothing while exiting zero."""
        fetcher = Mock()
        fetcher.declared_sitemaps.return_value = ()
        listing = f"https://www.{BAERUM_DOMAIN}/kunngjoringer/"
        record = SimpleNamespace(
            access_policy=SimpleNamespace(robots_txt_url=ROBOTS_URL),
            listing_entry_points=(listing,),
        )

        starts = _entry_points(fetcher, record, None)

        assert starts.urls == (listing,)
        # Not a probe: the operator declared this URL, nothing was guessed.
        assert starts.probed is False


class TestDiscover:
    """Reading what a source publishes about itself. Only the HTTP transport is
    mocked; the registry, the gates, the log and the blob store are real."""

    def test_entry_points_default_to_the_sitemap_robots_declares(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The source answers "where do I start?" in the same file the reviewer
        already checked, so nothing has to guess a host or trust a runbook."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert f"read {SITEMAP_URL}" in result.output
        assert f"sitemap  {PAGE_URL}" in result.output
        assert "candidates: 1" in result.output

    def test_a_candidate_is_proposed_never_fetched(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """The separation that keeps a 40,000-entry sitemap from becoming a
        mass download in one command."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))

        runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert PAGE_URL not in [str(request.url) for request in httpx_mock.get_requests()]

    def test_the_documents_read_are_recorded_as_observations(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """What a municipality listed on a given day is the evidence this
        archive exists to keep."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))

        runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        verify = runner.invoke(app, ["observatory", "verify"])
        assert "artifacts checked: 1" in verify.output
        assert verify.exit_code == 0

    def test_an_explicit_entry_point_overrides_the_declaration(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=NESTED_SITEMAP_URL, content=_urlset(PAGE_URL))

        result = runner.invoke(
            app,
            ["observatory", "discover", "--id", BAERUM_ID, "--entry-point", NESTED_SITEMAP_URL],
        )

        assert result.exit_code == 0, result.output
        assert f"read {NESTED_SITEMAP_URL}" in result.output
        assert SITEMAP_URL not in [str(request.url) for request in httpx_mock.get_requests()]

    def test_repeated_entry_point_options_are_all_used(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """``--entry-point`` is repeatable; a second one must not be dropped
        in favour of the first, and the declared sitemap must not be read at
        all once any explicit entry point is given."""
        second_entry = f"https://www.{BAERUM_DOMAIN}/sitemap-vedtak.xml"
        second_page = f"https://www.{BAERUM_DOMAIN}/politikk-og-samfunn/vedtak"
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=NESTED_SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=second_entry, content=_urlset(second_page))

        result = runner.invoke(
            app,
            [
                "observatory",
                "discover",
                "--id",
                BAERUM_ID,
                "--entry-point",
                NESTED_SITEMAP_URL,
                "--entry-point",
                second_entry,
            ],
        )

        assert result.exit_code == 0, result.output
        assert f"read {NESTED_SITEMAP_URL}" in result.output
        assert f"read {second_entry}" in result.output
        assert "documents read: 2" in result.output
        assert "candidates: 2" in result.output
        assert SITEMAP_URL not in [str(request.url) for request in httpx_mock.get_requests()]

    def test_a_sitemap_index_is_followed(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_sitemapindex(NESTED_SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_SITEMAP_URL, content=_urlset(PAGE_URL))

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert "documents read: 2" in result.output
        assert "candidates: 1" in result.output

    def test_what_was_declined_is_reported_with_its_reason(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A URL that vanished without a trace is indistinguishable from a
        parser that failed to see it."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset("https://oslo.kommune.no/forskrift", PAGE_URL)
        )

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert "skipped: 1" in result.output
        assert "off_source_host  https://oslo.kommune.no/forskrift" in result.output
        assert "candidates: 1" in result.output

    def test_a_source_that_declares_no_sitemap_is_probed_at_the_conventional_path(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert "documents read: 1" in result.output
        assert f"sitemap  {PAGE_URL}" in result.output

    def test_a_source_with_no_sitemap_anywhere_is_refused(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert "declares no sitemap" in result.stderr

    def test_declared_sitemaps_that_cannot_be_read_are_refused_as_declarations(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert result.stderr == (
            f"Refused: {BAERUM_ID} declares sitemaps that could not be read. "
            "Discovery read no documents, so there is nothing to capture.\n"
        )

    def test_an_unreadable_explicit_entry_point_is_reported_not_refused(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)

        result = runner.invoke(
            app,
            [
                "observatory",
                "discover",
                "--id",
                BAERUM_ID,
                "--entry-point",
                SITEMAP_URL,
            ],
        )

        assert result.exit_code == 0, result.output
        assert "documents read: 0" in result.output
        assert f"fetch_failed: http_404  {SITEMAP_URL}" in result.output
        assert "Refused:" not in result.stderr

    def test_an_inactive_source_is_refused_before_any_request(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Nothing observed it and nothing should have tried."""
        _register()

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert "not an activated source" in result.stderr
        assert httpx_mock.get_requests() == []

    def test_an_unregistered_source_is_refused(self, root: Path, httpx_mock: HTTPXMock) -> None:
        result = runner.invoke(app, ["observatory", "discover", "--id", "9999"])

        assert result.exit_code == 1
        assert "not an activated source" in result.stderr
        assert httpx_mock.get_requests() == []

    def test_every_declared_sitemap_is_read_as_an_entry_point(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A robots.txt that lists several ``Sitemap:`` lines is declaring
        several starting points, and dropping all but one would under-report
        what the source says it publishes."""
        second_sitemap = f"https://www.{BAERUM_DOMAIN}/sitemap-vedtak.xml"
        second_page = f"https://www.{BAERUM_DOMAIN}/politikk-og-samfunn/vedtak"
        _activate(root)
        _robots(
            httpx_mock,
            f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\nSitemap: {second_sitemap}\n",
        )
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=second_sitemap, content=_urlset(second_page))

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert "documents read: 2" in result.output
        assert "candidates: 2" in result.output
        assert f"sitemap  {PAGE_URL}" in result.output
        assert f"sitemap  {second_page}" in result.output

    def test_robots_txt_is_fetched_once_for_the_whole_run(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Reading the declared entry points and checking each document
        against the same host's rules share one cached robots.txt fetch,
        not one per document plus one for the declaration."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_sitemapindex(NESTED_SITEMAP_URL))
        httpx_mock.add_response(url=NESTED_SITEMAP_URL, content=_urlset(PAGE_URL))

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        requested = [str(request.url) for request in httpx_mock.get_requests()]
        assert requested.count(ROBOTS_URL) == 1


LISTING_URL = f"https://www.{BAERUM_DOMAIN}/kunngjoringer/"
LISTED_URL = f"https://www.{BAERUM_DOMAIN}/forskrift-om-vann"
OTHER_DOMAIN_LISTING = "https://www.asker.kommune.no/kunngjoringer/"


def _listing_page(href: str = "/forskrift-om-vann", date: str = "2026-08-01") -> bytes:
    body = (
        f'<ul><li><time datetime="{date}">1. august</time>'
        f'<a href="{href}">Forskrift om vann</a></li></ul>'
    )
    return f"<html><body>{body}</body></html>".encode()


def _update(*args: str) -> Result:
    return runner.invoke(app, ["observatory", "update-source", "--id", BAERUM_ID, *args])


def _listings(root: Path) -> tuple[str, ...]:
    return read_registry(root / "sources.json").sources[BAERUM_ID].listing_entry_points


class TestUpdateSource:
    """Declaring a listing entry point on a source that already exists (#184).

    The registry is the only switch that puts a URL on the listing path, and
    #182 shipped the field with no supported way to write it. Hand-editing
    ``sources.json`` was the only route, and that route is exactly the one
    that skips :class:`SourceRecord`'s domain validation — so the feature's
    activation step went around the guarantee the feature is built on.

    Nothing is mocked below except HTTP transport. Each test drives the same
    command an operator types.
    """

    def test_a_listing_on_the_cleared_domain_is_declared(self, root: Path) -> None:
        _register()

        result = _update("--add-listing", LISTING_URL)

        assert result.exit_code == 0, result.output
        assert _listings(root) == (LISTING_URL,)

    def test_a_listing_outside_the_cleared_domain_is_refused(self, root: Path) -> None:
        """The refusal the hand-edited registry could not give.

        The reviewer's access-policy check answers a question about one
        domain. A listing on another host would be crawled under a clearance
        nobody gave for it.
        """
        _register()
        before = (root / "sources.json").read_bytes()

        result = _update("--add-listing", OTHER_DOMAIN_LISTING)

        assert result.exit_code == 1
        assert "Refused" in result.output
        assert (root / "sources.json").read_bytes() == before

    def test_a_refused_update_reaches_stderr(self, root: Path) -> None:
        _register()

        result = runner.invoke(
            app,
            ["observatory", "update-source", "--id", BAERUM_ID, "--add-listing", "not-a-url"],
            catch_exceptions=False,
        )

        assert result.exit_code == 1
        assert "Refused" in result.stderr
        assert result.stdout == ""

    def test_a_declared_listing_can_be_withdrawn(self, root: Path) -> None:
        _register()
        _update("--add-listing", LISTING_URL)

        result = _update("--remove-listing", LISTING_URL)

        assert result.exit_code == 0, result.output
        assert _listings(root) == ()

    def test_distinct_additions_and_removals_are_applied_together(self, root: Path) -> None:
        kept = f"https://www.{BAERUM_DOMAIN}/horinger/"
        added = f"https://www.{BAERUM_DOMAIN}/politiske-saker/"
        _register()
        assert _update("--add-listing", LISTING_URL, "--add-listing", kept).exit_code == 0

        result = _update("--remove-listing", LISTING_URL, "--add-listing", added)

        assert result.exit_code == 0, result.output
        assert _listings(root) == (kept, added)

    def test_withdrawing_a_listing_that_was_never_declared_is_refused(self, root: Path) -> None:
        """A remove that matched nothing is a typo with live consequences.

        The operator means to stop sending traffic to a page. Reporting
        success while the entry they actually declared stays live is the one
        outcome this command must never produce, so a miss is an error rather
        than a no-op.
        """
        _register()
        _update("--add-listing", LISTING_URL)
        before = (root / "sources.json").read_bytes()
        first_missing = f"https://www.{BAERUM_DOMAIN}/kunngjoringer"
        second_missing = f"https://www.{BAERUM_DOMAIN}/horinger"

        result = _update(
            "--remove-listing",
            first_missing,
            "--remove-listing",
            second_missing,
        )

        assert result.exit_code == 1
        assert "not declared" in result.output
        assert result.stderr == (
            f"Refused: {first_missing}, {second_missing} not declared on this source.\n"
        )
        assert result.stdout == ""
        assert (root / "sources.json").read_bytes() == before

    def test_declaring_the_same_listing_twice_leaves_one(self, root: Path) -> None:
        """Re-running the command ends in the state the operator asked for."""
        _register()
        _update("--add-listing", LISTING_URL)

        result = _update("--add-listing", LISTING_URL)

        assert result.exit_code == 0, result.output
        assert _listings(root) == (LISTING_URL,)

    def test_repeating_the_same_listing_in_one_update_leaves_one(self, root: Path) -> None:
        """Idempotence has to hold within a single invocation too.

        The first version compared each addition against what was already in
        the registry and not against what the same command had just added, so
        one repeated ``--add-listing`` declared the page twice. A duplicate
        entry point is not cosmetic: every sweep would fetch that page once
        per copy, against someone else's server.
        """
        _register()

        result = _update("--add-listing", LISTING_URL, "--add-listing", LISTING_URL)

        assert result.exit_code == 0, result.output
        assert _listings(root) == (LISTING_URL,)

    def test_adding_and_removing_the_same_listing_at_once_is_refused(self, root: Path) -> None:
        """One command cannot be told both things about one URL.

        Applying removals first and additions second would let the addition
        win, and the operator who asked for the entry to go would be told the
        update succeeded while the page stayed live — the failure the removal
        rules exist to prevent. Neither order is more correct than the other,
        so the instruction is refused rather than resolved.
        """
        second_listing = f"https://www.{BAERUM_DOMAIN}/horinger/"
        _register()
        _update("--add-listing", LISTING_URL, "--add-listing", second_listing)
        before = (root / "sources.json").read_bytes()

        result = _update(
            "--add-listing",
            LISTING_URL,
            "--add-listing",
            second_listing,
            "--remove-listing",
            LISTING_URL,
            "--remove-listing",
            second_listing,
        )

        assert result.exit_code == 1
        assert "both added and removed" in result.stderr
        assert result.stderr == (
            f"Refused: {LISTING_URL}, {second_listing} is both added and removed.\n"
        )
        assert (root / "sources.json").read_bytes() == before

    def test_an_update_with_nothing_to_do_is_refused(self, root: Path) -> None:
        _register()

        result = _update()

        assert result.exit_code == 1
        assert "Refused" in result.output

    def test_an_unregistered_source_cannot_be_updated(self, root: Path) -> None:
        result = _update("--add-listing", LISTING_URL)

        assert result.exit_code == 1
        assert "not registered" in result.output

    def test_the_update_preserves_the_activation_it_did_not_touch(self, root: Path) -> None:
        """Rebuilding the record must not quietly drop the reviewer's check.

        The record is revalidated rather than mutated, so every field travels
        through the model again — including the access-policy evidence that is
        the whole reason this file is not in the engine repository.
        """
        _activate(root)

        result = _update("--add-listing", LISTING_URL)

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.active is True
        assert record.access_policy is not None
        assert record.access_policy.reviewed_by == "Bartosz Kobyliński"
        assert record.access_policy.rate_limit_seconds == 0.001

    def test_a_listing_declared_through_the_cli_is_read_as_a_listing(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The end-to-end the operator cares about: CLI, registry, discovery.

        This is the path that did not exist. The HTML reader engages only for
        a URL the registry declares, so until a supported command could write
        that field, no sequence of documented commands reached this state —
        which is why the feature had 3,850 unit tests and was still
        unreachable.

        robots.txt declares no sitemap on purpose: that is the case listings
        were built for, the 116 municipalities where discovery otherwise has
        no entry at all.
        """
        _activate(root)
        assert _update("--add-listing", LISTING_URL).exit_code == 0
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=LISTING_URL, content=_listing_page())

        result = runner.invoke(app, ["observatory", "discover", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert f"read {LISTING_URL}" in result.output
        assert f"{LISTING_METHOD}  {LISTED_URL}" in result.output

    def test_without_the_declaration_the_same_page_is_not_read_as_a_listing(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Why ``--entry-point`` was never the workaround.

        Discovery will fetch the page when told to, but the HTML reader is
        gated on registry membership, so an undeclared listing reaches the XML
        parser and is declined. That refusal is deliberate — an error page
        served under a sitemap URL must not become a source of discovery — and
        it is also what made the field unreachable.
        """
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=LISTING_URL, content=_listing_page())

        result = runner.invoke(
            app, ["observatory", "discover", "--id", BAERUM_ID, "--entry-point", LISTING_URL]
        )

        assert LISTED_URL not in result.output
        assert f"unparseable_document  {LISTING_URL}" in result.output


VERDICT_EVIDENCE = "https://github.com/bartoszkobylinski/lovspor/issues/194"


def _verdict_document(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "outcome": "no_machine_reachable_source",
        "routes_checked": ["sitemap and sitemap index", "atom and rss at conventional paths"],
        "evidence": VERDICT_EVIDENCE,
        "reached_at": "2026-08-26T18:00:00Z",
        "reviewed_by": "Bartosz Kobyliński",
        "recheck_after": "2026-11-26T18:00:00Z",
    }
    document.update(overrides)
    return document


def _write_verdict(path: Path, **overrides: object) -> Path:
    path.write_text(json.dumps(_verdict_document(**overrides)), encoding="utf-8")
    return path


class TestRecordVerdict:
    """Recording what an investigation concluded about a source (#195).

    Twelve municipalities in the bootstrap crawl publish no sitemap, no feed
    and no server-rendered index, and their regulations are absent from the
    only source this project may use. That result existed as a line in a shell
    log; the next sweep would have re-derived it from scratch. It is an
    operator decision, so it goes through the same route every other one does.
    """

    def test_a_verdict_is_recorded_against_the_source(self, root: Path) -> None:
        _activate(root)
        document = _write_verdict(root / "verdict.json")

        result = runner.invoke(
            app,
            ["observatory", "record-verdict", "--id", BAERUM_ID, "--verdict", str(document)],
        )

        assert result.exit_code == 0, result.output
        recorded = read_registry(root / "sources.json").sources[BAERUM_ID].capture_verdict
        assert recorded is not None
        assert recorded.outcome == "no_machine_reachable_source"
        assert recorded.evidence == VERDICT_EVIDENCE

    def test_the_verdict_does_not_withdraw_the_activation(self, root: Path) -> None:
        """Two separate decisions. The re-check this verdict schedules depends
        on the source still being cleared to fetch."""
        _activate(root)

        runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(_write_verdict(root / "verdict.json")),
            ],
        )

        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.active is True
        assert record.access_policy is not None

    def test_an_unregistered_source_cannot_carry_a_verdict(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        document = _write_verdict(root / "verdict.json")

        result = runner.invoke(
            app,
            ["observatory", "record-verdict", "--id", BAERUM_ID, "--verdict", str(document)],
        )

        assert result.exit_code == 1
        assert "not registered" in result.stderr

    def test_a_verdict_that_fails_the_schema_is_refused(self, root: Path) -> None:
        """The registry is not a place to put a conclusion with no routes
        behind it — the model decides that, here as in code."""
        _activate(root)
        document = _write_verdict(root / "verdict.json", routes_checked=[])
        before = (root / "sources.json").read_bytes()

        result = runner.invoke(
            app,
            ["observatory", "record-verdict", "--id", BAERUM_ID, "--verdict", str(document)],
        )

        assert result.exit_code == 1
        assert "Refused" in result.stderr
        assert (root / "sources.json").read_bytes() == before

    def test_an_unreadable_verdict_path_is_an_operator_mistake_not_a_traceback(
        self, root: Path
    ) -> None:
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(root / "nope.json"),
            ],
        )

        assert result.exit_code == 1
        assert "Refused" in result.stderr

    def test_a_verdict_path_that_is_a_directory_is_refused_not_crashed(self, root: Path) -> None:
        _activate(root)
        verdict_dir = root / "a-directory"
        verdict_dir.mkdir()

        result = runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(verdict_dir),
            ],
        )

        assert isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        assert "Refused: cannot read the capture verdict" in result.stderr

    def test_recording_a_verdict_reports_when_it_must_be_rechecked(self, root: Path) -> None:
        """The expiry is the point, so the operator sees it at the moment they
        record the verdict rather than discovering it in a file later."""
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(_write_verdict(root / "verdict.json")),
            ],
        )

        assert "2026-11-26" in result.output


class TestStatusShowsHeldSources:
    """A verdict must stay visible. A held source that vanished from the
    report would be the silent zero of #151 one level up — the archive looks
    complete because the sources that fail are no longer counted."""

    def test_status_reports_nothing_when_no_source_is_held(self, root: Path) -> None:
        _activate(root)

        result = runner.invoke(app, ["observatory", "status"])

        assert "held under a verdict" not in result.output

    def test_a_held_source_is_counted(self, root: Path) -> None:
        _activate(root)
        runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(_write_verdict(root / "verdict.json")),
            ],
        )

        result = runner.invoke(app, ["observatory", "status"])

        assert "held under a verdict: 1" in result.output

    def test_a_verdict_past_its_recheck_date_is_reported_as_due(self, root: Path) -> None:
        _activate(root)
        runner.invoke(
            app,
            [
                "observatory",
                "record-verdict",
                "--id",
                BAERUM_ID,
                "--verdict",
                str(
                    _write_verdict(
                        root / "verdict.json",
                        reached_at="2020-01-01T00:00:00Z",
                        recheck_after="2020-04-01T00:00:00Z",
                    )
                ),
            ],
        )

        result = runner.invoke(app, ["observatory", "status"])

        assert "due for re-check: 1" in result.output


class TestRepair:
    """Removing an unfinished final record. This edits evidence, so what it
    refuses matters more than what it does."""

    def _torn(self, root: Path) -> ObservationLog:
        log = _archive(root)
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')
        return log

    def test_an_intact_log_needs_no_repair(self, root: Path) -> None:
        log = _archive(root)
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 0
        assert "nothing to repair" in result.output
        assert log.log_path.read_bytes() == before

    def test_a_dry_run_writes_nothing(self, root: Path) -> None:
        log = self._torn(root)
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair"])

        assert result.exit_code == 0
        assert "unfinished final record: 37 bytes, 1 intact" in result.output
        assert "dry run — nothing written" in result.output
        assert log.log_path.read_bytes() == before
        assert not log.log_path.with_name("observations.jsonl.bak").exists()

    def test_applying_removes_the_line_and_keeps_the_original(self, root: Path) -> None:
        log = self._torn(root)
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 0, result.output
        backup = log.log_path.with_name("observations.jsonl.bak")
        # The path is the point of the message: without it the operator has no
        # way to know a backup was taken, let alone where it went.
        assert f"removed. The log as it stood is kept at {backup}" in result.output
        assert backup.read_bytes() == before
        assert log.scan().complete is True
        assert len(log.scan().records) == 1
        assert runner.invoke(app, ["observatory", "verify"]).exit_code == 0

    def test_applying_with_two_intact_records_keeps_both(self, root: Path) -> None:
        """Splitting on the *first* newline instead of the last would drop
        every intact record but the one immediately before the torn tail —
        silent data loss, not the crash-tail cleanup this command claims to
        be. A single-record fixture can't tell that mutation apart from a
        correct implementation; this one can."""
        log = _archive(root, b"first")
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 0, result.output
        assert "unfinished final record: 37 bytes, 2 intact" in result.output
        backup = log.log_path.with_name("observations.jsonl.bak")
        assert backup.read_bytes() == before
        scan = log.scan()
        assert scan.complete is True
        assert [record.url for record in scan.records] == [
            "https://baerum.kommune.no/f",
            "https://baerum.kommune.no/g",
        ]
        assert runner.invoke(app, ["observatory", "verify"]).exit_code == 0

    def test_the_repaired_log_can_still_be_appended_to(self, root: Path) -> None:
        """The repaired log has to be usable, not merely readable. Losing the
        terminating newline would leave the next append concatenating onto the
        last record — one corrupted line, written by the repair itself."""
        log = self._torn(root)

        assert runner.invoke(app, ["observatory", "repair", "--apply"]).exit_code == 0
        log.append_artifact(_observation(b"later", "https://baerum.kommune.no/later"), b"later")

        scan = log.scan()
        assert scan.complete is True
        assert len(scan.records) == 2
        assert runner.invoke(app, ["observatory", "verify"]).exit_code == 0

    def test_corruption_elsewhere_is_refused(self, root: Path) -> None:
        """Only an unfinished append is safe to fix by deleting. A line that
        was written in full and then damaged carries a record nobody has been
        told about."""
        log = _archive(root)
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ corrupted", lines[1], b""]))
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 1
        assert "line(s) 2 are corrupted" in result.stderr
        assert "restore from backup rather than truncating" in result.stderr
        assert log.log_path.read_bytes() == before

    def test_corruption_alongside_a_torn_tail_is_still_refused(self, root: Path) -> None:
        """Truncating here would clear the symptom the operator can see and
        leave the one that means the disk is failing."""
        log = _archive(root)
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(
            b"\n".join([lines[0], b"{ corrupted", lines[1]]) + b'\n{"kind":"art'
        )
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 1
        assert log.log_path.read_bytes() == before

    def test_an_existing_backup_is_never_overwritten(self, root: Path) -> None:
        """A second repair clobbering the first repair's evidence is exactly
        the loss this command exists to prevent."""
        log = self._torn(root)
        backup = log.log_path.with_name("observations.jsonl.bak")
        backup.write_bytes(b"an earlier repair kept this")
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 1
        assert "already exists" in result.stderr
        assert backup.read_bytes() == b"an earlier repair kept this"
        assert log.log_path.read_bytes() == before

    def test_a_log_that_is_only_an_unfinished_record_becomes_empty(self, root: Path) -> None:
        """No stray newline left behind: the repaired log must scan as empty,
        not as one blank line."""
        root.mkdir(parents=True, exist_ok=True)
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        log.log_path.write_bytes(b'{"kind":"artifact","authority_id":"32')

        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 0, result.output
        assert log.log_path.read_bytes() == b""
        assert log.scan().complete is True

    def test_an_unset_root_is_an_ordinary_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV_OBSERVATORY_ROOT, raising=False)

        result = runner.invoke(app, ["observatory", "repair"])

        assert result.exit_code == 1
        assert "Cannot locate the observatory archive" in result.stderr

    def test_a_dry_run_still_refuses_corruption(self, root: Path) -> None:
        """The malformed-lines refusal is not gated on --apply: a dry run must
        not report a clean bill of health, or a torn tail, when a line
        elsewhere was written in full and then damaged."""
        log = _archive(root)
        log.append_artifact(_observation(b"second", "https://baerum.kommune.no/g"), b"second")
        lines = log.log_path.read_bytes().split(b"\n")
        log.log_path.write_bytes(b"\n".join([lines[0], b"{ corrupted", lines[1], b""]))
        before = log.log_path.read_bytes()

        result = runner.invoke(app, ["observatory", "repair"])

        assert result.exit_code == 1
        assert "line(s) 2 are corrupted" in result.stderr
        assert "restore from backup rather than truncating" in result.stderr
        assert "dry run" not in result.output
        assert log.log_path.read_bytes() == before

    def test_an_empty_archive_needs_no_repair(self, root: Path) -> None:
        """No log file has ever been written yet. `scan()` treats that as
        trivially complete, and repair must agree rather than trying to read
        or back up a file that does not exist."""
        result = runner.invoke(app, ["observatory", "repair", "--apply"])

        assert result.exit_code == 0, result.output
        assert "nothing to repair" in result.output
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        assert not log.log_path.exists()
        assert not log.log_path.with_name("observations.jsonl.bak").exists()


ASKER_ID = "3203"
ASKER_DOMAIN = "asker.kommune.no"
ASKER_ROBOTS_URL = f"https://www.{ASKER_DOMAIN}/robots.txt"
ASKER_SITEMAP_URL = f"https://www.{ASKER_DOMAIN}/sitemap.xml"
ASKER_PAGE_URL = f"https://www.{ASKER_DOMAIN}/forskrift-om-baatplasser"


def _activate_asker(root: Path) -> None:
    """A second activated source, so a sweep has more than one lane."""
    result = runner.invoke(
        app,
        [
            "observatory",
            "register-source",
            "--id",
            ASKER_ID,
            "--name",
            "Asker",
            "--domain",
            ASKER_DOMAIN,
        ],
    )
    assert result.exit_code == 0, result.output
    document = {**_check_document(rate_limit_seconds=0.001), "robots_txt_url": ASKER_ROBOTS_URL}
    check = _write_check(root / "asker-check.json", document)
    result = runner.invoke(
        app, ["observatory", "activate-source", "--id", ASKER_ID, "--check", str(check)]
    )
    assert result.exit_code == 0, result.output


class TestCaptureAll:
    """One politely-paced sweep over every activated source — the steady-state
    delta cycle. One municipality's defect must not cost the other two hundred
    their day's observations, but a defect must still move the exit code."""

    def test_a_reserved_host_defers_without_starting_or_recording_a_sweep(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root.mkdir(parents=True)
        called = False

        def sweep(*_: object) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(observatory_commands, "_sweep", sweep)
        lock_path = default_lock_path()
        with exclusive_workload("llhb-run-arm", lock_path):
            result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert "OBSERVATORY SWEEP DEFERRED" in result.stderr
        assert "deferred_exclusive_workload" in result.stderr
        assert "llhb-run-arm" in result.stderr
        assert called is False
        assert not (root / "sweep-runs.jsonl").exists()

    def _two_sources(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _activate(root)
        _activate_asker(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=ASKER_ROBOTS_URL,
            text=f"User-agent: *\nAllow: /\nSitemap: {ASKER_SITEMAP_URL}\n",
            is_reusable=True,
        )

    def test_every_activated_source_is_swept_in_id_order(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 0, result.output
        assert result.output.index(f"== {BAERUM_ID}") < result.output.index(f"== {ASKER_ID}")
        # The candidates line is part of the sweep's contract, per lane — an
        # operator reading a nightly log needs it to tell a lane that found
        # nothing new from a lane that never proposed anything.
        assert result.output.count("candidates: 1") == 2
        assert result.output.count("captured: 1 | failed: 0 | unchanged since last seen: 0") == 2

    def test_one_refusing_source_does_not_stop_the_sweep(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Bærum's sitemap vanishes; Asker still gets observed. The refusal
        stays loud and the sweep's exit code says the day was not clean."""
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert f"refused: {BAERUM_ID}" in result.stderr
        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output
        assert "sources refused: 1" in result.stderr

    def _asker_only_on_the_wire(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """Two activated sources, but only Asker's server is expected to be
        asked: pytest-httpx refuses any request nothing was registered for, so
        a fetch to Bærum fails the test rather than passing unnoticed."""
        _activate(root)
        _activate_asker(root)
        httpx_mock.add_response(
            url=ASKER_ROBOTS_URL,
            text=f"User-agent: *\nAllow: /\nSitemap: {ASKER_SITEMAP_URL}\n",
            is_reusable=True,
        )
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

    def _record_verdict(self, root: Path, **overrides: object) -> None:
        document = _write_verdict(root / "verdict.json", **overrides)
        result = runner.invoke(
            app,
            ["observatory", "record-verdict", "--id", BAERUM_ID, "--verdict", str(document)],
        )
        assert result.exit_code == 0, result.output

    def test_a_source_held_under_a_verdict_is_not_asked_until_due(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The verdict is the record of an investigation already done (#195):
        Bærum is skipped, says so on the report, and the sweep stays green
        because nothing was left unobserved that could have been."""
        self._asker_only_on_the_wire(root, httpx_mock)
        self._record_verdict(root, recheck_after="2126-08-26T18:00:00Z")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 0, result.output
        assert f"held: {BAERUM_ID} under no_machine_reachable_source until 2126-08-26" in (
            result.output
        )
        assert "sources held under a verdict: 1 of 2" in result.output
        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output
        run = latest_sweep_run(sweeps_path(ObservatoryRoot(root, ())))
        assert run is not None
        assert (run.sources_held, run.sources_completed, run.status) == (1, 1, "success")

    def test_a_due_verdict_is_re_checked_and_a_refusal_is_loud_again(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Expiry is the point: a verdict past its re-check date does not
        spare the source, so the web changing is noticed rather than assumed
        away. Nothing has changed here, and the refusal is as loud as ever."""
        self._asker_only_on_the_wire(root, httpx_mock)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)
        self._record_verdict(
            root, reached_at="2026-01-01T00:00:00Z", recheck_after="2026-02-01T00:00:00Z"
        )

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert "held:" not in result.output
        assert f"refused: {BAERUM_ID}" in result.stderr
        run = latest_sweep_run(sweeps_path(ObservatoryRoot(root, ())))
        assert run is not None
        assert (run.sources_held, run.sources_refused, run.status) == (0, 1, "degraded")

    def test_limit_is_applied_independently_to_each_source(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        self._two_sources(root, httpx_mock)
        baerum_second = f"https://www.{BAERUM_DOMAIN}/forskrift-2"
        asker_second = f"https://www.{ASKER_DOMAIN}/forskrift-2"
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL, baerum_second))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(
            url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL, asker_second)
        )
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all", "--limit", "1"])

        # Exit 1, not 0: since #172 a source stopped by the limit is recorded
        # as capped, which makes the sweep degraded. The bound was deliberate,
        # but the sweep still did not finish observing, and the exit code
        # follows the recorded status rather than the operator's intent.
        assert result.exit_code == 1, result.output
        assert "sources capped: 2 of 2" in result.stderr
        assert result.output.count("stopping at --limit 1") == 2
        assert result.output.count("captured: 1 | failed: 0 | unchanged since last seen: 0") == 2
        requested = [str(request.url) for request in httpx_mock.get_requests()]
        assert baerum_second not in requested
        assert asker_second not in requested

    def test_unchanged_pages_are_skipped_for_every_source_on_the_next_sweep(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The fleet command must preserve capture's freshness rule in every
        lane; otherwise a nightly sweep needlessly downloads the full corpus."""
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(
            url=SITEMAP_URL,
            content=_urlset(PAGE_URL, lastmod="2020-01-01"),
            is_reusable=True,
        )
        httpx_mock.add_response(
            url=ASKER_SITEMAP_URL,
            content=_urlset(ASKER_PAGE_URL, lastmod="2020-01-01"),
            is_reusable=True,
        )
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        first = runner.invoke(app, ["observatory", "capture-all"])
        second = runner.invoke(app, ["observatory", "capture-all"])

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        assert second.output.count("captured: 0 | failed: 0 | unchanged since last seen: 1") == 2
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert run.unchanged == 2
        requested = [str(request.url) for request in httpx_mock.get_requests()]
        assert requested.count(PAGE_URL) == 1
        assert requested.count(ASKER_PAGE_URL) == 1

    def test_conventional_sitemap_refusal_is_loud_but_does_not_stop_the_sweep(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _activate_asker(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)
        httpx_mock.add_response(
            url=ASKER_ROBOTS_URL,
            text=f"User-agent: *\nAllow: /\nSitemap: {ASKER_SITEMAP_URL}\n",
        )
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert (
            f"refused: {BAERUM_ID} declares no sitemap in its robots.txt, and nothing "
            "readable answered at the conventional /sitemap.xml"
        ) in result.stderr
        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output
        assert "sources refused: 1 of 2" in result.stderr

    def test_an_inactive_source_is_not_swept(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """Registration is not permission; the sweep must not widen it."""
        _activate(root)
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                ASKER_ID,
                "--name",
                "Asker",
                "--domain",
                ASKER_DOMAIN,
            ],
        )
        assert result.exit_code == 0, result.output
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 0, result.output
        assert f"== {ASKER_ID}" not in result.output
        assert all(ASKER_DOMAIN not in str(r.url) for r in httpx_mock.get_requests())

    @pytest.mark.parametrize("command", ["capture", "capture-all", "nightly"])
    def test_a_negative_limit_is_a_usage_error_before_any_request(
        self, command: str, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """--limit -1 used to stop the loop at the first candidate and exit 0
        with `captured: 0` — a cron typo away from the silent zero of issue
        #151. Found by the independent test author on PR #156; the bound is
        enforced at the option, before any traffic."""
        _activate(root)

        args = ["observatory", command, "--limit", "-1"]
        if command == "capture":
            args += ["--id", BAERUM_ID]
        result = runner.invoke(app, args)

        assert result.exit_code == 2
        assert httpx_mock.get_requests() == []

    def test_no_activated_sources_is_a_refusal(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert result.stderr == "Refused: no activated sources.\n"

    def test_a_damaged_log_refuses_before_any_request(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        log.log_path.parent.mkdir(parents=True, exist_ok=True)
        log.log_path.write_text('{"torn', encoding="utf-8")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert result.stderr == (
            "Refused: the observation log is damaged. Run `observatory verify` first.\n"
        )
        assert httpx_mock.get_requests() == []

    def test_a_clean_sweep_records_itself(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """Issue #167: the observation log cannot answer whether the sweep ran
        at all, so the run records itself beside the registry."""
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        assert runner.invoke(app, ["observatory", "capture-all"]).exit_code == 0

        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.active_sources, run.sources_refused) == ("success", 2, 0)
        assert (run.captured, run.unchanged) == (2, 0)

    def test_a_zero_source_sweep_is_recorded_as_failed(self, root: Path) -> None:
        """A sweep that observed nothing must not leave green telemetry."""
        started = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)

        _record_sweep(ObservatoryRoot(root, ()), started, 0, _SweepTotals())

        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.active_sources, run.sources_completed, run.status) == (0, 0, "failed")
        # A failure has to say why: telemetry that is red without a cause is
        # barely better than telemetry that is missing.
        assert run.failure_reason == "no_active_sources"

    def test_recorded_sweep_preserves_deferred_count(self, root: Path) -> None:
        started = datetime(2026, 8, 24, 1, 0, tzinfo=UTC)

        run = _record_sweep(ObservatoryRoot(root, ()), started, 1, _SweepTotals(deferred=3))

        assert run.deferred == 3
        recorded = latest_sweep_run(root / "sweep-runs.jsonl")
        assert recorded is not None
        assert recorded.deferred == 3

    def test_a_degraded_sweep_is_recorded_too(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """The run that lost a municipality is the one somebody will want to
        read tomorrow, so it must not be the run that left no record."""
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        assert runner.invoke(app, ["observatory", "capture-all"]).exit_code == 1

        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.sources_completed, run.sources_refused) == ("degraded", 1, 1)

    def test_document_fetch_failures_are_recorded_in_sweep_totals(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 0, result.output
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.captured, run.failed_fetches, run.unchanged) == (1, 1, 0)

    def test_a_real_dead_end_reaches_the_recorded_sweep_as_deferred(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #204, by the route an operator actually takes. The isolated
        tests for this counter hand `_sweep_one` a mocked freshness rule and
        `_record_sweep` a hand-built total — both construct the state directly,
        which is the one move an operator cannot make. Here a municipality
        really 404s, and the second sweep really has to defer it, for the count
        on disk to be anything but zero.
        """
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(
            url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL), is_reusable=True
        )
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        assert runner.invoke(app, ["observatory", "capture-all"]).exit_code == 0
        second = runner.invoke(app, ["observatory", "capture-all"])

        assert second.exit_code == 0, second.output
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.deferred, run.captured, run.failed_fetches) == (1, 0, 0)

    def test_a_complete_pass_reports_capped_as_a_real_false(self) -> None:
        """`capped` is declared `bool` and every call site tests it for truth,
        so returning None would behave identically everywhere while being a lie
        about the declared type. Identity is the only thing that catches it —
        and a NamedTuple validates nothing at runtime, so nothing else will."""
        counts = _capture_candidates(Mock(), (), CaptureState.empty(), 0)

        assert counts.capped is False
        assert (counts.captured, counts.failed, counts.unchanged) == (0, 0, 0)

    def test_redirect_hops_are_preserved_when_the_terminal_fetch_fails(self) -> None:
        """The redirect count describes the route, independently of whether
        that route eventually produced bytes. A two-hop route ending in a 404
        is one lost document and two followed hops, not three failures."""
        candidate = Candidate(
            url=PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
        )
        provenance = _observation(b"page").provenance.model_copy(
            update={"redirect_chain": (OTHER_PAGE_URL, THIRD_PAGE_URL)}
        )
        terminal_failure = FetchFailure(
            authority_id=BAERUM_ID,
            url=PAGE_URL,
            observed_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            provenance=provenance,
            outcome="http_404",
            http_status=404,
        )
        fetcher = Mock()
        fetcher.capture.return_value = terminal_failure

        counts = _capture_candidates(fetcher, (candidate,), CaptureState.empty(), limit=0)

        assert (counts.captured, counts.failed, counts.redirects) == (0, 1, 2)
        assert counts.capped is False

    def test_capped_counts_preserve_redirects_from_every_completed_fetch(self) -> None:
        """Stopping before the next fetch returns the route totals already
        observed; redirects from later records add to rather than replace them."""
        candidates = tuple(
            Candidate(url=url, discovery_method="sitemap", found_in=SITEMAP_URL)
            for url in (PAGE_URL, OTHER_PAGE_URL, THIRD_PAGE_URL)
        )
        first = _observation(b"first", PAGE_URL).model_copy(
            update={
                "provenance": _observation(b"first", PAGE_URL).provenance.model_copy(
                    update={"redirect_chain": (OTHER_PAGE_URL, THIRD_PAGE_URL)}
                )
            }
        )
        second = _observation(b"second", OTHER_PAGE_URL).model_copy(
            update={
                "provenance": _observation(b"second", OTHER_PAGE_URL).provenance.model_copy(
                    update={"redirect_chain": (PAGE_URL, THIRD_PAGE_URL, SITEMAP_URL)}
                )
            }
        )
        fetcher = Mock()
        fetcher.capture.side_effect = (first, second)

        counts = _capture_candidates(fetcher, candidates, CaptureState.empty(), limit=2)

        assert counts == (2, 0, 0, True, 0, 5)
        assert fetcher.capture.call_count == 2

    def test_every_candidate_in_a_pass_uses_the_same_clock_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A long pass must not classify equal-age observations differently
        merely because the wall clock advanced between candidates."""
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        clock = Mock()
        clock.now.return_value = now
        monkeypatch.setattr(observatory_commands, "datetime", clock)
        candidates = tuple(
            Candidate(url=url, discovery_method="sitemap", found_in=SITEMAP_URL)
            for url in (PAGE_URL, OTHER_PAGE_URL)
        )
        observed = {candidate.url: now - timedelta(hours=23) for candidate in candidates}
        fetcher = Mock()

        counts = _capture_candidates(fetcher, candidates, CaptureState(observed, {}), limit=0)

        clock.now.assert_called_once_with(UTC)
        assert (counts.captured, counts.failed, counts.unchanged) == (0, 0, 2)
        fetcher.capture.assert_not_called()

    def test_a_recent_undated_candidate_does_not_spend_the_fetch_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Issue #209. Skipping the repeatedly proposed head of the list must
        leave the limited fetch budget available for an unseen URL behind it."""
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        clock = Mock()
        clock.now.return_value = now
        monkeypatch.setattr(observatory_commands, "datetime", clock)
        recent = Candidate(
            url=PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
        )
        unseen = Candidate(
            url=OTHER_PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
        )
        fetcher = Mock()
        fetcher.capture.return_value = _observation(b"page", OTHER_PAGE_URL)

        counts = _capture_candidates(
            fetcher,
            (recent, unseen),
            CaptureState({PAGE_URL: now - timedelta(hours=1)}, {}),
            limit=1,
        )

        assert counts == (1, 0, 1, False, 0, 0)
        fetcher.capture.assert_called_once_with(OTHER_PAGE_URL, "sitemap")

    def test_multiple_held_candidates_are_all_counted_as_deferred(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        clock = Mock()
        clock.now.return_value = now
        monkeypatch.setattr(observatory_commands, "datetime", clock)
        candidates = tuple(
            Candidate(url=url, discovery_method="sitemap", found_in=SITEMAP_URL)
            for url in (PAGE_URL, OTHER_PAGE_URL)
        )
        holds = {candidate.url: FailureHold("http_404", 1, now) for candidate in candidates}
        fetcher = Mock()

        counts = _capture_candidates(fetcher, candidates, CaptureState({}, holds), limit=0)

        assert counts.deferred == 2
        fetcher.capture.assert_not_called()

    def test_capped_counts_preserve_deferred_candidates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)
        clock = Mock()
        clock.now.return_value = now
        monkeypatch.setattr(observatory_commands, "datetime", clock)
        held = Candidate(url=PAGE_URL, discovery_method="sitemap", found_in=SITEMAP_URL)
        first = Candidate(url=OTHER_PAGE_URL, discovery_method="sitemap", found_in=SITEMAP_URL)
        capped = Candidate(url=THIRD_PAGE_URL, discovery_method="sitemap", found_in=SITEMAP_URL)
        fetcher = Mock()
        fetcher.capture.return_value = _observation(b"page", OTHER_PAGE_URL)
        state = CaptureState({}, {PAGE_URL: FailureHold("http_404", 1, now)})

        counts = _capture_candidates(fetcher, (held, first, capped), state, limit=1)

        assert counts == (1, 0, 0, True, 1, 0)

    def test_one_sweep_source_preserves_deferred_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        candidate = Candidate(url=PAGE_URL, discovery_method="sitemap", found_in=SITEMAP_URL)
        discovery = SimpleNamespace(documents_read=1, candidates=(candidate,))
        monkeypatch.setattr(observatory_commands, "_entry_points", lambda *args: Mock())
        discoverer = Mock()
        discoverer.discover.return_value = discovery
        monkeypatch.setattr(observatory_commands, "Discoverer", lambda *args: discoverer)
        monkeypatch.setattr(observatory_commands, "worth_capturing", lambda *args: False)
        record = Mock(authority_id=BAERUM_ID)

        totals = _sweep_one(Mock(), Mock(), record, CaptureState.empty(), limit=0)

        assert totals.deferred == 1

    def test_using_the_whole_limit_on_the_final_candidate_is_not_capped(self) -> None:
        """A limit is truncation only when another fetch remains."""
        candidate = Candidate(
            url=PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
        )
        fetcher = Mock()
        fetcher.capture.return_value = _observation(b"page", PAGE_URL)

        counts = _capture_candidates(fetcher, (candidate,), CaptureState.empty(), limit=1)

        assert counts.capped is False
        assert (counts.captured, counts.failed, counts.unchanged) == (1, 0, 0)

    def test_unchanged_candidates_after_the_limit_do_not_make_a_source_capped(self) -> None:
        """The fetch budget may be exhausted while the sitemap is still fully swept.

        Candidates already observed after their reported change need no fetch,
        so merely appearing after the last permitted fetch is not truncation.
        """
        fresh = Candidate(
            url=PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
        )
        unchanged = Candidate(
            url=OTHER_PAGE_URL,
            discovery_method="sitemap",
            found_in=SITEMAP_URL,
            site_reported_lastmod="2020-01-01",
        )
        fetcher = Mock()
        fetcher.capture.return_value = _observation(b"page", PAGE_URL)

        counts = _capture_candidates(
            fetcher,
            (fresh, unchanged),
            CaptureState({OTHER_PAGE_URL: datetime(2026, 8, 18, tzinfo=UTC)}, {}),
            limit=1,
        )

        assert counts.capped is False
        assert (counts.captured, counts.failed, counts.unchanged) == (1, 0, 1)
        fetcher.capture.assert_called_once_with(PAGE_URL, "sitemap")

    def test_a_capped_source_is_recorded_and_degrades_the_sweep(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #172: the whole harm is that a truncated source reads as
        finished. The record has to carry the difference, not just stdout."""
        self._two_sources(root, httpx_mock)
        second = f"https://www.{BAERUM_DOMAIN}/forskrift-2"
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL, second))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL))
        httpx_mock.add_response(url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>")

        result = runner.invoke(app, ["observatory", "capture-all", "--limit", "1"])

        assert result.exit_code == 1
        assert f"capped: {BAERUM_ID} stopped at --limit 1" in result.stderr
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        # Bærum was cut short; Asker had exactly one candidate and finished.
        assert (run.sources_capped, run.sources_refused, run.status) == (1, 0, "degraded")

    def test_two_sweeps_leave_two_records(self, root: Path, httpx_mock: HTTPXMock) -> None:
        self._two_sources(root, httpx_mock)
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>", is_reusable=True)
        httpx_mock.add_response(
            url=ASKER_SITEMAP_URL, content=_urlset(ASKER_PAGE_URL), is_reusable=True
        )
        httpx_mock.add_response(
            url=ASKER_PAGE_URL, content=b"<html>baatplasser</html>", is_reusable=True
        )

        runner.invoke(app, ["observatory", "capture-all"])
        runner.invoke(app, ["observatory", "capture-all"])

        assert len(read_sweep_runs(root / "sweep-runs.jsonl")) == 2


OTHER_PAGE_URL = f"https://www.{BAERUM_DOMAIN}/tjenester/forskrift-om-avfallssug"
THIRD_PAGE_URL = f"https://www.{BAERUM_DOMAIN}/tjenester/forskrift-om-vann"


class TestNightly:
    """#167: the scheduled entry point. Preflight is the whole value — a sweep
    that starts on a half-present archive produces records nobody can trust."""

    def test_a_reserved_host_records_deferral_after_successful_preflight(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _activate(root)
        called = False

        def sweep(*_: object) -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(observatory_commands, "_sweep", sweep)
        lock_path = default_lock_path()
        with exclusive_workload("llhb-run-arm", lock_path):
            result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "OBSERVATORY SWEEP DEFERRED" in result.stderr
        assert called is False
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "deferred_exclusive_workload")

    def test_a_missing_archive_fails_and_creates_nothing(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The most damaging thing this command could do to be helpful is
        quietly start a second observatory on the internal disk."""
        missing = root.parent / "not-mounted"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(missing))

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "OBSERVATORY SWEEP FAILED" in result.stderr
        assert "storage_unavailable" in result.stderr
        assert str(missing) in result.stderr
        assert not missing.exists()

    def test_a_missing_archive_records_nothing_either(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With nowhere to write, the message and the exit code are the whole
        output — the remote dead-man switch is what makes the silence loud."""
        missing = root.parent / "not-mounted"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(missing))

        runner.invoke(app, ["observatory", "nightly"])

        assert not (missing / "sweep-runs.jsonl").exists()

    def test_a_missing_registry_is_recorded_with_its_reason(self, root: Path) -> None:
        root.mkdir(parents=True)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "registry_missing" in result.stderr
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "registry_missing")

    def test_a_registry_with_no_active_sources_records_a_failed_run(self, root: Path) -> None:
        """An installed but unconfigured registry must leave failed telemetry.

        This is distinct from a missing registry: preflight can read it, but a
        scheduled run still observed nothing and must not disappear from the
        sweep history without the ``no_active_sources`` reason.
        """
        root.mkdir(parents=True)
        (root / "sources.json").write_text('{"sources": {}}\n', encoding="utf-8")

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "no_active_sources")

    def test_registered_but_inactive_sources_count_as_no_active_sources(self, root: Path) -> None:
        """A non-empty registry is not enough to make a nightly run viable.

        Registration records eligibility; only activation records the human
        access-policy decision that permits network traffic.
        """
        _register()

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "no_active_sources" in result.stderr
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "no_active_sources")

    def test_one_active_source_is_enough_when_another_is_inactive(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Preflight asks whether *any* source is active, not whether all are.

        An authority awaiting review must neither block an approved authority
        nor receive a request of its own.
        """
        _activate(root)
        result = runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                ASKER_ID,
                "--name",
                "Asker",
                "--domain",
                ASKER_DOMAIN,
            ],
        )
        assert result.exit_code == 0, result.output
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        assert all(ASKER_DOMAIN not in str(request.url) for request in httpx_mock.get_requests())
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.active_sources, run.status) == (1, "success")

    def test_a_failed_run_reports_that_nothing_happened(self, root: Path) -> None:
        """The point of a failed record is that no observation took place. The
        status says why it stopped; these say that it stopped before doing
        anything, and without them a record could claim a page was captured by
        a sweep that never started.

        `sources_capped` is deliberately not passed at the call site — the
        model's default is 0 and passing it again only creates a value nobody
        reads. This assertion is what pins the default, so a change to it comes
        back as a red test rather than as a quietly different record.
        """
        root.mkdir(parents=True)

        assert runner.invoke(app, ["observatory", "nightly"]).exit_code == 1

        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.active_sources, run.sources_completed, run.sources_refused) == (0, 0, 0)
        assert (run.sources_capped, run.captured, run.failed_fetches, run.unchanged) == (0, 0, 0, 0)

    def test_a_damaged_log_is_recorded_with_its_reason(self, root: Path) -> None:
        _activate(root)
        (root / "observations.jsonl").write_text("{not json\n", encoding="utf-8")

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "observation_log_damaged" in result.stderr
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "observation_log_damaged")

    def test_a_clean_archive_sweeps(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason, run.captured) == ("success", None, 1)

    def test_the_fetch_limit_is_forwarded_to_the_sweep(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The scheduler-facing wrapper owns the same emergency bound as
        capture-all; accepting the option but dropping it would make a nightly
        run look complete after ignoring the operator's requested limit."""
        _activate(root)
        second_page = f"https://www.{BAERUM_DOMAIN}/tjenester/andre-forskrift"
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL, second_page))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "nightly", "--limit", "1"])

        assert result.exit_code == 1
        assert "stopping at --limit 1" in result.output
        assert all(str(request.url) != second_page for request in httpx_mock.get_requests())
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.sources_capped, run.status, run.failure_reason) == (1, "degraded", None)

    def test_a_clean_sweep_reports_alive(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=HEARTBEAT)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        assert "heartbeat: reported success" in result.output
        assert HEARTBEAT in [str(r.url) for r in httpx_mock.get_requests()]

    def test_a_missing_archive_still_reports_failure(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The case with nowhere to write is the case that most needs the
        switch: the heartbeat does not need the archive to speak."""
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        missing = root.parent / "not-mounted"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(missing))
        httpx_mock.add_response(url=HEARTBEAT + FAIL_SUFFIX)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert [str(r.url) for r in httpx_mock.get_requests()] == [HEARTBEAT + FAIL_SUFFIX]
        assert not missing.exists()

    def test_a_writable_preflight_failure_reports_the_current_failed_run(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failures stored in the archive must also reach the remote switch.

        The request body pins the failure to this invocation and preserves the
        actionable reason; an endpoint-only assertion would allow yesterday's
        failed record, or a reasonless placeholder, to be reported instead.
        """
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        root.mkdir(parents=True)
        httpx_mock.add_response(url=HEARTBEAT + FAIL_SUFFIX)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        assert requests[0].url == HEARTBEAT + FAIL_SUFFIX
        assert b'"status":"failed"' in requests[0].content
        assert b'"failure_reason":"registry_missing"' in requests[0].content
        recorded = latest_sweep_run(root / "sweep-runs.jsonl")
        assert recorded is not None
        assert requests[0].content == recorded.model_dump_json().encode()

    def test_an_undeliverable_failure_heartbeat_preserves_the_preflight_failure(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monitoring is secondary even when the sweep itself already failed.

        A refused failure ping must stay loud without replacing the actionable
        preflight reason or preventing its run record from being written.
        """
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        root.mkdir(parents=True)
        httpx_mock.add_response(url=HEARTBEAT + FAIL_SUFFIX, status_code=503)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert "reason: registry_missing\n" in result.stderr
        assert "heartbeat: NOT DELIVERED (run was failed)\n" in result.stderr
        run = latest_sweep_run(root / "sweep-runs.jsonl")
        assert run is not None
        assert (run.status, run.failure_reason) == ("failed", "registry_missing")

    def test_a_concurrent_run_is_never_reported_as_ours(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The independent author's finding, kept as a property rather than as a
        guard: a timestamp cannot say which invocation wrote a record, so this
        command no longer asks. It reports the run it holds, and another sweep
        writing a newer one — an operator running by hand while the nightly
        fires — cannot be mistaken for it.
        """
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=HEARTBEAT)
        _write_sweep(root, started=datetime.now(UTC) + timedelta(hours=1), refused=1)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        # Ours succeeded; the newer record is degraded. Reporting the log's
        # last line would have said so.
        assert "heartbeat: reported success" in result.output

    def test_an_unarmed_switch_says_so_rather_than_passing_quietly(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_HEARTBEAT_URL, raising=False)
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        # The exact line: a substring assertion passes against any message that
        # merely contains this phrase, which is how five mutants survived here.
        assert "heartbeat: not configured; no dead-man switch is armed\n" in result.stderr

    def test_an_undeliverable_heartbeat_does_not_fail_a_good_sweep(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The sweep is the point; the telemetry is not. But it must be loud —
        a switch that silently stopped reporting looks like a dead machine."""
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=HEARTBEAT, status_code=502)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 0, result.output
        assert "heartbeat: NOT DELIVERED (run was success)\n" in result.stderr

    def test_a_degraded_sweep_still_reports_alive(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The non-zero exit raised by capture-all must still pass through the
        nightly command's reporting guard: degradation is evidence it ran."""
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        _activate(root)
        second_page = f"https://www.{BAERUM_DOMAIN}/tjenester/andre-forskrift"
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL, second_page))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        httpx_mock.add_response(url=HEARTBEAT)

        result = runner.invoke(app, ["observatory", "nightly", "--limit", "1"])

        assert result.exit_code == 1
        heartbeat = [request for request in httpx_mock.get_requests() if request.url == HEARTBEAT]
        assert len(heartbeat) == 1
        assert b'"status":"degraded"' in heartbeat[0].content

    def test_a_crash_before_recording_does_not_report_an_old_success(
        self, root: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Yesterday's green record must not masquerade as this invocation's
        heartbeat when the sweep exits unexpectedly before writing telemetry.

        Since the reporting stopped inferring which record is ours, this holds
        structurally rather than by a guard: a crash never reaches the report at
        all. The assertion is kept because the property is what matters, not the
        mechanism that happens to provide it.
        """
        monkeypatch.setenv(ENV_HEARTBEAT_URL, HEARTBEAT)
        _activate(root)
        yesterday = datetime.now(UTC) - timedelta(days=1)
        append_sweep_run(
            ObservatoryRoot(root, ()),
            SweepRun(
                run_id=yesterday.isoformat(),
                started_at=yesterday,
                finished_at=yesterday,
                active_sources=1,
                sources_completed=1,
                sources_refused=0,
                captured=0,
                failed_fetches=0,
                unchanged=1,
                status="success",
            ),
        )

        def crash_before_recording(*_: object) -> None:
            raise RuntimeError("unexpected sweep crash")

        monkeypatch.setattr(observatory_commands, "_sweep", crash_before_recording)

        result = runner.invoke(app, ["observatory", "nightly"])

        assert result.exit_code == 1
        assert isinstance(result.exception, RuntimeError)
        assert httpx_mock.get_requests() == []


class TestStatus:
    def test_status_sections_render_the_complete_operator_report(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        run = SweepRun(
            run_id="report",
            started_at=datetime(2026, 8, 25, 1, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 25, 2, 16, tzinfo=UTC),
            active_sources=3,
            sources_completed=2,
            sources_refused=1,
            captured=47,
            failed_fetches=1,
            unchanged=4218,
            status="degraded",
        )

        _echo_sources(SourceRegistry())
        _echo_last_sweep(run)
        _echo_cadence(CadenceState(age=timedelta(hours=25), overdue=True), run)

        assert capsys.readouterr().out == (
            "Sources\n"
            "  registered: 0\n"
            "  active:     0\n"
            "\nLast sweep\n"
            "  started:    2026-08-25T01:00:00+00:00\n"
            "  finished:   2026-08-25T02:16:00+00:00\n"
            "  duration:   1h16m\n"
            "  completed:  2 / 3\n"
            "  refused:    1\n"
            "  capped:     0\n"
            "  held:       0\n"
            "  captured:   47 | unchanged: 4218 | deferred: 0\n"
            "  status:     DEGRADED\n"
            "\nCadence\n"
            f"  target:     {_hm(OBSERVATION_SLA)}\n"
            "  age:        25h00m\n"
            f"  deadline:   {_hm(SWEEP_DEADLINE)}\n"
            "  state:      OVERDUE\n"
        )

    def test_never_swept_status_uses_the_exact_operator_label(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _echo_last_sweep(None)
        _echo_cadence(CadenceState(age=None, overdue=True), None)

        assert capsys.readouterr().out == (
            "\nLast sweep\n"
            "  never\n"
            "\nCadence\n"
            f"  target:     {_hm(OBSERVATION_SLA)}\n"
            "  age:        never swept\n"
            f"  deadline:   {_hm(SWEEP_DEADLINE)}\n"
            "  state:      OVERDUE\n"
        )

    def test_duration_discards_partial_minutes(self) -> None:
        assert _hm(timedelta(hours=1, minutes=1, seconds=59)) == "1h01m"

    def test_sweep_totals_preserve_unchanged_counts(self) -> None:
        assert _SweepTotals(unchanged=2).plus(_SweepTotals(unchanged=3)).unchanged == 5

    def test_sweep_totals_add_capped_sources(self) -> None:
        assert _SweepTotals(capped=1).plus(_SweepTotals(capped=2)).capped == 3

    def test_sweep_totals_add_held_sources(self) -> None:
        assert _SweepTotals(held=1).plus(_SweepTotals(held=2)).held == 3
        assert _SweepTotals(deferred=1).plus(_SweepTotals(deferred=2)).deferred == 3

    def test_a_never_swept_archive_says_so_and_exits_nonzero(self, root: Path) -> None:
        """Never swept cannot read as healthy — that is the Mac-was-off case
        the deadline exists for."""
        _activate(root)

        result = runner.invoke(app, ["observatory", "status"])

        assert result.exit_code == 1
        assert "never" in result.output
        assert "OVERDUE" in result.output

    def test_a_recent_sweep_reports_ok(self, root: Path) -> None:
        _activate(root)
        _write_sweep(root, started=datetime.now(UTC) - timedelta(hours=18))

        result = runner.invoke(app, ["observatory", "status"])

        assert result.exit_code == 0
        assert "state:      OK" in result.output
        assert "status:     SUCCESS" in result.output

    def test_a_sweep_older_than_the_deadline_is_overdue(self, root: Path) -> None:
        _activate(root)
        _write_sweep(root, started=datetime.now(UTC) - timedelta(hours=37))

        result = runner.invoke(app, ["observatory", "status"])

        assert result.exit_code == 1
        assert "OVERDUE" in result.output

    def test_a_sweep_stamped_ahead_of_the_clock_is_named_not_called_never(self, root: Path) -> None:
        """ "Never swept" printed beside a printed last sweep would contradict
        itself, and the ahead-of-clock case is the one that would otherwise
        have read as fresh."""
        _activate(root)
        _write_sweep(root, started=datetime.now(UTC) + timedelta(hours=2))

        result = runner.invoke(app, ["observatory", "status"])

        assert result.exit_code == 1
        # The exact line, not a substring of it: a substring assertion passes
        # against any label that merely contains this phrase.
        assert "  age:        unknown — last sweep is stamped ahead of the clock\n" in result.output
        assert "never swept" not in result.output
        assert "  state:      OVERDUE\n" in result.output

    def test_it_counts_registered_and_active_separately(self, root: Path) -> None:
        _activate(root)
        runner.invoke(
            app,
            [
                "observatory",
                "register-source",
                "--id",
                "3203",
                "--name",
                "Nesodden",
                "--domain",
                "nesodden.kommune.no",
            ],
        )

        result = runner.invoke(app, ["observatory", "status"])

        assert "registered: 2" in result.output
        assert "active:     1" in result.output

    def test_a_damaged_sweep_file_refuses_rather_than_reporting_an_older_run(
        self, root: Path
    ) -> None:
        _activate(root)
        _write_sweep(root, started=datetime.now(UTC) - timedelta(hours=1))
        with (root / "sweep-runs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")

        result = runner.invoke(app, ["observatory", "status"])

        assert result.exit_code == 1
        assert "unreadable sweep run" in result.stderr


class TestCapture:
    """Observing what discovery proposes. Hours of politely-spaced requests in
    production, so what it declines to fetch matters as much as what it does."""

    def _ready(self, httpx_mock: HTTPXMock, root: Path, sitemap: bytes) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=sitemap)

    def test_an_undeclared_sitemap_is_found_at_the_conventional_path(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #151: 190 of 358 Norwegian municipalities publish a sitemap
        at /sitemap.xml without declaring it in robots.txt (Phase A sweep,
        2026-08-20). A declaration is the exception, not the rule, so an
        undeclared sitemap is probed at the conventional path — through the
        same gates and recorded the same way as any declared one."""
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output

    def test_a_source_without_any_sitemap_is_a_refusal_not_a_silent_zero(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #151: this used to end `captured: 0` with exit code 0 — in a
        cron job indistinguishable from a healthy no-change run. Nothing was
        captured because nothing COULD be: no sitemap is declared, and the
        conventional path answered with nothing readable either."""
        _activate(root)
        _robots(httpx_mock, "User-agent: *\nAllow: /\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert result.stderr == (
            f"Refused: {BAERUM_ID} declares no sitemap in its robots.txt, and "
            "nothing readable answered at the conventional /sitemap.xml. "
            "Discovery read no documents, so there is nothing to capture.\n"
        )
        assert "captured:" not in result.output

    def test_declared_sitemaps_that_cannot_be_read_refuse_capture(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, status_code=404)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert result.stderr == (
            f"Refused: {BAERUM_ID} declares sitemaps that could not be read. "
            "Discovery read no documents, so there is nothing to capture.\n"
        )
        assert "candidates:" not in result.output
        assert "captured:" not in result.output

    def test_an_unreachable_robots_file_cannot_report_a_successful_zero_capture(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A failed robots lookup also yields no discovery entry points; the
        capture command must preserve the new non-zero automation verdict."""
        _activate(root)
        httpx_mock.add_exception(httpx.ConnectError("unreachable"), url=ROBOTS_URL)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert "nothing to capture" in result.stderr
        assert "candidates:" not in result.output
        assert "captured:" not in result.output

    def test_candidates_are_fetched_and_recorded(self, root: Path, httpx_mock: HTTPXMock) -> None:
        self._ready(httpx_mock, root, _urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert f"200  {PAGE_URL}" in result.output
        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output
        # The sitemap and the page: discovery's own document is evidence too.
        assert "artifacts checked: 2" in runner.invoke(app, ["observatory", "verify"]).output

    def test_a_page_unchanged_since_the_last_run_is_not_fetched_again(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The whole point of reading lastmod: a second pass over a site that
        did not change costs two requests, not thousands."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(PAGE_URL, lastmod="2020-01-01"), is_reusable=True
        )
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in first.output
        assert "captured: 0 | failed: 0 | unchanged since last seen: 1" in second.output
        assert [str(r.url) for r in httpx_mock.get_requests()].count(PAGE_URL) == 1

    def test_only_this_sources_own_sightings_can_hold_a_page_back(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #199. The freshness fold is narrowed to the source being
        captured, so a sighting filed under another authority does not answer
        for this one. The narrowing can only lose a sighting, and a lost
        sighting re-fetches — the direction freshness always errs in."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(PAGE_URL, lastmod="2020-01-01"), is_reusable=True
        )
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        foreign = _observation(b"<html>forskrift</html>", PAGE_URL).model_copy(
            update={"authority_id": "9999"}
        )
        log.append(foreign)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output

    def test_an_undated_page_is_not_fetched_again_on_the_next_pass(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #209. A sitemap entry with no `lastmod` used to be fetched on
        every pass, so a crawl could never reach `captured: 0` and never
        finished. Worse, the fetch budget was spent on the same head of the
        list every round, and the candidates behind it were never reached at
        all — 214 of one municipality's 274 URLs went unseen for two days
        while 58 were re-downloaded."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in first.output
        assert "captured: 0 | failed: 0 | unchanged since last seen: 1" in second.output
        assert [str(r.url) for r in httpx_mock.get_requests()].count(PAGE_URL) == 1

    def test_an_undated_page_never_seen_is_still_fetched(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The window only ever applies to a URL already observed. A candidate
        with no claim and no sighting is fetched, as it always was."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in result.output

    def test_a_page_changed_since_the_last_run_is_fetched_again(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(PAGE_URL, lastmod="2099-01-01"), is_reusable=True
        )
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>v1</html>", is_reusable=True)

        runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 0" in second.output

    def test_a_failed_fetch_is_counted_and_does_not_end_the_run(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>ok</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert f"http_404  {PAGE_URL}" in result.output
        assert "captured: 1 | failed: 1" in result.output

    def test_every_capture_is_counted(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """The tally is the run's only summary of how much of the source was
        actually observed."""
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>one</html>")
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>two</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 2 | failed: 0" in result.output

    def test_every_unchanged_page_is_counted(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """The tally is what tells an operator a second pass did nothing
        because nothing changed, rather than because it stopped early."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL,
            content=_urlset(PAGE_URL, OTHER_PAGE_URL, lastmod="2020-01-01"),
            is_reusable=True,
        )
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>one</html>")
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>two</html>")

        runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 0 | failed: 0 | unchanged since last seen: 2" in second.output

    def test_every_failure_is_counted(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """A run reporting one failure when two pages were unreachable would
        understate how much of the source went unobserved."""
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(url=OTHER_PAGE_URL, status_code=500)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 0 | failed: 2" in result.output

    def test_the_limit_bounds_a_run(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """A first pass over an unfamiliar source should be stoppable without
        waiting hours to find out what it does."""
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>one</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "1"])

        assert "stopping at --limit 1" in result.output
        assert "captured: 1" in result.output
        assert OTHER_PAGE_URL not in [str(r.url) for r in httpx_mock.get_requests()]

    def test_a_limit_equal_to_the_candidate_count_does_not_stop_early(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>one</html>")
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>two</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "2"])

        assert "stopping" not in result.output
        assert "captured: 2 | failed: 0 | unchanged since last seen: 0" in result.output

    def test_a_failed_fetch_counts_toward_the_limit(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The limit bounds *attempts*, not just successes — a run against a
        source returning errors should still stop on schedule."""
        self._ready(httpx_mock, root, _urlset(PAGE_URL, OTHER_PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, status_code=404)

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "1"])

        assert "stopping at --limit 1" in result.output
        assert "captured: 0 | failed: 1" in result.output
        assert OTHER_PAGE_URL not in [str(r.url) for r in httpx_mock.get_requests()]

    def test_a_skipped_candidate_does_not_count_against_the_limit(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Declining to fetch an unchanged page is not the budget --limit
        exists to spend; only an actual fetch attempt should count."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL, lastmod="2020-01-01"))
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>forskrift</html>")
        runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        httpx_mock.add_response(
            url=SITEMAP_URL,
            content=_urlset(PAGE_URL, OTHER_PAGE_URL, THIRD_PAGE_URL, lastmod="2020-01-01"),
        )
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>other</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "1"])

        assert "captured: 1 | failed: 0 | unchanged since last seen: 1" in result.output
        assert "stopping at --limit 1" in result.output
        assert THIRD_PAGE_URL not in [str(r.url) for r in httpx_mock.get_requests()]

    def test_a_damaged_log_is_refused_before_anything_is_fetched(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Appending to a log that cannot be read would bury the damage under
        thousands of new records."""
        _activate(root)
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        log.log_path.write_bytes(b'{"kind":"artifact","authority_id":"32')

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert "log is damaged" in result.stderr
        assert httpx_mock.get_requests() == []

    def test_an_inactive_source_is_refused(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _register()

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 1
        assert "not an activated source" in result.stderr
        assert httpx_mock.get_requests() == []

    def test_an_unregistered_source_is_refused(self, root: Path, httpx_mock: HTTPXMock) -> None:
        result = runner.invoke(app, ["observatory", "capture", "--id", "9999"])

        assert result.exit_code == 1
        assert "not an activated source" in result.stderr
        assert httpx_mock.get_requests() == []

    def test_a_pass_says_how_many_redirects_it_followed(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #188. The hops are not failures and never were counted as
        such here — but they are 75% of what the log files under
        `fetch_failure`, and the pass that stops calling them failures must not
        be the pass that stops mentioning them. A page reached through one
        `www` to apex hop is one captured document and one hop."""
        apex = f"https://{BAERUM_DOMAIN}/politikk-og-samfunn/ny-forskrift"
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=f"https://{BAERUM_DOMAIN}/robots.txt", text="User-agent: *\nAllow: /\n"
        )
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL))
        httpx_mock.add_response(url=PAGE_URL, status_code=301, headers={"Location": apex})
        httpx_mock.add_response(url=apex, content=b"<html>forskrift</html>")

        result = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert result.exit_code == 0, result.output
        assert "captured: 1 | failed: 0" in result.output
        assert "redirect hops: 1" in result.output

    def test_a_url_that_only_ever_404s_is_not_asked_again_next_pass(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Issue #204. Freshness keyed only on sightings, so a URL that never
        produced one was proposed, fetched, failed and proposed again on every
        pass — 962 URLs accounted for 39,672 requests that came back with
        nothing, one municipality's own 404 page asked for 154 times.

        The second pass registers no response for the page: if the URL were
        asked again, this test fails on the missing mock rather than on a
        count, which is the failure mode worth having.
        """
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_response(url=PAGE_URL, status_code=404)

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 0 | failed: 1" in first.output
        assert "captured: 0 | failed: 0" in second.output
        assert "deferred after repeated failure: 1" in second.output
        assert [str(r.url) for r in httpx_mock.get_requests()].count(PAGE_URL) == 1

    def test_a_declined_redirect_is_not_asked_again_next_pass(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The bucket the issue did not predict, and the largest one once #210
        and #212 had removed the loops that masked it: 100 URLs asked 268 times
        in 41 minutes. It carries a 302, so nothing about its status code says
        it will land the same way tomorrow — only the fact that the target sits
        outside the domain a human cleared does.
        """
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_response(
            url=PAGE_URL,
            status_code=302,
            headers={"location": "https://dialog.example.invalid/login"},
        )

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert f"redirect_not_followed  {PAGE_URL}" in first.output
        assert "deferred after repeated failure: 1" in second.output
        assert [str(r.url) for r in httpx_mock.get_requests()].count(PAGE_URL) == 1

    def test_a_timeout_is_asked_again_next_pass(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """A timeout describes the moment, not the URL. Holding it back would
        let one bad night hide a page, which is the mistake freshness has
        always refused to make."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(url=SITEMAP_URL, content=_urlset(PAGE_URL), is_reusable=True)
        httpx_mock.add_exception(httpx.TimeoutException("slow"), url=PAGE_URL, is_reusable=True)

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 0 | failed: 1" in first.output
        assert "captured: 0 | failed: 1" in second.output
        assert "deferred after repeated failure: 0" in second.output
        assert [str(r.url) for r in httpx_mock.get_requests()].count(PAGE_URL) == 2

    def test_a_page_the_site_says_changed_is_asked_again_despite_the_wait(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The override that keeps the backoff from becoming #151's silent
        zero. A `lastmod` later than the failure is the source saying in public
        that the URL is not what it was, and it outranks our own record of
        having been refused."""
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(PAGE_URL, lastmod="2099-01-01"), is_reusable=True
        )
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(url=PAGE_URL, content=b"<html>published</html>")

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID])

        assert "captured: 0 | failed: 1" in first.output
        assert "captured: 1 | failed: 0" in second.output
        assert "deferred after repeated failure: 0" in second.output

    def test_a_deferred_candidate_does_not_spend_the_fetch_limit(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The cost the issue is actually about. Every re-confirmed failure
        occupies a rate-limit slot that a capturable document is queued behind,
        so the waste is not the wasted request — it is the page never reached.
        """
        _activate(root)
        _robots(httpx_mock, f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_URL}\n")
        httpx_mock.add_response(
            url=SITEMAP_URL, content=_urlset(PAGE_URL, OTHER_PAGE_URL), is_reusable=True
        )
        httpx_mock.add_response(url=PAGE_URL, status_code=404)
        httpx_mock.add_response(url=OTHER_PAGE_URL, content=b"<html>reached</html>")

        first = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "1"])
        second = runner.invoke(app, ["observatory", "capture", "--id", BAERUM_ID, "--limit", "1"])

        assert "captured: 0 | failed: 1" in first.output
        assert "captured: 1 | failed: 0" in second.output
        assert f"200  {OTHER_PAGE_URL}" in second.output


NEW_BAERUM_DOMAIN = "baerum.no"
NEW_BAERUM_ROBOTS = f"https://www.{NEW_BAERUM_DOMAIN}/robots.txt"


class TestReplaceSourceDomain:
    """Issue #166. A municipality that starts redirecting to another domain
    cannot have `canonical_domain` edited: the review answers about the old
    host, and leaving it in place would let a clearance obtained for one
    server authorise traffic to another. The supported route withdraws the
    clearance and records the decision."""

    def _replace(self, *extra: str) -> Result:
        return runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                NEW_BAERUM_DOMAIN,
                "--reason",
                "baerum.kommune.no redirects to baerum.no",
                "--by",
                "Bartosz Kobyliński",
                *extra,
            ],
        )

    def _events(self, root: Path) -> list[object]:
        return list(read_source_events(source_events_path(ObservatoryRoot(root, ()))))

    def test_the_domain_moves_and_the_clearance_is_withdrawn(self, root: Path) -> None:
        _activate(root)

        result = self._replace()

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.canonical_domain == NEW_BAERUM_DOMAIN
        assert record.access_policy is None
        assert record.active is False

    def test_the_decision_is_recorded_with_what_it_replaced(self, root: Path) -> None:
        """`sources.json` is current state and cannot say what was withdrawn;
        the fingerprint identifies the record by content, not by timestamp."""
        _activate(root)
        before = read_registry(root / "sources.json").sources[BAERUM_ID]

        self._replace()

        recorded = self._events(root)
        assert len(recorded) == 1
        event = recorded[0]
        assert event.authority_id == BAERUM_ID
        assert (event.from_domain, event.to_domain) == (BAERUM_DOMAIN, NEW_BAERUM_DOMAIN)
        assert event.reason == "baerum.kommune.no redirects to baerum.no"
        assert event.changed_by == "Bartosz Kobyliński"
        assert event.previous_record_sha256 == record_fingerprint(before)

    def test_the_old_clearance_cannot_activate_the_new_domain(self, root: Path) -> None:
        """The property the whole command exists for, asked the way an
        operator would: re-activating with the check that is already on disk
        must fail, because that check was performed against the old host."""
        _activate(root)
        stale = _write_check(root / "stale.json", _check_document())
        self._replace()

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(stale)]
        )

        assert result.exit_code == 1
        assert "was not performed for" in result.stderr
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is False

    def test_a_review_of_the_new_domain_activates_it(self, root: Path) -> None:
        _activate(root)
        self._replace()
        fresh = _write_check(
            root / "fresh.json",
            {**_check_document(), "robots_txt_url": NEW_BAERUM_ROBOTS},
        )

        result = runner.invoke(
            app, ["observatory", "activate-source", "--id", BAERUM_ID, "--check", str(fresh)]
        )

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.active is True
        assert record.canonical_domain == NEW_BAERUM_DOMAIN

    def test_declared_listings_do_not_survive_the_move(self, root: Path) -> None:
        _activate(root)
        runner.invoke(
            app,
            [
                "observatory",
                "update-source",
                "--id",
                BAERUM_ID,
                "--add-listing",
                f"https://www.{BAERUM_DOMAIN}/kunngjoringer",
            ],
        )

        self._replace()

        assert read_registry(root / "sources.json").sources[BAERUM_ID].listing_entry_points == ()

    def test_an_unregistered_source_is_refused(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)

        result = self._replace()

        assert result.exit_code == 1
        assert "not registered" in result.stderr

    def test_replacing_a_domain_with_itself_is_refused_and_writes_nothing(self, root: Path) -> None:
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                BAERUM_DOMAIN,
                "--reason",
                "no reason at all",
                "--by",
                "Bartosz Kobyliński",
            ],
        )

        assert result.exit_code == 1
        assert "already on" in result.stderr
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is True
        assert self._events(root) == []

    def test_an_unattributed_change_is_refused_before_anything_is_written(self, root: Path) -> None:
        """The registry must not move on a decision the history cannot hold —
        the refusal happens while both are still unwritten."""
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                NEW_BAERUM_DOMAIN,
                "--reason",
                "baerum.kommune.no redirects to baerum.no",
                "--by",
                "",
            ],
        )

        assert result.exit_code == 1
        assert "refusing to record this replacement" in result.stderr
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert (record.canonical_domain, record.active) == (BAERUM_DOMAIN, True)
        assert self._events(root) == []

    def test_an_event_log_write_failure_leaves_the_source_safely_deactivated(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registry is deliberately written first: if recording the
        decision fails, the old clearance must not remain live."""
        _activate(root)
        monkeypatch.setattr(
            observatory_commands,
            "append_source_event",
            Mock(side_effect=OSError("archive unavailable")),
        )

        result = self._replace()

        assert result.exit_code == 1
        assert result.stderr == (
            f"Refused: {BAERUM_ID} was moved to {NEW_BAERUM_DOMAIN} and deactivated, "
            "but the decision could not be recorded: archive unavailable. The source "
            "is safe — it has no clearance — but the history is incomplete; record it "
            "before re-activating.\n"
        )
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert (record.canonical_domain, record.access_policy, record.active) == (
            NEW_BAERUM_DOMAIN,
            None,
            False,
        )
        assert self._events(root) == []

    def test_a_registry_write_failure_does_not_record_a_change_that_never_landed(
        self, root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The event is evidence of a completed withdrawal, so it must not be
        appended when current state could not be changed. This pins the safe
        side of the command's registry-first ordering."""
        _activate(root)
        append = Mock()
        monkeypatch.setattr(
            observatory_commands,
            "write_registry",
            Mock(side_effect=OSError("registry unavailable")),
        )
        monkeypatch.setattr(observatory_commands, "append_source_event", append)

        result = self._replace()

        assert result.exit_code == 1
        append.assert_not_called()
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert (record.canonical_domain, record.active) == (BAERUM_DOMAIN, True)
        assert self._events(root) == []

    def test_a_domain_typed_with_stray_whitespace_reaches_both_records_the_same(
        self, root: Path
    ) -> None:
        """The registry and the event must not disagree about where the source
        moved to; the argument is normalised once, before either is built."""
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                f"  {NEW_BAERUM_DOMAIN}  ",
                "--reason",
                "baerum.kommune.no redirects to baerum.no",
                "--by",
                "Bartosz Kobyliński",
            ],
        )

        assert result.exit_code == 0, result.output
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert record.canonical_domain == NEW_BAERUM_DOMAIN
        assert self._events(root)[0].to_domain == NEW_BAERUM_DOMAIN

    def test_a_blank_reason_is_refused_like_an_empty_one(self, root: Path) -> None:
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                NEW_BAERUM_DOMAIN,
                "--reason",
                "   ",
                "--by",
                "Bartosz Kobyliński",
            ],
        )

        assert result.exit_code == 1
        assert "refusing to record this replacement" in result.stderr
        assert read_registry(root / "sources.json").sources[BAERUM_ID].active is True
        assert self._events(root) == []

    def test_a_blank_destination_is_refused_before_anything_is_written(self, root: Path) -> None:
        _activate(root)

        result = runner.invoke(
            app,
            [
                "observatory",
                "replace-source-domain",
                "--id",
                BAERUM_ID,
                "--domain",
                " \t ",
                "--reason",
                "baerum.kommune.no redirects to baerum.no",
                "--by",
                "Bartosz Kobyliński",
            ],
        )

        assert result.exit_code == 1
        assert "Refused:" in result.stderr
        record = read_registry(root / "sources.json").sources[BAERUM_ID]
        assert (record.canonical_domain, record.active) == (BAERUM_DOMAIN, True)
        assert self._events(root) == []

    def test_the_operator_is_told_the_one_step_that_restores_capture(self, root: Path) -> None:
        """The report is half of what this command does. A migration leaves the
        source unfetchable on purpose, so an operator who is not told which
        record was replaced, or what re-activates it, has been handed a broken
        source and no way back."""
        _activate(root)

        result = self._replace()

        fingerprint = self._events(root)[0].previous_record_sha256
        assert result.output == (
            f"{BAERUM_ID} (Bærum): {BAERUM_DOMAIN} -> {NEW_BAERUM_DOMAIN}\n"
            "  clearance withdrawn; the source is inactive and will not be swept\n"
            f"  replaced record {fingerprint[:12]} recorded in the event log\n"
            f"Next: review {NEW_BAERUM_DOMAIN} and run\n"
            f"  lovspor observatory activate-source --id {BAERUM_ID} --check <check>.json\n"
        )

    def test_the_reported_fingerprint_identifies_the_record_that_was_replaced(
        self, root: Path
    ) -> None:
        """Twelve hex digits, and they must be the ones an operator can match
        against the event log — a prefix of some other hash would be worse than
        printing nothing."""
        _activate(root)
        before = read_registry(root / "sources.json").sources[BAERUM_ID]

        result = self._replace()

        assert f"  replaced record {record_fingerprint(before)[:12]} " in result.output
