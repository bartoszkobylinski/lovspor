"""The offline document report over the observatory's stored blobs (issue #332).

Every archive here is real: blobs written through ``append_artifact`` into the
sharded store under ``tmp_path``, records in ``observations.jsonl`` — the same
on-disk shape capture leaves behind. Nothing is fetched.
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.observatory.document_report import (
    DOCUMENT_TEXT_THRESHOLD,
    DocumentReport,
    SourceDocuments,
    blob_form,
    build_report,
    measure_html,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    RetrievalProvenance,
    Tombstone,
)
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot

runner = CliRunner()

OBSERVED_AT = datetime(2026, 9, 16, 6, 30, tzinfo=UTC)
LONG_PROSE = "Forskrift om renovasjon. " * 20
SHELL = b"<html><body><main><div id='app'></div></main><script>render()</script></body></html>"


def _provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="generic-html",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=2.0,
    )


def _artifact(
    payload: bytes, authority: str, content_type: str = "text/html"
) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id=authority,
        url=f"https://{authority}.invalid/{hashlib.sha256(payload).hexdigest()[:8]}",
        observed_at=OBSERVED_AT,
        provenance=_provenance(),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type=content_type,
        http_status=200,
    )


def _page(main: str) -> bytes:
    return f"<html><body><nav>Meny</nav><main>{main}</main></body></html>".encode()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return observatory


def _log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _store(
    log: ObservationLog, payload: bytes, authority: str, content_type: str = "text/html"
) -> None:
    log.append_artifact(_artifact(payload, authority, content_type), payload)


class TestMeasureHtml:
    def test_the_text_of_main_is_counted_and_the_rest_of_the_page_is_not(self) -> None:
        measured = measure_html(_page("Forskrift"))

        assert measured.text_chars == len("Forskrift")
        assert measured.region == "main"

    def test_a_section_sign_inside_main_is_found(self) -> None:
        assert measure_html(_page("§ 1 Formål")).has_section_sign is True

    def test_a_section_sign_outside_main_does_not_count(self) -> None:
        payload = b"<html><body><nav>\xc2\xa7 lenke</nav><main>Tekst</main></body></html>"

        assert measure_html(payload).has_section_sign is False

    def test_script_and_style_carry_no_document_text(self) -> None:
        measured = measure_html(SHELL)

        assert measured.text_chars == 0
        assert measured.has_section_sign is False

    def test_style_noscript_and_template_are_dropped_but_text_after_them_is_kept(self) -> None:
        payload = _page(
            "<style>p{}</style><noscript>Slå på JS</noscript><template>x</template>Rest"
        )

        assert measure_html(payload).text_chars == len("Rest")

    def test_whitespace_runs_count_as_one_character(self) -> None:
        payload = _page("  Første\n\n\t  andre  ")

        assert measure_html(payload).text_chars == len("Første andre")

    def test_without_main_the_body_is_measured_and_says_so(self) -> None:
        measured = measure_html(b"<html><body><p>Kapittel 1</p></body></html>")

        assert measured.text_chars == len("Kapittel 1")
        assert measured.region == "body"

    def test_empty_bytes_measure_as_no_text_rather_than_failing(self) -> None:
        measured = measure_html(b"")

        assert measured.text_chars == 0
        assert measured.region == "none"

    def test_a_frameset_page_has_no_region_and_its_title_is_not_text(self) -> None:
        payload = (
            b"<html><head><title>Forskrift</title></head><frameset><frame src=a></frameset></html>"
        )

        measured = measure_html(payload)

        assert measured.text_chars == 0
        assert measured.region == "none"

    def test_a_declared_charset_is_honoured(self) -> None:
        payload = (
            b'<html><head><meta charset="iso-8859-1"></head><body><main>\xa7 2</main></body></html>'
        )

        measured = measure_html(payload)

        assert measured.has_section_sign is True
        assert measured.text_chars == len("§ 2")

    def test_an_undeclared_utf8_page_is_read_as_utf8(self) -> None:
        assert measure_html(_page("blåbær §")).text_chars == len("blåbær §")

    def test_the_header_charset_wins_over_a_utf8_guess(self) -> None:
        """Valid UTF-8 read as the Latin-1 the server declared: two characters."""
        measured = measure_html(_page("Æ"), "text/html; charset=ISO-8859-1")

        assert measured.text_chars == len("Ã\x86")

    def test_a_quoted_header_charset_is_understood(self) -> None:
        measured = measure_html(_page("Æ"), 'text/html; Charset="iso-8859-1"')

        assert measured.text_chars == len("Ã\x86")

    def test_a_charset_parameter_without_a_value_is_ignored(self) -> None:
        assert measure_html(_page("Æ"), "text/html; charset=").text_chars == 1

    def test_an_unknown_header_charset_falls_back_to_utf8(self) -> None:
        measured = measure_html(_page("§ 5"), "text/html; charset=no-such-codec")

        assert measured.text_chars == len("§ 5")
        assert measured.has_section_sign is True

    def test_a_page_with_an_xml_declaration_is_parsed_from_its_bytes(self) -> None:
        payload = b'<?xml version="1.0" encoding="utf-8"?>' + _page("Første §")

        measured = measure_html(payload, "application/xhtml+xml")

        assert measured.text_chars == len("Første §")
        assert measured.region == "main"


class TestBlobForm:
    @pytest.mark.parametrize(
        ("content_type", "form"),
        [
            ("text/html", "html"),
            ("text/html; charset=utf-8", "html"),
            ("TEXT/HTML", "html"),
            ("application/xhtml+xml", "html"),
            ("application/pdf", "pdf"),
            ("application/pdf; name=x.pdf", "pdf"),
            ("image/png", "other"),
            ("application/octet-stream", "other"),
        ],
    )
    def test_the_form_follows_the_recorded_media_type(self, content_type: str, form: str) -> None:
        assert blob_form(content_type) == form


class TestSourceDocuments:
    def test_an_empty_source_has_no_median_rather_than_zero(self) -> None:
        assert SourceDocuments().median_html_chars is None

    def test_the_median_is_over_html_blobs(self) -> None:
        source = SourceDocuments(html_chars=[10, 500, 200])

        assert source.median_html_chars == 200

    def test_short_blobs_are_those_under_the_threshold(self) -> None:
        source = SourceDocuments(
            html_chars=[DOCUMENT_TEXT_THRESHOLD - 1, DOCUMENT_TEXT_THRESHOLD, 5]
        )

        assert source.html_under_threshold == 2


class TestBuildReport:
    def test_each_source_is_measured_on_its_own_blobs(self, root: Path) -> None:
        log = _log(root)
        _store(log, _page("§ 1 " + LONG_PROSE), "0301")
        _store(log, SHELL, "0301")
        _store(log, SHELL, "1101")

        report = build_report(log)

        oslo, other = report.sources["0301"], report.sources["1101"]
        assert oslo.html_blobs == 2
        assert oslo.html_with_section_sign == 1
        assert oslo.html_documents == 1
        assert other.html_blobs == 1
        assert other.html_documents == 0

    def test_a_blob_observed_twice_by_one_source_is_measured_once(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        _store(log, SHELL, "0301")

        assert build_report(log).sources["0301"].html_blobs == 1

    def test_the_same_bytes_under_two_sources_count_for_each(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        _store(log, SHELL, "1101")

        report = build_report(log)

        assert report.sources["0301"].html_blobs == 1
        assert report.sources["1101"].html_blobs == 1

    def test_a_document_needs_both_the_text_and_the_section_sign(self, root: Path) -> None:
        log = _log(root)
        _store(log, _page(LONG_PROSE), "0301")
        _store(log, _page("§ 3"), "0301")

        source = build_report(log).sources["0301"]

        assert source.html_with_section_sign == 1
        assert source.html_documents == 0

    def test_pdfs_are_counted_and_never_measured(self, root: Path) -> None:
        log = _log(root)
        _store(log, b"%PDF-1.7 \xa7 forskrift", "0301", "application/pdf")
        _store(log, b"\x89PNG", "0301", "image/png")

        source = build_report(log).sources["0301"]

        assert source.pdf_blobs == 1
        assert source.other_blobs == 1
        assert source.html_blobs == 0

    def test_a_blob_gone_from_disk_is_counted_as_unreadable(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        log.blob_path(hashlib.sha256(SHELL).hexdigest()).unlink()

        source = build_report(log).sources["0301"]

        assert source.unreadable_blobs == 1
        assert source.html_blobs == 0

    def test_failures_and_tombstones_are_not_blobs(self, root: Path) -> None:
        log = _log(root)
        log.append(
            FetchFailure(
                authority_id="0301",
                url="https://0301.invalid/x",
                observed_at=OBSERVED_AT,
                provenance=_provenance(),
                outcome="http_404",
                http_status=404,
            )
        )
        log.append(
            Tombstone(
                sha256="0" * 64, removed_at=OBSERVED_AT, basis="privacy", authorised_by="owner"
            )
        )

        assert build_report(log) == DocumentReport()

    def test_a_damaged_log_is_reported_rather_than_read_past(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')

        assert build_report(log).complete is False


class TestCommand:
    def test_the_report_names_each_source_with_its_measurements(self, root: Path) -> None:
        log = _log(root)
        _store(log, _page("§ 1 " + LONG_PROSE), "0301")
        _store(log, SHELL, "0301")
        _store(log, b"%PDF-1.7", "1101", "application/pdf")

        result = runner.invoke(app, ["observatory", "document-report"])

        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert any(line.split()[:1] == ["0301"] and "1/2" in line for line in lines)
        assert any(line.split()[:1] == ["1101"] for line in lines)
        assert "pdf blobs are counted, not measured" in result.output

    def test_the_totals_line_sums_every_source(self, root: Path) -> None:
        log = _log(root)
        _store(log, _page("§ 1 " + LONG_PROSE), "0301")
        _store(log, SHELL, "1101")

        result = runner.invoke(app, ["observatory", "document-report"])

        total = next(line for line in result.output.splitlines() if line.startswith("total"))
        assert "1/2" in total

    def test_an_empty_archive_reports_no_sources(self, root: Path) -> None:
        result = runner.invoke(app, ["observatory", "document-report"])

        assert result.exit_code == 0, result.output
        assert "sources: 0" in result.output

    def test_a_damaged_log_is_refused(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        with log.log_path.open("ab") as handle:
            handle.write(b'{"kind":"artifact","authority_id":"32')

        result = runner.invoke(app, ["observatory", "document-report"])

        assert result.exit_code == 1
        assert "log is damaged" in result.stderr

    def test_the_command_writes_nothing_into_the_archive(self, root: Path) -> None:
        log = _log(root)
        _store(log, SHELL, "0301")
        before = sorted((path, path.read_bytes()) for path in root.rglob("*") if path.is_file())

        runner.invoke(app, ["observatory", "document-report"])

        after = sorted((path, path.read_bytes()) for path in root.rglob("*") if path.is_file())
        assert after == before
