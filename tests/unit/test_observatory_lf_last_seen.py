"""The last-seen view of linked Lovdata local-regulation ids (issue #694).

The operator's question: which ids are no longer linked from any page that
once linked them, so a human can review them as candidate repeals? The view
is a function of the log alone, like the first-seen ledger, and it retracts
nothing. A failed fetch, a page served with an error status, or a capture
whose blob is gone is never evidence that a link went away.
"""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import LogIntegrityError
from lovspor.observatory.lf_last_seen import LastSeen, last_seen_report
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    FetchFailure,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    Tombstone,
    record_key,
    record_to_json_line,
)
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot

T0 = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
T1 = T0 + timedelta(days=1)
T2 = T0 + timedelta(days=2)
T3 = T0 + timedelta(days=3)
LF_A = "https://lovdata.no/dokument/LF/forskrift/2020-11-19-2630"
LF_B = "https://lovdata.no/dokument/LTII/forskrift/2026-02-26-329"
PAGE = "https://kommune.example.invalid/forskrifter"
OTHER_PAGE = "https://kommune.example.invalid/lokale-forskrifter"
KEY_A = ("3201", "LF", "2020-11-19-2630")
KEY_B = ("3201", "LTII", "2026-02-26-329")

_PROVENANCE = RetrievalProvenance(
    adapter="http",
    channel="http",
    discovery_method="sitemap",
    user_agent="test-agent",
    rate_limit_seconds=1.0,
)


def make_log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _page(*hrefs: str, salt: str = "") -> bytes:
    links = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html><!--{salt}--><body>{links}</body></html>".encode()


def _capture(log: ObservationLog, payload: bytes, **fields: object) -> ArtifactObservation:
    values: dict[str, object] = {
        "authority_id": "3201",
        "url": PAGE,
        "observed_at": T0,
        "content_type": "text/html; charset=utf-8",
        "http_status": 200,
    }
    values.update(fields)
    record = ArtifactObservation(
        provenance=_PROVENANCE,
        sha256=hashlib.sha256(payload).hexdigest(),
        **values,  # type: ignore[arg-type]
    )
    log.append_artifact(record, payload)
    return record


def _failure(log: ObservationLog, observed_at: datetime, outcome: str = "timeout") -> None:
    log.append(
        FetchFailure(
            authority_id="3201",
            url=PAGE,
            observed_at=observed_at,
            provenance=_PROVENANCE,
            outcome=outcome,
        )
    )


def _by_key(entries: tuple[LastSeen, ...]) -> dict[tuple[str, str, str], LastSeen]:
    return {(e.authority_id, e.kind, e.lf_id): e for e in entries}


class TestStillLinked:
    def test_a_page_whose_latest_capture_links_the_id_keeps_it_linked(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A, LF_B))
        _capture(log, _page(LF_A, LF_B, salt="later"), observed_at=T1)

        report = last_seen_report(log)

        seen = _by_key(report.entries)[KEY_A]
        assert (seen.first_seen, seen.last_seen_linked, seen.last_url) == (T0, T1, PAGE)
        assert (seen.status, seen.unlinked_since, seen.evidence_at) == ("linked", None, T1)
        assert (seen.pages, seen.pages_failing) == (1, 0)
        assert sorted(_by_key(report.entries)) == [KEY_A, KEY_B]
        assert (report.html_records, report.blobs_read, report.blobs_missing) == (2, 2, 0)

    @pytest.mark.parametrize("outcome", ["timeout", "http_404", "connection_error"])
    def test_a_failed_fetch_after_the_link_is_no_evidence(
        self, tmp_path: Path, outcome: str
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _failure(log, T1, outcome)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.last_seen_linked, seen.evidence_at) == ("linked", T0, T0)
        assert (seen.pages, seen.pages_failing) == (1, 1)

    def test_a_followed_redirect_is_not_a_failing_page(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _failure(log, T1, "redirect_followed")

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.pages_failing) == ("linked", 0)

    def test_an_error_page_or_a_non_html_answer_is_no_evidence(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(salt="error"), observed_at=T1, http_status=503)
        _capture(log, b"%PDF-1.4", observed_at=T2, content_type="application/pdf")

        report = last_seen_report(log)
        seen = _by_key(report.entries)[KEY_A]

        assert (seen.status, seen.evidence_at, seen.pages_failing) == ("linked", T0, 1)
        assert report.html_records == 1

    def test_a_capture_whose_blob_is_gone_is_no_evidence(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        later = _capture(log, _page(salt="dropped"), observed_at=T1)
        log.append(
            Tombstone(
                sha256=later.sha256,
                removed_at=T2,
                basis="privacy",
                authorised_by="owner",
            )
        )
        log.blob_path(later.sha256).unlink()

        report = last_seen_report(log)
        seen = _by_key(report.entries)[KEY_A]

        assert (seen.status, seen.evidence_at) == ("linked", T0)
        assert seen.pages_failing == 1
        assert report.blobs_missing == 1

    def test_one_page_still_linking_outweighs_another_that_dropped_it(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(LF_A, salt="other"), url=OTHER_PAGE)
        _capture(log, _page(salt="dropped"), observed_at=T1)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.last_seen_linked, seen.last_url) == ("linked", T0, OTHER_PAGE)
        assert (seen.pages, seen.evidence_at) == (2, T1)

    def test_an_id_linked_again_after_a_gap_is_linked_from_its_latest_sighting(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(salt="gap"), observed_at=T1)
        _capture(log, _page(LF_A, salt="back"), observed_at=T2)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.first_seen, seen.last_seen_linked) == ("linked", T0, T2)
        assert seen.unlinked_since is None


