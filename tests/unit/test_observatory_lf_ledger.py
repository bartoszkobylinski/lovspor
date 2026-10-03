"""The first-seen ledger of Lovdata local-regulation ids (issue #509).

The owner's rule: an LF id first seen on a kommune's pages is requested from
that kommune the next day. So the ledger has one job — say, for every
(authority, kind, id), the earliest moment the archive saw a page link it —
and it must keep saying the same thing however often it is updated.
"""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.errors import LogIntegrityError
from lovspor.observatory.lf_ledger import (
    LedgerEntry,
    _prefix_digest,
    first_seen_between,
    ledger_cursor_path,
    ledger_path,
    read_ledger,
    update_ledger,
)
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import (
    ArtifactObservation,
    Correction,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    Tombstone,
    record_key,
    record_to_json_line,
)
from lovspor.observatory.storage import ObservatoryRoot

T0 = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
LF_A = "https://lovdata.no/dokument/LF/forskrift/2020-11-19-2630"
LF_B = "https://lovdata.no/dokument/LTII/forskrift/2026-02-26-329"


def make_log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def _page(*hrefs: str, salt: str = "") -> bytes:
    links = "".join(f'<a href="{href}">x</a>' for href in hrefs)
    return f"<html><!--{salt}--><body>{links}</body></html>".encode()


def _capture(log: ObservationLog, payload: bytes, **fields: object) -> ArtifactObservation:
    values: dict[str, object] = {
        "authority_id": "3201",
        "url": "https://kommune.example.invalid/forskrifter",
        "observed_at": T0,
        "content_type": "text/html; charset=utf-8",
        "http_status": 200,
    }
    values.update(fields)
    record = ArtifactObservation(
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="test-agent",
            rate_limit_seconds=1.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        **values,  # type: ignore[arg-type]
    )
    log.append_artifact(record, payload)
    return record


def _keys(entries: list[LedgerEntry]) -> list[tuple[str, str, str]]:
    return [(entry.authority_id, entry.kind, entry.lf_id) for entry in entries]


class TestWhatTheLedgerRecords:
    def test_one_entry_per_linked_id_with_its_page_and_blob(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = _capture(log, _page(LF_A, LF_B))

        update_ledger(log)

        assert read_ledger(log) == [
            LedgerEntry(
                authority_id="3201",
                lf_id="2020-11-19-2630",
                kind="LF",
                first_seen=T0,
                url=record.url,
                sha256=record.sha256,
            ),
            LedgerEntry(
                authority_id="3201",
                lf_id="2026-02-26-329",
                kind="LTII",
                first_seen=T0,
                url=record.url,
                sha256=record.sha256,
            ),
        ]

    def test_the_earliest_sighting_wins_even_when_appended_later(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A, salt="late"), observed_at=T0, url="https://k.invalid/late")
        early = _capture(
            log,
            _page(LF_A, salt="early"),
            observed_at=T0 - timedelta(hours=5),
            url="https://k.invalid/early",
        )

        update_ledger(log)

        [entry] = read_ledger(log)
        assert (entry.first_seen, entry.url, entry.sha256) == (
            early.observed_at,
            early.url,
            early.sha256,
        )

    def test_a_tie_goes_to_the_record_appended_first(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        first = _capture(log, _page(LF_A, salt="1"), url="https://k.invalid/first")
        _capture(log, _page(LF_A, salt="2"), url="https://k.invalid/second")

        update_ledger(log)

        assert [entry.url for entry in read_ledger(log)] == [first.url]

    def test_new_entries_are_appended_in_first_seen_order(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A), observed_at=T0, authority_id="0301")
        _capture(log, _page(LF_B), observed_at=T0 - timedelta(days=1), authority_id="9999")

        update_ledger(log)

        assert [entry.authority_id for entry in read_ledger(log)] == ["9999", "0301"]

    def test_the_same_id_is_kept_per_authority(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A), authority_id="3201")
        _capture(log, _page(LF_A), authority_id="4601")

        update_ledger(log)

        assert _keys(read_ledger(log)) == [
            ("3201", "LF", "2020-11-19-2630"),
            ("4601", "LF", "2020-11-19-2630"),
        ]

    @pytest.mark.parametrize(
        "fields",
        [
            {"content_type": "application/pdf"},
            {"content_type": "application/xml"},
            {"http_status": 404},
            {"http_status": 199},
            {"http_status": 300},
            {"content_type": 'application/octet-stream; name="page.html"'},
            {"content_type": "application/json;profile=html"},
            {"content_type": "application/json; profile=html; charset=utf-8"},
        ],
    )
    def test_only_successfully_served_html_is_read(
        self, tmp_path: Path, fields: dict[str, object]
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A), **fields)

        report = update_ledger(log)

        assert read_ledger(log) == []
        assert report.html_records == 0

    @pytest.mark.parametrize(
        "content_type",
        ["TEXT/HTML", "application/xhtml+xml", "text/html;charset=utf-8", "text/html;a=b;c=d"],
    )
    def test_html_is_recognised_in_any_spelling(self, tmp_path: Path, content_type: str) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A), content_type=content_type, http_status=299)

        update_ledger(log)

        assert _keys(read_ledger(log)) == [("3201", "LF", "2020-11-19-2630")]

    def test_a_page_with_no_links_adds_nothing(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page("https://lovdata.no/dokument/SF/forskrift/2002-03-22-313"))

        report = update_ledger(log)

        assert (read_ledger(log), report.appended, report.html_records) == ([], (), 1)


