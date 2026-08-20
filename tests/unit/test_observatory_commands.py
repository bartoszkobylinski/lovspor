"""Registering and activating capture sources through the CLI.

Nothing is mocked. The commands resolve their registry path the way an
operator's shell does — through ``LOVSPOR_OBSERVATORY_ROOT`` and the ADR-0010
§5 boundary check — because the discovery order *is* the behaviour under test:
a registry path handed in by a fixture would prove nothing about where the
real one lands.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.observatory.commands import _entry_points
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, RetrievalProvenance, Tombstone
from lovspor.observatory.registry import read_registry
from lovspor.observatory.storage import (
    ENV_CORPUS_ROOT,
    ENV_OBSERVATORY_ROOT,
    ObservatoryRoot,
    engine_root,
)

runner = CliRunner()

BAERUM_ID = "3201"
BAERUM_DOMAIN = "baerum.kommune.no"
ROBOTS_URL = f"https://www.{BAERUM_DOMAIN}/robots.txt"
USER_AGENT = "lovspor-observatory/0.1 (+https://lovspor.no/observatory)"


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


class TestVerify:
    """The audit an operator runs after an interrupted run. Its whole value is
    that it answers "how bad is it?" precisely when the archive is damaged."""

    def test_an_intact_archive_passes(self, root: Path) -> None:
        _archive(root)

        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 0, result.output
        assert "records read: 1" in result.output
        assert "snapshot ok" in result.output

    def test_an_empty_archive_passes(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "verify"])

        assert result.exit_code == 0, result.output
        assert "records read: 0" in result.output

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
        record = SimpleNamespace(access_policy=SimpleNamespace(robots_txt_url=ROBOTS_URL))

        starts = _entry_points(fetcher, record, None)

        assert starts.urls == (SITEMAP_URL, NESTED_SITEMAP_URL)
        assert starts.probed is False
        fetcher.declared_sitemaps.assert_called_once_with(ROBOTS_URL)


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
        assert "records read: 1" in verify.output
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

    def test_no_activated_sources_is_a_refusal(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert "no activated sources" in result.stderr

    def test_a_damaged_log_refuses_before_any_request(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _activate(root)
        log = ObservationLog(ObservatoryRoot(root, forbidden=[]))
        log.log_path.parent.mkdir(parents=True, exist_ok=True)
        log.log_path.write_text('{"torn', encoding="utf-8")

        result = runner.invoke(app, ["observatory", "capture-all"])

        assert result.exit_code == 1
        assert "log is damaged" in result.stderr
        assert httpx_mock.get_requests() == []


OTHER_PAGE_URL = f"https://www.{BAERUM_DOMAIN}/tjenester/forskrift-om-avfallssug"
THIRD_PAGE_URL = f"https://www.{BAERUM_DOMAIN}/tjenester/forskrift-om-vann"


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
        assert "records read: 2" in runner.invoke(app, ["observatory", "verify"]).output

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
