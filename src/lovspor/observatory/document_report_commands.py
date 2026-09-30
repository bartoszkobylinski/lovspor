"""The ``observatory document-report`` command (issue #332, option c).

Its own module rather than a share of :mod:`lovspor.observatory.commands`,
which is at its size ratchet; :mod:`lovspor.observatory.entrypoint` imports it
for the registration side effect. The decorated command only calls
:func:`document_report_impl`, so the body stays inside the mutation gate
(#292: mutmut skips decorated functions).

Read-only and offline: it reads the log and the blob store and writes nothing.
"""

import typer

from lovspor.observatory.app import observatory_app
from lovspor.observatory.document_report import (
    DOCUMENT_TEXT_THRESHOLD,
    SourceDocuments,
    build_report,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.registry_io import _root

_HEADER = (
    f"{'source':10} {'html':>6} {'median':>7} {'<' + str(DOCUMENT_TEXT_THRESHOLD):>6} "
    f"{'§':>6} {'no-main':>7} {'docs/html':>11} {'pdf':>6} {'other':>6} {'missing':>7}"
)


def _row(name: str, source: SourceDocuments) -> str:
    median = source.median_html_chars
    shown = "-" if median is None else f"{median:.0f}"
    documents = f"{source.html_documents}/{source.html_blobs}"
    return (
        f"{name:10} {source.html_blobs:>6} {shown:>7} {source.html_under_threshold:>6} "
        f"{source.html_with_section_sign:>6} {source.html_without_main:>7} {documents:>11} "
        f"{source.pdf_blobs:>6} {source.other_blobs:>6} {source.unreadable_blobs:>7}"
    )


def _legend() -> None:
    typer.echo(
        f"\ndocs = HTML blobs with at least {DOCUMENT_TEXT_THRESHOLD} characters of <main> "
        "text and a §.\nA proxy for 'carries a document', not for 'is a forskrift' "
        "(ADR-0010 defers classification).\npdf blobs are counted, not measured: "
        "the engine has no PDF text extractor.\nno-main = measured from <body> because "
        "the page has no <main>. missing = blob not on disk."
    )


def document_report_impl() -> None:
    """Print the per-source table, or refuse a log that does not read to its end."""
    report = build_report(ObservationLog(_root()))
    if not report.complete:
        typer.echo(
            "Refused: the observation log is damaged. Run `observatory verify` first.", err=True
        )
        raise typer.Exit(1)
    typer.echo(f"sources: {len(report.sources)}")
    typer.echo(_HEADER)
    for name in sorted(report.sources):
        typer.echo(_row(name, report.sources[name]))
    typer.echo(_row("total", report.total()))
    _legend()


@observatory_app.command("document-report")
def document_report() -> None:
    """Which stored blobs carry a document: text length and § per source, offline.

    Reads every distinct blob the log names, once per source, and measures the
    visible text of HTML pages. No request is made and nothing is written; the
    observation schema is unchanged (issue #332).
    """
    document_report_impl()