class TestUpdatingIsIdempotentAndCheap:
    def test_a_second_update_appends_nothing(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        update_ledger(log)
        before = ledger_path(log).read_bytes()

        report = update_ledger(log)

        assert report.appended == ()
        assert report.started_at_offset == log.log_path.stat().st_size
        assert report.html_records == 0
        assert ledger_path(log).read_bytes() == before

    def test_only_the_tail_of_the_log_is_read(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        update_ledger(log)
        _capture(log, _page(LF_B), observed_at=T0 + timedelta(days=1))

        report = update_ledger(log)

        assert (report.html_records, report.blobs_read) == (1, 1)
        assert report.started_at_offset > 0
        assert _keys(list(report.appended)) == [("3201", "LTII", "2026-02-26-329")]

    def test_identical_bytes_are_read_once_per_update(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        payload = _page(LF_A)
        _capture(log, payload, url="https://k.invalid/a")
        _capture(log, payload, url="https://k.invalid/b")

        report = update_ledger(log)

        assert (report.html_records, report.blobs_read) == (2, 1)

    def test_a_rebuild_rereads_everything_and_still_appends_nothing_known(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        update_ledger(log)

        report = update_ledger(log, rebuild=True)

        assert (report.started_at_offset, report.html_records, report.appended) == (0, 1, ())
        assert len(read_ledger(log)) == 1

    def test_a_log_rewritten_under_the_cursor_is_read_again(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        update_ledger(log)
        log.log_path.write_bytes(log.log_path.read_bytes().replace(b"3201", b"3202"))

        report = update_ledger(log)

        assert report.started_at_offset == 0
        assert ("3202", "LF", "2020-11-19-2630") in _keys(read_ledger(log))

    @pytest.mark.parametrize(
        "content",
        [
            "not json",
            json.dumps({"schema_version": 1, "log_offset": 10**9, "prefix_sha256": "0" * 64}),
            json.dumps({"schema_version": 2, "log_offset": 0, "prefix_sha256": "0" * 64}),
        ],
    )
    def test_a_doubtful_cursor_costs_a_full_read(self, tmp_path: Path, content: str) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        update_ledger(log)
        ledger_cursor_path(log).write_text(content, encoding="utf-8")

        report = update_ledger(log)

        assert (report.started_at_offset, report.html_records, report.appended) == (0, 1, ())

    def test_a_cursor_past_an_absent_log_costs_a_full_read(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        cursor = {"schema_version": 1, "log_offset": 1, "prefix_sha256": "0" * 64}
        ledger_cursor_path(log).write_text(json.dumps(cursor), encoding="utf-8")

        report = update_ledger(log)

        assert (report.started_at_offset, report.html_records, report.appended) == (0, 0, ())

    def test_a_prefix_longer_than_the_log_digests_the_bytes_there_are(self, tmp_path: Path) -> None:
        short = tmp_path / "short.jsonl"
        short.write_bytes(b"abc")

        assert _prefix_digest(short, 10) == hashlib.sha256(b"abc").hexdigest()

    def test_non_ascii_pages_are_kept_whatever_the_locale(
        self, tmp_path: Path, c_locale: None
    ) -> None:
        log = make_log(tmp_path)
        record = _capture(log, _page(LF_A), url="https://k.invalid/forskrifter-for-v\u00e6r")

        update_ledger(log)

        assert [entry.url for entry in read_ledger(log)] == [record.url]

    def test_a_correction_after_the_cursor_forces_a_full_read(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = _capture(log, _page(LF_A))
        update_ledger(log)
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
                    corrected_at=T0 + timedelta(days=1),
                    **attribution,
                ),
            )
        )
        log.append(
            RecordTombstone(
                retracts=key, correction_id="c1", corrected_at=T0 + timedelta(days=1), **attribution
            )
        )

        report = update_ledger(log)

        assert report.started_at_offset == 0
        assert ("3203", "LF", "2020-11-19-2630") in _keys(list(report.appended))
        assert update_ledger(log).started_at_offset == log.log_path.stat().st_size

    def test_the_cursor_records_where_the_read_stopped(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))

        update_ledger(log)

        cursor = json.loads(ledger_cursor_path(log).read_text(encoding="utf-8"))
        size = log.log_path.stat().st_size
        assert cursor["log_offset"] == size
        assert cursor["prefix_sha256"] == hashlib.sha256(log.log_path.read_bytes()).hexdigest()


class TestWhatTheLedgerRefuses:
    def test_a_damaged_log_appends_nothing_and_does_not_move_the_cursor(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        clean = log.log_path.stat().st_size
        with log.log_path.open("ab") as handle:
            handle.write(b'{"torn":')

        with pytest.raises(LogIntegrityError) as refused:
            update_ledger(log)

        assert str(refused.value) == (
            f"{log.log_path}: unreadable record after byte {clean}; the LF ledger is not "
            "updated from a damaged log (run `observatory verify`)"
        )
        assert not ledger_path(log).exists() or read_ledger(log) == []
        assert not ledger_cursor_path(log).exists()

    def test_a_damaged_ledger_is_refused_rather_than_extended(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        _capture(log, _page(LF_A))
        ledger_path(log).write_text('{"authority_id": "3201"}\n', encoding="utf-8")

        with pytest.raises(LogIntegrityError, match=r"lf-ledger\.jsonl:1: .*--rebuild"):
            update_ledger(log)

    def test_a_damaged_ledger_is_refused_when_read(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        ledger_path(log).write_text("\n", encoding="utf-8")

        with pytest.raises(LogIntegrityError) as refused:
            read_ledger(log)

        assert str(refused.value) == (
            f"{ledger_path(log)}:1: unreadable ledger entry; the ledger is derived from the "
            "log, so remove it and run `lovspor observatory lf-ledger --rebuild`"
        )

    def test_a_missing_blob_is_counted_not_fatal(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        record = _capture(log, _page(LF_A))
        log.append(
            Tombstone(
                sha256=record.sha256,
                removed_at=T0,
                basis="privacy",
                authorised_by="operator",
            )
        )
        log.blob_path(record.sha256).unlink()

        report = update_ledger(log)

        assert (report.blobs_missing, report.blobs_read, read_ledger(log)) == (1, 0, [])

    def test_every_missing_blob_is_counted(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        records = [_capture(log, _page(LF_A, salt=str(n))) for n in range(3)]
        for record in records:
            log.blob_path(record.sha256).unlink()

        report = update_ledger(log)

        assert (report.blobs_missing, report.blobs_read) == (3, 0)

    def test_an_empty_archive_has_an_empty_ledger(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)

        report = update_ledger(log)
        again = update_ledger(log)

        assert (read_ledger(log), report.appended, report.html_records) == ([], (), 0)
        assert again.started_at_offset == 0


class TestFirstSeenByDay:
    def _entry(self, when: datetime, lf_id: str = "2020-11-19-2630") -> LedgerEntry:
        return LedgerEntry(
            authority_id="3201",
            lf_id=lf_id,
            kind="LF",
            first_seen=when,
            url="https://k.invalid/a",
            sha256="0" * 64,
        )

    def test_the_day_is_the_oslo_calendar_day(self) -> None:
        late_evening_utc = datetime(2026, 10, 1, 22, 30, tzinfo=UTC)
        entries = [self._entry(late_evening_utc)]

        assert first_seen_between(entries, date(2026, 10, 2), date(2026, 10, 2)) == entries
        assert first_seen_between(entries, date(2026, 10, 1), date(2026, 10, 1)) == []

    def test_both_ends_of_the_range_are_included(self) -> None:
        entries = [
            self._entry(datetime(2026, 9, 30, 12, tzinfo=UTC), "2020-01-01-1"),
            self._entry(datetime(2026, 10, 1, 12, tzinfo=UTC), "2020-01-01-2"),
            self._entry(datetime(2026, 10, 2, 12, tzinfo=UTC), "2020-01-01-3"),
            self._entry(datetime(2026, 10, 3, 12, tzinfo=UTC), "2020-01-01-4"),
        ]

        chosen = first_seen_between(entries, date(2026, 10, 1), date(2026, 10, 2))

        assert [entry.lf_id for entry in chosen] == ["2020-01-01-2", "2020-01-01-3"]

    def test_a_naive_first_seen_is_refused(self) -> None:
        with pytest.raises(ValueError, match="UTC|timezone|naive"):
            self._entry(datetime(2026, 10, 1, 12))
