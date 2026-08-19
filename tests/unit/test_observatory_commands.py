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

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
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
    *, robots_allows: bool = True, terms_reviewed: bool = True, permits: bool = True
) -> dict[str, object]:
    return {
        "checked_at": "2026-08-18T17:00:00Z",
        "robots_txt_url": ROBOTS_URL,
        "robots_allows": robots_allows,
        "terms_reviewed": terms_reviewed,
        "terms_permit_capture": permits,
        "rate_limit_seconds": 7.0,
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
            "and the fetch it describes was never recorded. Dropping that one line recovers "
            "the log; back it up first, it is evidence." in result.output
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
