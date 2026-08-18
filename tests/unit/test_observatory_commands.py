"""Registering and activating capture sources through the CLI.

Nothing is mocked. The commands resolve their registry path the way an
operator's shell does — through ``LOVSPOR_OBSERVATORY_ROOT`` and the ADR-0010
§5 boundary check — because the discovery order *is* the behaviour under test:
a registry path handed in by a fixture would prove nothing about where the
real one lands.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.observatory.registry import read_registry
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, engine_root

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
        assert "Cannot locate the source registry" in result.stderr

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
        assert "Cannot locate the source registry" in result.stderr