class TestUnlinked:
    def test_a_second_drop_uses_the_drop_after_the_latest_link(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(salt="first drop"), observed_at=T1)
        _capture(log, _page(LF_A, salt="returned"), observed_at=T2)
        _capture(log, _page(salt="second drop"), observed_at=T3)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.first_seen, seen.last_seen_linked) == (T0, T2)
        assert (seen.status, seen.unlinked_since, seen.evidence_at) == ("unlinked", T3, T3)

    @pytest.mark.parametrize("reverse", [False, True])
    def test_a_capture_at_the_same_time_is_not_a_later_capture(
        self, tmp_path: Path, reverse: bool
    ) -> None:
        log = make_log(tmp_path)
        payloads = [_page(LF_A), _page(salt="without link")]
        for payload in reversed(payloads) if reverse else payloads:
            _capture(log, payload)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.unlinked_since, seen.evidence_at) == ("linked", None, T0)

    def test_the_same_url_under_another_authority_cannot_drop_a_link(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(LF_A), authority_id="3203")
        _capture(log, _page(salt="dropped"), authority_id="3203", observed_at=T1)

        report = last_seen_report(log)
        seen = _by_key(report.entries)

        assert [entry.key for entry in report.entries] == [KEY_A, ("3203", *KEY_A[1:])]
        assert (seen[KEY_A].status, seen[KEY_A].evidence_at) == ("linked", T0)
        other = seen[("3203", *KEY_A[1:])]
        assert (other.status, other.unlinked_since, other.pages) == ("unlinked", T1, 1)
        assert (report.html_records, report.blobs_read) == (3, 2)

    @pytest.mark.parametrize("late_failure", [False, True])
    def test_readable_recovery_clears_failure_regardless_of_append_order(
        self, tmp_path: Path, late_failure: bool
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        if not late_failure:
            _failure(log, T1)
        _capture(log, _page(salt="recovered without link"), observed_at=T2)
        if late_failure:
            _failure(log, T1)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.unlinked_since, seen.evidence_at) == ("unlinked", T2, T2)
        assert seen.pages_failing == 0

    def test_a_later_readable_capture_without_the_link_makes_a_candidate(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A, LF_B))
        _capture(log, _page(LF_B, salt="without a"), observed_at=T1)
        _capture(log, _page(LF_B, salt="still without a"), observed_at=T2)

        report = last_seen_report(log)
        seen = _by_key(report.entries)

        assert (seen[KEY_A].status, seen[KEY_A].unlinked_since) == ("unlinked", T1)
        assert (seen[KEY_A].last_seen_linked, seen[KEY_A].evidence_at) == (T0, T2)
        assert seen[KEY_B].status == "linked"

    def test_unlinked_since_is_when_the_last_page_dropped_it(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(LF_A, salt="other"), url=OTHER_PAGE)
        _capture(log, _page(salt="dropped"), observed_at=T1)
        _capture(log, _page(salt="other dropped"), url=OTHER_PAGE, observed_at=T3)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.unlinked_since, seen.pages) == ("unlinked", T3, 2)

    def test_the_log_order_does_not_decide_the_verdict(self, tmp_path: Path) -> None:
        # A re-filed or late-appended older capture must not read as the latest.
        log = make_log(tmp_path)
        _capture(log, _page(salt="dropped"), observed_at=T1)
        _capture(log, _page(LF_A))

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.unlinked_since) == ("unlinked", T1)

    def test_a_failing_page_after_the_drop_keeps_the_candidate(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        _capture(log, _page(salt="dropped"), observed_at=T1)
        _failure(log, T2)

        seen = _by_key(last_seen_report(log).entries)[KEY_A]

        assert (seen.status, seen.unlinked_since, seen.pages_failing) == ("unlinked", T1, 1)


class TestTheLogIsTheOnlySource:
    def test_a_damaged_log_is_refused(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("{torn")

        clean_through = log.log_path.stat().st_size - len("{torn")
        with pytest.raises(LogIntegrityError) as caught:
            last_seen_report(log)
        assert str(caught.value) == (
            f"{log.log_path}: unreadable record after byte {clean_through}; "
            "the last-seen view is not built from a damaged log (run `observatory verify`)"
        )

    def test_an_empty_log_is_an_empty_report(self, tmp_path: Path) -> None:
        report = last_seen_report(make_log(tmp_path))
        assert report.entries == ()
        assert (report.html_records, report.blobs_read) == (0, 0)


class TestTheCommand:
    def _run(self, root: Path, *args: str) -> tuple[int, str, str]:
        runner = CliRunner()
        env = {ENV_OBSERVATORY_ROOT: str(root), ENV_CORPUS_ROOT: None}
        result = runner.invoke(app, ["observatory", "lf-last-seen", *args], env=env)
        return result.exit_code, result.stdout, result.stderr

    def test_rows_are_tab_separated_and_the_counts_go_to_stderr(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A, LF_B))
        _capture(log, _page(LF_B, salt="without a"), observed_at=T1)

        code, out, err = self._run(tmp_path)

        assert code == 0, err
        rows = [line.split("\t") for line in out.splitlines()]
        assert rows == [
            [
                "3201",
                "LF",
                "2020-11-19-2630",
                T0.isoformat(),
                T0.isoformat(),
                "unlinked",
                T1.isoformat(),
                T1.isoformat(),
                "1",
                "0",
                PAGE,
            ],
            [
                "3201",
                "LTII",
                "2026-02-26-329",
                T0.isoformat(),
                T1.isoformat(),
                "linked",
                "-",
                T1.isoformat(),
                "1",
                "0",
                PAGE,
            ],
        ]
        assert "2 ids: 1 unlinked (candidate repeals for review), 1 linked" in err
        assert "2 html records (2 blobs, 0 missing)" in err

    def test_unlinked_lists_the_candidates_alone(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A, LF_B))
        _capture(log, _page(LF_B, salt="without a"), observed_at=T1)

        code, out, err = self._run(tmp_path, "--unlinked")

        assert code == 0, err
        assert [line.split("\t")[2] for line in out.splitlines()] == ["2020-11-19-2630"]
        assert "1 unlinked" in err

    @pytest.mark.parametrize("drop", [False, True])
    def test_summary_counts_a_single_id(self, tmp_path: Path, drop: bool) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        if drop:
            _capture(log, _page(salt="dropped"), observed_at=T1)

        code, out, err = self._run(tmp_path)

        assert code == 0, err
        assert len(out.splitlines()) == 1
        assert (
            f"1 ids: {int(drop)} unlinked (candidate repeals for review), {int(not drop)} linked;"
        ) in err

    def test_a_damaged_log_is_refused_with_exit_1(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        with log.log_path.open("a", encoding="utf-8") as handle:
            handle.write("{torn")

        code, out, err = self._run(tmp_path)

        assert (code, out) == (1, "")
        assert "Refused:" in err


@pytest.mark.parametrize("failure_at", [T0, T1])
@pytest.mark.parametrize("repeated_outcome", ["timeout", "http_404", "connection_error"])
def test_repeated_equal_time_failures_preserve_the_page_verdict(
    tmp_path: Path, failure_at: datetime, repeated_outcome: str
) -> None:
    log = make_log(tmp_path)
    _capture(log, _page(LF_A))
    _failure(log, failure_at, "timeout")
    before = last_seen_report(log)
    _failure(log, failure_at, repeated_outcome)

    after = last_seen_report(log)
    assert after == before
    seen = _by_key(after.entries)[KEY_A]

    assert (seen.status, seen.evidence_at) == ("linked", T0)
    assert seen.pages_failing == int(failure_at > T0)

    _capture(log, _page(LF_A, salt="recovered"), observed_at=T2)
    recovered = _by_key(last_seen_report(log).entries)[KEY_A]
    assert (recovered.status, recovered.evidence_at, recovered.pages_failing) == ("linked", T2, 0)


def test_missing_blob_count_counts_distinct_blobs_not_captures(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    _capture(log, _page(LF_A))
    missing_payloads = [_page(salt="missing one"), _page(salt="missing two")]
    for payload in missing_payloads:
        record = _capture(log, payload, observed_at=T1)
        log.blob_path(record.sha256).unlink()
    # Repeated observations of a missing blob use the same cached absence.
    log.append(record.model_copy(update={"observed_at": T2}))

    report = last_seen_report(log)

    assert (report.html_records, report.blobs_read, report.blobs_missing) == (4, 1, 2)
    seen = _by_key(report.entries)[KEY_A]
    assert (seen.status, seen.evidence_at, seen.pages_failing) == ("linked", T0, 1)


@pytest.mark.parametrize("status", [199, 200, 299, 300])
def test_only_2xx_html_can_establish_that_a_link_disappeared(tmp_path: Path, status: int) -> None:
    log = make_log(tmp_path)
    _capture(log, _page(LF_A))
    _capture(log, _page(salt="no link"), observed_at=T1, http_status=status)

    report = last_seen_report(log)
    seen = _by_key(report.entries)[KEY_A]
    readable = 200 <= status < 300
    assert seen.status == ("unlinked" if readable else "linked")
    assert seen.unlinked_since == (T1 if readable else None)
    assert seen.evidence_at == (T1 if readable else T0)
    assert seen.pages_failing == int(not readable)
    assert (report.html_records, report.blobs_read, report.blobs_missing) == (
        1 + int(readable),
        1 + int(readable),
        0,
    )


def test_corrected_authority_uses_original_capture_time_not_correction_time(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    original = _capture(log, _page(LF_A))
    _capture(log, _page(salt="dropped"), authority_id="3203", observed_at=T1)
    key = record_key(record_to_json_line(original).encode("utf-8"))
    attribution = {"reason": "misattributed", "corrected_by": "operator"}
    log.append(
        RefiledObservation(
            observation=original.model_copy(update={"authority_id": "3203"}),
            correction=Correction(
                supersedes=key,
                correction_id="c1",
                corrected_fields=("authority_id",),
                previous_values={"authority_id": "3201"},
                corrected_at=T3,
                **attribution,
            ),
        )
    )
    log.append(RecordTombstone(retracts=key, correction_id="c1", corrected_at=T3, **attribution))

    report = last_seen_report(log)

    assert len(report.entries) == 1
    seen = report.entries[0]
    assert seen.key == ("3203", *KEY_A[1:])
    assert (seen.first_seen, seen.last_seen_linked) == (T0, T0)
    assert (seen.status, seen.unlinked_since, seen.evidence_at) == ("unlinked", T1, T1)
    assert (report.html_records, report.blobs_read) == (2, 2)


def test_report_is_repeatable_and_persists_nothing(tmp_path: Path) -> None:
    log = make_log(tmp_path)
    _capture(log, _page(LF_A, LF_B))
    _capture(log, _page(LF_B), observed_at=T1)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    first = last_seen_report(log).model_dump_json()
    second = last_seen_report(make_log(tmp_path)).model_dump_json()

    assert first == second
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
