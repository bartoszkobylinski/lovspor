"""The `observatory survey` command (issue #349).

What a run must leave behind is the whole point. The 2026-08-20 sweep over all
358 municipalities produced the figures still quoted in `commands.py:302` and
persisted nothing, so its population cannot be re-derived. A survey that only
printed a table would repeat that.
"""

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.observatory.survey import RobotsReadout, read_site_shape
from lovspor.observatory.survey_commands import (
    DEFAULT_DELAY_SECONDS,
    _domains,
    _run_name,
    _survey_path,
    _tally,
    _write,
)

runner = CliRunner()

DOMAIN = "example.invalid"
OTHER = "other.invalid"
SITEMAP_XML = (
    b'<?xml version="1.0"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.invalid/forskrift</loc></url></urlset>"
)


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return observatory


def _allow(httpx_mock: HTTPXMock, domain: str, body: str = "User-agent: *\nAllow: /\n") -> None:
    httpx_mock.add_response(url=f"https://{domain}/robots.txt", text=body)


def _rows(root: Path) -> list[dict[str, object]]:
    files = sorted((root / "survey").glob("*.jsonl"))
    assert len(files) == 1, f"expected exactly one survey log, found {files}"
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


class TestWhatTheRunLeavesBehind:
    def test_a_row_per_host_is_persisted_under_the_archive(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _allow(httpx_mock, DOMAIN)
        httpx_mock.add_response(url=f"https://{DOMAIN}/sitemap.xml", content=SITEMAP_XML)
        httpx_mock.add_response(url=f"https://{DOMAIN}/", content=b"<html></html>")

        result = runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0"])

        assert result.exit_code == 0
        assert _rows(root) == [
            {
                "domain": DOMAIN,
                "entry": "conventional_sitemap",
                "robots_readable": True,
                "robots_allows_root": True,
                "declared_sitemaps": [],
                "front_page_markers": [],
            }
        ]

    def test_the_run_is_named_so_two_surveys_do_not_overwrite_each_other(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        runner.invoke(
            app,
            ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", "first"],
        )

        assert (root / "survey" / "first.jsonl").exists()

    def test_a_run_name_cannot_move_the_log_out_of_the_survey_directory(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A run id names a file. It is not a path and may not act like one.

        Found by the Codex test author on PR #353: interpolating `--run-id`
        straight into a path wrote `<root>/survey/../escaped.jsonl`, and a longer
        climb left the archive altogether — the ADR-0010 §5 boundary reached
        through an argument rather than through the env var the boundary type
        guards.
        """
        result = runner.invoke(
            app,
            ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", "../escaped"],
        )

        assert result.exit_code == 2
        assert list(root.glob("*.jsonl")) == []
        assert not (root / "survey").exists()

    @pytest.mark.parametrize(
        "run_id",
        ["../escaped", "../../../../tmp/escaped", "/absolute", "sub/dir", "..", ".hidden", ""],
    )
    def test_a_run_id_that_could_behave_like_a_path_is_refused(
        self, root: Path, run_id: str
    ) -> None:
        result = runner.invoke(
            app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", run_id]
        )

        assert result.exit_code == 2
        assert "plain file name" in result.output

    def test_the_refusal_costs_no_requests_because_it_happens_before_probing(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A bad argument must not be paid for in requests to 358 municipalities."""
        runner.invoke(
            app,
            ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", "../escaped"],
        )

        assert httpx_mock.get_requests() == []

    def test_an_ordinary_dated_name_is_accepted(self, root: Path, httpx_mock: HTTPXMock) -> None:
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app,
            [
                "observatory",
                "survey",
                "--domain",
                DOMAIN,
                "--delay",
                "0",
                "--run-id",
                "2026-09-19-all.v2_final",
            ],
        )

        assert result.exit_code == 0
        assert (root / "survey" / "2026-09-19-all.v2_final.jsonl").exists()

    def test_the_archive_boundary_is_enforced_not_assumed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No root, no survey — ADR-0010 §5 has no in-repo default."""
        monkeypatch.delenv(ENV_OBSERVATORY_ROOT, raising=False)

        result = runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN])

        assert result.exit_code == 1
        assert "observatory archive" in result.output


class TestTheLogOnDisk:
    """Properties of the written file, found by surviving mutants on PR #353."""

    def test_norwegian_characters_are_written_as_themselves_not_escaped(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A row a person greps for `kunngjøring` has to contain it.

        `ensure_ascii=True` would write `\\u00f8` — valid JSON that no reader of
        this archive would think to search for.
        """
        _allow(
            httpx_mock,
            DOMAIN,
            "User-agent: *\nAllow: /\nSitemap: https://example.invalid/kunngjøring.xml\n",
        )
        httpx_mock.add_response(url=f"https://{DOMAIN}/", content=b"<html></html>")

        runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0"])

        written = (root / "survey").glob("*.jsonl").__next__().read_text(encoding="utf-8")
        assert "kunngjøring.xml" in written
        assert "\\u00f8" not in written

    def test_a_second_survey_into_the_same_archive_is_not_an_error(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The survey directory outlives the first run that created it."""
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER, "User-agent: *\nDisallow: /\n")

        first = runner.invoke(
            app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", "one"]
        )
        second = runner.invoke(
            app, ["observatory", "survey", "--domain", OTHER, "--delay", "0", "--run-id", "two"]
        )

        assert (first.exit_code, second.exit_code) == (0, 0)
        assert sorted(p.name for p in (root / "survey").glob("*.jsonl")) == [
            "one.jsonl",
            "two.jsonl",
        ]

    def test_an_archive_root_that_does_not_exist_yet_is_created(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
    ) -> None:
        """First survey on a fresh disk: the root is a path, not a precondition."""
        absent = tmp_path / "fresh-archive"
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(absent))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0", "--run-id", "first"]
        )

        assert result.exit_code == 0
        assert (absent / "survey" / "first.jsonl").exists()

    def test_a_refusal_goes_to_stderr_so_a_piped_run_does_not_swallow_it(self, root: Path) -> None:
        """`survey ... > list.txt` must still show why nothing was surveyed."""
        result = runner.invoke(app, ["observatory", "survey"])

        assert "no domains" in result.stderr.lower()
        assert "no domains" not in result.stdout.lower()


class TestTheDefaultRunName:
    def test_it_is_a_sortable_utc_stamp(self, root: Path, httpx_mock: HTTPXMock) -> None:
        """Two surveys a day apart must sort in the order they ran."""
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0"])

        written = next((root / "survey").glob("*.jsonl"))
        assert re.fullmatch(r"\d{8}T\d{6}Z", written.stem), written.stem

    def test_the_stamp_is_utc_even_where_the_machine_is_not(
        self, root: Path, monkeypatch: pytest.MonkeyPatch, httpx_mock: HTTPXMock
    ) -> None:
        """The trailing Z is a claim. A local clock formatted as Z is a false one."""
        monkeypatch.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        before = datetime.now(UTC)
        runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0"])
        after = datetime.now(UTC)

        written = next((root / "survey").glob("*.jsonl"))
        minute = "%Y%m%dT%H%M"
        assert written.stem[:13] in {before.strftime(minute), after.strftime(minute)}


class TestWhatItReports:
    def test_each_host_and_a_tally_by_entry_are_printed(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app,
            ["observatory", "survey", "--domain", DOMAIN, "--domain", OTHER, "--delay", "0"],
        )

        assert DOMAIN in result.output
        assert OTHER in result.output
        assert "robots_disallowed: 2" in result.output

    def test_the_tally_leads_with_the_biggest_group(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A planner reads the tally top-down; the largest population comes first."""
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER, "User-agent: *\nDisallow: /\n")
        third = "third.invalid"
        _allow(httpx_mock, third)
        httpx_mock.add_response(url=f"https://{third}/sitemap.xml", content=SITEMAP_XML)
        httpx_mock.add_response(url=f"https://{third}/", content=b"<html></html>")

        result = runner.invoke(
            app,
            [
                "observatory",
                "survey",
                "--domain",
                DOMAIN,
                "--domain",
                OTHER,
                "--domain",
                third,
                "--delay",
                "0",
            ],
        )

        tally = [
            line.strip() for line in result.output.splitlines() if ": " in line and "  " in line
        ]
        assert tally == ["robots_disallowed: 2", "conventional_sitemap: 1"]

    def test_groups_of_equal_size_are_tallied_alphabetically(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A tie has to break somewhere, and a stable order is a readable diff."""
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER)
        httpx_mock.add_response(url=f"https://{OTHER}/sitemap.xml", content=SITEMAP_XML)
        httpx_mock.add_response(url=f"https://{OTHER}/", content=b"<html></html>")

        result = runner.invoke(
            app,
            ["observatory", "survey", "--domain", DOMAIN, "--domain", OTHER, "--delay", "0"],
        )

        tally = [line.strip() for line in result.output.splitlines() if ": 1" in line]
        assert tally == ["conventional_sitemap: 1", "robots_disallowed: 1"]

    def test_the_written_row_is_the_same_under_either_pydantic_dump_mode(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """Assumption test for the `mode="json"` equivalence in mutation-equivalents.toml.

        SiteShape holds only strings, bools and tuples of strings, so pydantic's
        python mode and json mode serialise identically once json.dumps turns a
        tuple into an array. The equivalence registered for the `mode` argument
        rests on that and on nothing else, so a field whose two modes disagree —
        a datetime, a Path, an enum — must turn this test red rather than let a
        stale justification stand (CLAUDE.md, issue #132).
        """
        shape = read_site_shape(
            domain=DOMAIN,
            robots=RobotsReadout(
                readable=True, allows_root=True, declared_sitemaps=("https://x.invalid/s.xml",)
            ),
            conventional_sitemap=False,
            front_page=b'<html><script src="/api/presentation/x"></script></html>',
        )

        assert json.dumps(shape.model_dump(mode="json")) == json.dumps(
            shape.model_dump(mode="python")
        )

    def test_the_browser_assembled_count_is_the_one_a_planner_reads_first(
        self, root: Path, httpx_mock: HTTPXMock
    ) -> None:
        """It sizes the population issue #194 says needs a JSON transport."""
        _allow(httpx_mock, DOMAIN)
        httpx_mock.add_response(url=f"https://{DOMAIN}/sitemap.xml", status_code=404)
        httpx_mock.add_response(
            url=f"https://{DOMAIN}/", content=b'<html><script src="/api/presentation/x"></script>'
        )

        result = runner.invoke(app, ["observatory", "survey", "--domain", DOMAIN, "--delay", "0"])

        assert "browser_assembled: 1" in result.output


class TestWhereTheListComesFrom:
    def test_indented_comments_are_not_domains(self, tmp_path: Path) -> None:
        listing = tmp_path / "domains.txt"
        listing.write_text("   # operator note\nexample.invalid\n", encoding="utf-8")

        assert _domains(None, listing) == [DOMAIN]

    def test_domain_lists_are_explicitly_read_as_utf8(self) -> None:
        listing = Mock(spec=Path)
        listing.read_text.return_value = DOMAIN

        assert _domains(None, listing) == [DOMAIN]
        listing.read_text.assert_called_once_with(encoding="utf-8")

    def test_a_file_of_domains_is_accepted_because_358_do_not_fit_on_a_command_line(
        self, root: Path, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        listing = tmp_path / "domains.txt"
        listing.write_text(f"# recon list\n{DOMAIN}\n\n{OTHER}\n", encoding="utf-8")
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app, ["observatory", "survey", "--from", str(listing), "--delay", "0"]
        )

        assert result.exit_code == 0
        assert [row["domain"] for row in _rows(root)] == [DOMAIN, OTHER]

    def test_duplicates_across_arguments_and_the_file_are_probed_once(
        self, root: Path, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """The persisted population and request cost both use unique hosts."""
        listing = tmp_path / "domains.txt"
        listing.write_text(f"{DOMAIN}\n{OTHER}\n{DOMAIN}\n", encoding="utf-8")
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")
        _allow(httpx_mock, OTHER, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app,
            [
                "observatory",
                "survey",
                "--domain",
                DOMAIN,
                "--from",
                str(listing),
                "--delay",
                "0",
            ],
        )

        assert result.exit_code == 0
        assert [row["domain"] for row in _rows(root)] == [DOMAIN, OTHER]
        assert [str(request.url) for request in httpx_mock.get_requests()] == [
            f"https://{DOMAIN}/robots.txt",
            f"https://{OTHER}/robots.txt",
        ]

    def test_an_indented_comment_is_still_a_comment_and_not_a_hostname(
        self, root: Path, tmp_path: Path, httpx_mock: HTTPXMock
    ) -> None:
        """A hand-maintained list has indented notes under its groups."""
        listing = tmp_path / "domains.txt"
        listing.write_text(f"# ACOS\n    # blocked, see #194\n{DOMAIN}\n", encoding="utf-8")
        _allow(httpx_mock, DOMAIN, "User-agent: *\nDisallow: /\n")

        result = runner.invoke(
            app, ["observatory", "survey", "--from", str(listing), "--delay", "0"]
        )

        assert result.exit_code == 0
        assert [row["domain"] for row in _rows(root)] == [DOMAIN]

    def test_a_blank_and_commented_file_is_a_refusal_not_an_empty_survey(
        self, root: Path, tmp_path: Path
    ) -> None:
        """An empty run would write a log that reads as "358 hosts, none reachable"."""
        listing = tmp_path / "domains.txt"
        listing.write_text("# nothing here\n\n", encoding="utf-8")

        result = runner.invoke(
            app, ["observatory", "survey", "--from", str(listing), "--delay", "0"]
        )

        assert result.exit_code == 2
        assert "no domains" in result.output.lower()
        assert not (root / "survey").exists()

    def test_a_domain_with_a_scheme_is_refused_before_any_request(self, root: Path) -> None:
        """The help says "without scheme"; probing https://https://x/ would
        exit 0 after asking nothing that exists."""
        result = runner.invoke(
            app, ["observatory", "survey", "--domain", f"https://{DOMAIN}", "--delay", "0"]
        )

        assert result.exit_code == 2
        assert "scheme" in result.output.lower()
        assert not (root / "survey").exists()

    def test_a_listed_host_with_a_path_is_refused_the_same_way(
        self, root: Path, tmp_path: Path
    ) -> None:
        listing = tmp_path / "domains.txt"
        listing.write_text(f"{DOMAIN}/kunngjoringer\n", encoding="utf-8")

        result = runner.invoke(
            app, ["observatory", "survey", "--from", str(listing), "--delay", "0"]
        )

        assert result.exit_code == 2
        assert f"{DOMAIN}/kunngjoringer" in result.output

    def test_naming_no_host_at_all_is_refused(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "survey"])

        assert result.exit_code == 2
        assert "no domains" in result.output.lower()

    def test_a_missing_list_file_is_an_operator_mistake_not_a_traceback(
        self, root: Path, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app, ["observatory", "survey", "--from", str(tmp_path / "absent.txt")]
        )

        assert result.exit_code == 2
        assert "absent.txt" in result.output


class TestPoliteness:
    def test_the_delay_defaults_to_the_limit_every_source_was_cleared_with(self) -> None:
        """Passing --delay 0 everywhere else in this file must stay a test-only act."""
        assert DEFAULT_DELAY_SECONDS == 7.0


class TestSurveyHelpers:
    def test_default_run_name_is_a_utc_timestamp(self) -> None:
        instant = datetime(2026, 9, 20, 12, 34, 56, tzinfo=UTC)
        clock = Mock()
        clock.now.return_value = instant

        with patch("lovspor.observatory.survey_commands.datetime", clock):
            assert _run_name(None) == "20260920T123456Z"

        clock.now.assert_called_once_with(UTC)

    def test_invalid_run_name_explains_the_allowed_characters(self, root: Path) -> None:
        result = runner.invoke(
            app, ["observatory", "survey", "--domain", DOMAIN, "--run-id", "bad/name"]
        )

        assert result.exit_code == 2
        assert (
            "Letters, digits, dot, dash and underscore, starting with a letter or digit."
            in result.stderr
        )

    def test_survey_path_creates_missing_parents_and_is_idempotent(self, tmp_path: Path) -> None:
        root_path = tmp_path / "missing" / "archive"
        root = ObservatoryRoot(root_path, forbidden=[])

        expected = root_path / "survey" / "run.jsonl"
        assert _survey_path(root, "run") == expected
        assert _survey_path(root, "run") == expected

    def test_jsonl_is_utf8_and_keeps_non_ascii_text(self, tmp_path: Path) -> None:
        shape = read_site_shape(
            domain="ø.example",
            robots=RobotsReadout(readable=True, allows_root=False),
            conventional_sitemap=False,
            front_page=b"",
        )
        output = tmp_path / "survey.jsonl"

        _write(output, [shape])

        raw = output.read_bytes()
        assert b"\\u00f8" not in raw
        assert json.loads(raw.decode("utf-8"))["domain"] == "ø.example"

    def test_jsonl_file_is_explicitly_opened_as_utf8(self) -> None:
        path = Mock(spec=Path)
        path.open.return_value = MagicMock()
        handle = path.open.return_value.__enter__.return_value

        _write(path, [])

        path.open.assert_called_once_with("a", encoding="utf-8")
        handle.write.assert_not_called()

    def test_write_requests_json_compatible_model_values(self, tmp_path: Path) -> None:
        shape = Mock()
        shape.model_dump.return_value = {"domain": DOMAIN}

        _write(tmp_path / "survey.jsonl", [shape])

        shape.model_dump.assert_called_once_with(mode="json")

    def test_tally_is_sorted_by_count_then_entry_name(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        shapes = [Mock(entry="z"), Mock(entry="a"), Mock(entry="z"), Mock(entry="b")]

        _tally(shapes)

        assert capsys.readouterr().out.splitlines() == ["  z: 2", "  a: 1", "  b: 1"]

    def test_refusals_are_written_to_stderr(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "survey"])

        assert result.exit_code == 2
        assert "no domains" in result.stderr.lower()
        assert result.stdout == ""
