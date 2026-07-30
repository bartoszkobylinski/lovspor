"""Tests for lovspor.history."""

import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.history import (
    HISTORY_SCHEMA_VERSION,
    HistoryEvent,
    HistoryRecord,
    _classify_bulk_sync,
    _classify_commit,
    _frontmatter_id_at,
    _parse_events,
    _parse_line_stats,
    _parse_rename_paths,
    _scan_frontmatter_id,
    extract_history,
    render_history_markdown,
    write_history,
)

# ---------- model tests ----------


def test_history_event_is_frozen() -> None:
    event = HistoryEvent(
        date=date(2026, 4, 27),
        commit="abc1234",
        type="added",
        subject="add(lov): skatteloven",
    )
    with pytest.raises(ValidationError):
        event.commit = "deadbee"  # type: ignore[misc]


def test_history_event_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        HistoryEvent(
            date=date(2026, 4, 27),
            commit="abc1234",
            type="exploded",  # type: ignore[arg-type]
            subject="anything",
        )


def test_history_event_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        HistoryEvent(
            date=date(2026, 4, 27),
            commit="abc1234",
            type="added",
            subject="x",
            mystery_field=42,  # type: ignore[call-arg]
        )


def test_history_event_optional_fields_default_to_none() -> None:
    event = HistoryEvent(
        date=date(2026, 4, 27),
        commit="abc1234",
        type="added",
        subject="add(lov): skatteloven",
    )

    assert event.from_path is None
    assert event.to_path is None
    assert event.lines_added is None
    assert event.lines_removed is None


def test_history_record_default_schema_version_is_one() -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    assert record.schema_version == HISTORY_SCHEMA_VERSION == 1


def test_history_record_is_frozen() -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    with pytest.raises(ValidationError):
        record.slug = "y"  # type: ignore[misc]


@pytest.mark.parametrize(
    "bad_slug",
    [
        "",
        "../escape",
        "..",
        "subdir/x",
        "x\\y",
        "with..dots",
        "with\x00null",
    ],
)
def test_history_record_rejects_unsafe_slug(bad_slug: str) -> None:
    """Path-traversal defense: slug must be a safe basename, otherwise
    write_history could write outside the intended history/ directory."""
    with pytest.raises(ValidationError) as exc_info:
        HistoryRecord(slug=bad_slug, doc_id="nl-x", events=[])
    message = str(exc_info.value)
    if bad_slug == "":
        assert "slug must not be empty" in message
    elif "/" in bad_slug or "\\" in bad_slug:
        assert "slug must not contain path separators" in message
    elif ".." in bad_slug:
        assert "slug must not contain parent references" in message
    elif "\x00" in bad_slug:
        assert "slug must not contain null bytes" in message


def test_history_record_slug_validation_messages_are_exact() -> None:
    cases = {
        "": "slug must not be empty",
        "subdir/x": "slug must not contain path separators: 'subdir/x'",
        "with..dots": "slug must not contain parent references: 'with..dots'",
        "with\x00null": "slug must not contain null bytes",
    }
    for bad_slug, expected in cases.items():
        with pytest.raises(ValidationError) as exc_info:
            HistoryRecord(slug=bad_slug, doc_id="nl-x", events=[])
        assert expected in str(exc_info.value)


def test_history_record_accepts_realistic_lovdata_slugs() -> None:
    for slug in ("skatteloven", "opplæringslova", "agenturloven-agl"):
        record = HistoryRecord(slug=slug, doc_id="nl-x", events=[])
        assert record.slug == slug


# ---------- _parse_line_stats ----------


def test_parse_line_stats_sums_added_and_removed() -> None:
    lines = ["52\t18\tlover/skatteloven.md"]
    assert _parse_line_stats(lines) == (52, 18)


def test_parse_line_stats_accepts_numstat_without_path() -> None:
    assert _parse_line_stats(["52\t18"]) == (52, 18)


def test_parse_line_stats_rejects_non_numeric_added_column() -> None:
    assert _parse_line_stats(["x\t18\tlover/skatteloven.md"]) == (None, None)


def test_parse_line_stats_sums_across_multiple_files() -> None:
    lines = ["10\t5\ta.md", "20\t3\tb.md"]
    assert _parse_line_stats(lines) == (30, 8)


def test_parse_line_stats_returns_none_for_binary_files() -> None:
    """numstat shows '-\\t-\\tpath' for binary; we treat that as no stats."""
    lines = ["-\t-\tlover/binary.bin"]
    assert _parse_line_stats(lines) == (None, None)


def test_parse_line_stats_skips_binary_when_text_files_present() -> None:
    lines = ["-\t-\tbinary.bin", "10\t5\ttext.md"]
    assert _parse_line_stats(lines) == (10, 5)


def test_parse_line_stats_returns_none_for_empty_input() -> None:
    assert _parse_line_stats([]) == (None, None)


# ---------- _parse_rename_paths ----------


def test_parse_rename_paths_brace_form() -> None:
    """Common prefix factored out by git: lover/{old.md => new.md}."""
    lines = ["0\t0\tlover/{nl-19990326-014.md => skatteloven.md}"]
    assert _parse_rename_paths(lines) == (
        "lover/nl-19990326-014.md",
        "lover/skatteloven.md",
    )


def test_parse_rename_paths_plain_form() -> None:
    """No common prefix: full paths on both sides."""
    lines = ["0\t0\told_dir/x.md => new_dir/x.md"]
    assert _parse_rename_paths(lines) == ("old_dir/x.md", "new_dir/x.md")


def test_parse_rename_paths_no_rename_in_stats() -> None:
    lines = ["52\t18\tlover/skatteloven.md"]
    assert _parse_rename_paths(lines) == (None, None)


def test_parse_rename_paths_skips_non_rename_then_finds_later_rename() -> None:
    lines = [
        "52\t18\tlover/skatteloven.md",
        "0\t0\tlover/{old.md => new.md}",
    ]
    assert _parse_rename_paths(lines) == ("lover/old.md", "lover/new.md")


def test_parse_rename_paths_ignores_two_column_numstat() -> None:
    assert _parse_rename_paths(["0\t0"]) == (None, None)


def test_parse_rename_paths_empty_input() -> None:
    assert _parse_rename_paths([]) == (None, None)


def test_parse_rename_paths_brace_form_with_suffix() -> None:
    lines = ["0\t0\tlover/{old => new}/file.md"]
    assert _parse_rename_paths(lines) == ("lover/old/file.md", "lover/new/file.md")


# ---------- _scan_frontmatter_id ----------


def test_scan_frontmatter_id_quoted_form() -> None:
    """The renderer emits ``id: "sf-…"`` (double-quoted) as the first field."""
    text = '---\nid: "sf-20260710-1545"\nslug: "x"\n---\n\nbody\n'
    assert _scan_frontmatter_id(text) == "sf-20260710-1545"


def test_scan_frontmatter_id_unquoted_form() -> None:
    text = "---\nid: nl-19990326-014\n---\nbody\n"
    assert _scan_frontmatter_id(text) == "nl-19990326-014"


def test_scan_frontmatter_id_no_frontmatter_returns_none() -> None:
    assert _scan_frontmatter_id("body without frontmatter\n") is None


def test_scan_frontmatter_id_empty_text_returns_none() -> None:
    assert _scan_frontmatter_id("") is None


def test_scan_frontmatter_id_missing_id_field_returns_none() -> None:
    text = '---\ntype: "history"\nslug: "x"\n---\nbody\n'
    assert _scan_frontmatter_id(text) is None


def test_scan_frontmatter_id_ignores_id_line_in_body() -> None:
    """Only the frontmatter block is scanned; an ``id:`` line after the
    closing delimiter must never match."""
    text = '---\ntype: "history"\n---\nid: "sneaky-body-id"\n'
    assert _scan_frontmatter_id(text) is None


# ---------- _classify_commit ----------


def test_classify_per_doc_add() -> None:
    event = _classify_commit(
        "abc1234deadbeef",
        "2026-04-27T10:15:00+02:00",
        "add(lov): skatteloven",
        ["52\t0\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "added"
    assert event.commit == "abc1234"
    assert event.date == date(2026, 4, 27)
    assert event.lines_added == 52
    assert event.lines_removed == 0
    assert event.from_path is None


def test_classify_per_doc_update() -> None:
    event = _classify_commit(
        "abc1234",
        "2024-01-15T03:30:00Z",
        "update(forskrift): trafikkforskriften",
        ["52\t18\tforskrifter/trafikkforskriften.md"],
    )
    assert event is not None
    assert event.type == "updated"
    assert event.subject == "update(forskrift): trafikkforskriften"


def test_classify_per_doc_rename_captures_paths() -> None:
    event = _classify_commit(
        "def5678",
        "2026-04-27T05:00:00Z",
        "rename(lov): skatteloven",
        ["0\t0\tlover/{nl-19990326-014.md => skatteloven.md}"],
    )
    assert event is not None
    assert event.type == "renamed"
    assert event.from_path == "lover/nl-19990326-014.md"
    assert event.to_path == "lover/skatteloven.md"


def test_classify_per_doc_remove() -> None:
    event = _classify_commit(
        "f00ba12",
        "2026-04-27T05:00:00Z",
        "remove(forskrift): defunctforskriften",
        [],
    )
    assert event is not None
    assert event.type == "removed"


def test_classify_migration_subject_is_renamed() -> None:
    event = _classify_commit(
        "3dddeca",
        "2026-04-27T05:00:00Z",
        "migration: rename 4522 documents to slug-based filenames",
        ["0\t0\tlover/{nl-19990326-014.md => skatteloven.md}"],
    )
    assert event is not None
    assert event.type == "renamed"
    assert event.from_path == "lover/nl-19990326-014.md"


def test_classify_migration_without_numstat_is_renamed() -> None:
    event = _classify_commit(
        "3dddeca",
        "2026-04-27T05:00:00Z",
        "migration: rename documents",
        [],
    )
    assert event is not None
    assert event.type == "renamed"
    assert event.from_path is None
    assert event.to_path is None


def test_classify_migration_backfill_eu_basis_is_updated() -> None:
    """A non-rename migration (Sprint 8 eu_basis backfill) re-renders the doc
    in place — a content update, not a filename change. It must not stamp a
    bogus "Filename renamed" event on every backfilled doc's history."""
    event = _classify_commit(
        "beef123",
        "2026-05-10T05:00:00Z",
        "migration: backfill eu_basis for 4200 documents",
        ["6\t0\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "updated"
    assert event.from_path is None
    assert event.to_path is None


def test_classify_rerender_migration_is_no_event() -> None:
    """A re-render migration rewrites Markdown from unchanged XML — a renderer
    fix, not a legal change. It must produce NO history event, or a corpus-wide
    backfill would stamp thousands of phantom "Content updated" events and
    corrupt every doc's last_changed."""
    event = _classify_commit(
        "cafe042",
        "2026-07-10T04:00:00Z",
        "migration: re-render 1991 documents (renderer v2)",
        ["166\t3\tforskrifter/skiltforskriften.md"],
    )
    assert event is None


def test_classify_rerender_migration_without_numstat_is_no_event() -> None:
    event = _classify_commit(
        "cafe042",
        "2026-07-10T04:00:00Z",
        "migration: re-render 12 documents (renderer v3)",
        [],
    )
    assert event is None


def test_classify_rerender_prefix_requires_the_exact_verb() -> None:
    """Only "migration: re-render " is exempt; any other migration subject
    keeps its Sprint-8 classification (updated)."""
    event = _classify_commit(
        "cafe042",
        "2026-07-10T04:00:00Z",
        "migration: re-renderish experiment",
        ["1\t1\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "updated"


def test_parse_events_drops_rerender_commits() -> None:
    """End-to-end through the git-log parser: the re-render commit vanishes,
    surrounding legal events survive in order."""
    raw = (
        "__COMMIT__\n"
        "aaa1111\n"
        "2026-07-10T04:00:00Z\n"
        "migration: re-render 1991 documents (renderer v2)\n"
        "166\t3\tforskrifter/skiltforskriften.md\n"
        "__COMMIT__\n"
        "bbb2222\n"
        "2026-06-01T04:00:00Z\n"
        "update(forskrift): skiltforskriften\n"
        "5\t2\tforskrifter/skiltforskriften.md\n"
        "__COMMIT__\n"
        "ccc3333\n"
        "2026-05-01T04:00:00Z\n"
        "add(forskrift): skiltforskriften\n"
        "40\t0\tforskrifter/skiltforskriften.md\n"
    )
    events = _parse_events(raw)
    assert [event.type for event in events] == ["updated", "added"]
    assert all(not event.subject.startswith("migration: re-render") for event in events)


def test_classify_bulk_sync_no_removals_is_added() -> None:
    """Pre-Sprint-4 bulk subject. With only additions in numstat, treat as add."""
    event = _classify_commit(
        "57c3052",
        "2026-04-26T19:05:00Z",
        "sync: 4522 new, 0 changed, 0 removed",
        ["127\t0\tforskrifter/sf-18960626-9805.md"],
    )
    assert event is not None
    assert event.type == "added"


def test_classify_bulk_sync_with_removals_is_updated() -> None:
    event = _classify_commit(
        "f00ba12",
        "2025-08-01T05:00:00Z",
        "sync: 100 new, 50 changed, 0 removed",
        ["52\t18\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "updated"


def test_classify_bulk_sync_deleted_file_is_removed() -> None:
    """Codex PR-A regression guard. Pre-Sprint-4 bulk sync that deleted
    one file used to be misclassified as updated because the heuristic
    only checked numstat removals. Now we cross-reference the subject's
    own removed-count: the file's row shows 0 additions AND the bulk
    subject claims removals, so it must be a delete."""
    event = _classify_commit(
        "abc1234",
        "2026-04-27T10:15:00Z",
        "sync: 0 new, 0 changed, 0 renamed, 1 removed",
        ["0\t42\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "removed"


def test_classify_bulk_sync_renamed_file_captures_from_to() -> None:
    """Codex PR-A round 2 regression guard. Pre-Sprint-4 single-mode
    syncs that renamed a doc encoded the rename in numstat as the
    brace form, but the subject said 'sync: ... 1 renamed, 0 removed'.
    The bulk-sync heuristic used to ignore the numstat rename row and
    classify the event as added, dropping from_path / to_path. Numstat
    is authoritative for the actual git operation, so it now wins
    over the bulk-sync subject text."""
    event = _classify_commit(
        "abc1234deadbeef",
        "2026-04-27T10:15:00+02:00",
        "sync: 0 new, 0 changed, 1 renamed, 0 removed",
        ["0\t0\tlover/{old.md => new.md}"],
    )
    assert event is not None
    assert event.type == "renamed"
    assert event.from_path == "lover/old.md"
    assert event.to_path == "lover/new.md"


def test_classify_bulk_sync_content_only_removal_stays_updated() -> None:
    """A file whose content was reduced (lines removed, none added) but
    that the bulk subject does NOT count as removed remains an update —
    differentiates "deleted from corpus" from "content shrank in place"."""
    event = _classify_commit(
        "def5678",
        "2025-08-01T05:00:00Z",
        "sync: 0 new, 1 changed, 0 removed",
        ["0\t100\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "updated"


def test_classify_bulk_sync_mixed_changes_and_removals_defaults_to_updated() -> None:
    """Codex PR-B regression guard. When a bulk commit claims BOTH
    'M changed > 0' AND 'L removed > 0', the same numstat row pattern
    (additions=0, removals>0) could mean either a delete or an
    in-place shrinkage update. Without name-status we cannot tell —
    so we default to 'updated' (the more common case in this codebase)
    rather than 'removed'. The misclassification window is bounded:
    no commit type in the post-Sprint-4 codebase produces ambiguous
    bulk-mode subjects."""
    event = _classify_commit(
        "abc1234deadbeef",
        "2026-04-27T10:15:00+02:00",
        "sync: 0 new, 1 changed, 0 renamed, 1 removed",
        ["0\t100\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "updated"


def test_classify_bulk_sync_pure_delete_still_classified_as_removed() -> None:
    """The fix above must NOT regress the pure-delete case: a bulk
    commit with 'M changed == 0' and 'L removed > 0' still classifies
    a 0-additions file as removed."""
    event = _classify_commit(
        "abc1234",
        "2026-04-27T10:15:00Z",
        "sync: 0 new, 0 changed, 0 renamed, 1 removed",
        ["0\t42\tlover/skatteloven.md"],
    )
    assert event is not None
    assert event.type == "removed"


def test_classify_bulk_sync_without_removed_count_never_removes() -> None:
    assert _classify_bulk_sync("sync: 0 new, 0 changed", 0, 42) == "updated"


def test_classify_bulk_sync_without_changed_count_can_remove() -> None:
    assert _classify_bulk_sync("sync: 0 new, 1 removed", 0, 42) == "removed"


def test_classify_bulk_sync_requires_positive_removed_lines_for_delete() -> None:
    assert _classify_bulk_sync("sync: 0 new, 0 changed, 1 removed", 0, 0) == "added"
    assert _classify_bulk_sync("sync: 0 new, 0 changed, 1 removed", 0, 1) == "removed"
    assert _classify_bulk_sync("sync: 0 new, 0 changed, 1 removed", None, 1) == "removed"


def test_classify_unknown_subject_falls_back_to_added() -> None:
    """Initial seed commit ('chore: initialize corpus repository') has no
    per-doc info; we still want an entry rather than dropping the commit."""
    event = _classify_commit(
        "27aef25",
        "2026-04-22T13:04:00Z",
        "chore: initialize corpus repository",
        [],
    )
    assert event is not None
    assert event.type == "added"


def test_classify_partial_rename_paths_do_not_count_as_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("lovspor.history._parse_rename_paths", lambda _lines: ("old.md", None))

    event = _classify_commit(
        "abc1234",
        "2026-04-27T10:15:00Z",
        "sync: 0 new, 0 changed, 0 renamed, 0 removed",
        ["0\t0\told.md => "],
    )

    assert event is not None
    assert event.type == "added"


def test_classify_uses_utc_calendar_date_of_author_instant() -> None:
    """The author %aI instant is converted to a UTC calendar date so history
    agrees with the time-machine's end-of-day-UTC cutoff. 23:59:59-08:00 is
    already 07:59:59 the NEXT day in UTC."""
    event = _classify_commit(
        "abc",
        "2026-04-27T23:59:59-08:00",
        "add(lov): x",
        [],
    )
    assert event is not None
    assert event.date == date(2026, 4, 28)


# ---------- _parse_events ----------


def test_parse_events_empty_string_returns_empty_list() -> None:
    assert _parse_events("") == []


def test_parse_events_single_commit() -> None:
    raw = (
        "__COMMIT__\n"
        "abc1234deadbeef\n"
        "2026-04-27T10:15:00+02:00\n"
        "add(lov): skatteloven\n"
        "52\t0\tlover/skatteloven.md\n"
    )
    events = _parse_events(raw)
    assert len(events) == 1
    assert events[0].type == "added"
    assert events[0].commit == "abc1234"


def test_parse_events_multiple_commits_in_order() -> None:
    raw = (
        "__COMMIT__\n"
        "newer12\n"
        "2026-04-27T05:00:00Z\n"
        "rename(lov): skatteloven\n"
        "0\t0\tlover/{nl-19990326-014.md => skatteloven.md}\n"
        "__COMMIT__\n"
        "older34\n"
        "2024-01-15T05:00:00Z\n"
        "update(lov): skatteloven\n"
        "52\t18\tlover/skatteloven.md\n"
    )
    events = _parse_events(raw)
    assert [e.type for e in events] == ["renamed", "updated"]
    assert [e.date for e in events] == [date(2026, 4, 27), date(2024, 1, 15)]


def test_parse_events_skips_malformed_block() -> None:
    """A block missing the date/subject lines is silently skipped."""
    raw = "__COMMIT__\nabc\n"  # only sha, no date or subject
    assert _parse_events(raw) == []


def test_parse_events_skips_malformed_block_then_parses_later_commit() -> None:
    raw = "__COMMIT__\nabc\n__COMMIT__\ndef5678\n2026-04-27T10:15:00Z\nadd(lov): x\n52\t0\tx.md\n"
    events = _parse_events(raw)
    assert len(events) == 1
    assert events[0].commit == "def5678"


def test_parse_events_preserves_first_numstat_line() -> None:
    raw = "__COMMIT__\nabc1234\n2026-04-27T10:15:00Z\nadd(lov): x\n52\t18\tx.md\n"
    events = _parse_events(raw)
    assert events[0].lines_added == 52
    assert events[0].lines_removed == 18


def test_parse_events_strips_only_outer_newlines_from_block() -> None:
    raw = "__COMMIT__\n\nabc1234\n2026-04-27T10:15:00Z\nadd(lov): x\n52\t18\tx.md\n\n"
    events = _parse_events(raw)
    assert events[0].commit == "abc1234"


def test_parse_events_accepts_three_line_block_without_numstat() -> None:
    """A commit that touched no tracked file (e.g., the initial 'chore:
    initialize corpus repository') has only sha + date + subject — no
    numstat rows. Must still produce one event."""
    raw = "__COMMIT__\n27aef25\n2026-04-22T13:04:00Z\nchore: initialize corpus repository\n"
    events = _parse_events(raw)
    assert len(events) == 1
    assert events[0].type == "added"
    assert events[0].lines_added is None
    assert events[0].lines_removed is None


# ---------- render_history_markdown ----------


def _history_fm(slug: str) -> str:
    return (
        f'---\ntype: "history"\nslug: "{slug}"\n'
        'source_provider: "Lovdata"\nsource_license: "NLOD 2.0"\n---\n\n'
    )


def test_render_empty_history() -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    assert render_history_markdown(record) == (
        _history_fm("x") + "# x — Change history\n\n_No history available; doc_id `nl-x`._\n"
    )


def test_render_history_carries_nlod_frontmatter() -> None:
    """Corpus invariant: history/<slug>.md carries NLOD 2.0 attribution."""
    content = render_history_markdown(HistoryRecord(slug="x", doc_id="nl-x", events=[]))

    assert content.startswith("---\n")
    assert 'source_license: "NLOD 2.0"' in content
    assert 'source_provider: "Lovdata"' in content


def test_render_single_event_includes_basics() -> None:
    record = HistoryRecord(
        slug="skatteloven",
        doc_id="nl-19990326-014",
        events=[
            HistoryEvent(
                date=date(2024, 1, 15),
                commit="def5678",
                type="updated",
                subject="update(lov): skatteloven",
                lines_added=52,
                lines_removed=18,
            ),
        ],
    )
    assert render_history_markdown(record) == (
        _history_fm("skatteloven") + "# skatteloven — Change history\n"
        "\n"
        "_1 events; doc_id `nl-19990326-014`._\n"
        "\n"
        "## 2024-01-15 — Content updated\n"
        "Lines: +52 -18.\n"
        "Subject: `update(lov): skatteloven`\n"
        "Commit: `def5678`.\n"
    )


def test_render_rename_event_includes_from_to() -> None:
    record = HistoryRecord(
        slug="skatteloven",
        doc_id="nl-19990326-014",
        events=[
            HistoryEvent(
                date=date(2026, 4, 27),
                commit="3dddeca",
                type="renamed",
                subject="migration: rename 4522 documents to slug-based filenames",
                from_path="lover/nl-19990326-014.md",
                to_path="lover/skatteloven.md",
            ),
        ],
    )
    assert render_history_markdown(record) == (
        _history_fm("skatteloven") + "# skatteloven — Change history\n"
        "\n"
        "_1 events; doc_id `nl-19990326-014`._\n"
        "\n"
        "## 2026-04-27 — Filename renamed\n"
        "Renamed: `lover/nl-19990326-014.md` → `lover/skatteloven.md`.\n"
        "Subject: `migration: rename 4522 documents to slug-based filenames`\n"
        "Commit: `3dddeca`.\n"
    )


def test_render_removed_event_uses_removed_title() -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="removed",
                subject="remove(lov): x",
            ),
        ],
    )
    assert "## 2024-01-01 — Removed from corpus\n" in render_history_markdown(record)


def test_render_partial_rename_or_line_stats_are_omitted() -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="renamed",
                subject="rename(lov): x",
                from_path="lover/old.md",
            ),
            HistoryEvent(
                date=date(2024, 1, 2),
                commit="def5678",
                type="updated",
                subject="update(lov): x",
                lines_added=1,
            ),
        ],
    )
    md = render_history_markdown(record)
    assert "Renamed:" not in md
    assert "Lines:" not in md


def test_render_is_deterministic() -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="added",
                subject="add(lov): x",
                lines_added=10,
                lines_removed=0,
            ),
        ],
    )
    assert render_history_markdown(record) == render_history_markdown(record)


def test_render_ends_with_single_trailing_newline() -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="added",
                subject="add(lov): x",
            ),
        ],
    )
    md = render_history_markdown(record)
    assert md.endswith("\n")
    assert not md.endswith("\n\n")


# ---------- write_history ----------


def test_write_history_creates_directory(tmp_path: Path) -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    json_path, md_path = write_history(record, tmp_path)
    assert json_path == tmp_path / "history" / "x.json"
    assert md_path == tmp_path / "history" / "x.md"
    assert json_path.exists()
    assert md_path.exists()


def test_write_history_json_is_deterministic(tmp_path: Path) -> None:
    """Same record in -> byte-identical JSON file out."""
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="added",
                subject="add(lov): x",
            ),
        ],
    )
    a = tmp_path / "a"
    b = tmp_path / "b"
    json_path_a, _ = write_history(record, a)
    json_path_b, _ = write_history(record, b)
    assert json_path_a.read_bytes() == json_path_b.read_bytes()


def test_write_history_json_has_exact_stable_format(tmp_path: Path) -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="added",
                subject="add(lov): x",
            ),
        ],
    )
    json_path, _ = write_history(record, tmp_path)

    assert json_path.read_text(encoding="utf-8") == (
        "{\n"
        '  "doc_id": "nl-x",\n'
        '  "events": [\n'
        "    {\n"
        '      "commit": "abc1234",\n'
        '      "date": "2024-01-01",\n'
        '      "from_path": null,\n'
        '      "lines_added": null,\n'
        '      "lines_removed": null,\n'
        '      "subject": "add(lov): x",\n'
        '      "to_path": null,\n'
        '      "type": "added"\n'
        "    }\n"
        "  ],\n"
        '  "schema_version": 1,\n'
        '  "slug": "x"\n'
        "}\n"
    )


def test_write_history_json_round_trips(tmp_path: Path) -> None:
    record = HistoryRecord(
        slug="x",
        doc_id="nl-x",
        events=[
            HistoryEvent(
                date=date(2024, 1, 1),
                commit="abc1234",
                type="renamed",
                subject="rename(lov): x",
                from_path="lover/old.md",
                to_path="lover/x.md",
            ),
        ],
    )
    json_path, _ = write_history(record, tmp_path)
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    rebuilt = HistoryRecord.model_validate(parsed)
    assert rebuilt == record


def test_write_history_md_matches_renderer_output(tmp_path: Path) -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    _, md_path = write_history(record, tmp_path)
    assert md_path.read_text(encoding="utf-8") == render_history_markdown(record)


def test_write_history_norwegian_chars_preserved(tmp_path: Path) -> None:
    """ensure_ascii=False keeps æøå literal in JSON."""
    record = HistoryRecord(
        slug="opplæringslova",
        doc_id="nl-x",
        events=[],
    )
    json_path, _ = write_history(record, tmp_path)
    assert "opplæringslova" in json_path.read_text(encoding="utf-8")


def test_write_history_is_idempotent_on_existing_directory(tmp_path: Path) -> None:
    """Calling write_history twice into the same dataset_dir must not
    fail when history/ already exists from the first call."""
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    write_history(record, tmp_path)
    json_path, md_path = write_history(record, tmp_path)
    assert json_path.exists()
    assert md_path.exists()


# ---------- extract_history end-to-end ----------


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _setup_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)


def test_extract_history_walks_real_git_log(tmp_path: Path) -> None:
    """End-to-end: build a tiny repo with two commits, follow a rename,
    and check the extractor produces the expected events shape."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()

    # First commit: add the doc under its old opaque name.
    (repo / "lover" / "nl-19990326-014.md").write_text("body v1\n", encoding="utf-8")
    _git("add", "lover/nl-19990326-014.md", cwd=repo)
    _git("commit", "-m", "add(lov): nl-19990326-014", cwd=repo)

    # Second commit: rename to slug.
    _git(
        "mv",
        "lover/nl-19990326-014.md",
        "lover/skatteloven.md",
        cwd=repo,
    )
    _git("commit", "-m", "migration: rename 1 document to slug-based filenames", cwd=repo)

    record = extract_history(
        repo_path=repo,
        current_path="lover/skatteloven.md",
        doc_id="nl-19990326-014",
        slug="skatteloven",
    )

    assert record.slug == "skatteloven"
    assert record.doc_id == "nl-19990326-014"
    assert len(record.events) == 2
    # Newest first (the rename, then the original add)
    assert record.events[0].type == "renamed"
    assert record.events[0].from_path == "lover/nl-19990326-014.md"
    assert record.events[0].to_path == "lover/skatteloven.md"
    assert record.events[1].type == "added"


def test_extract_history_classifies_eu_basis_backfill_as_update(tmp_path: Path) -> None:
    """End-to-end: a real ``migration: backfill eu_basis`` commit edits the
    doc's frontmatter in place. git records no rename, so the extracted event
    must be ``updated`` — not the spurious ``renamed`` the old blanket
    migration rule produced for every backfilled doc."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()

    doc = repo / "lover" / "skatteloven.md"
    doc.write_text("---\ntitle: Skatteloven\n---\nbody\n", encoding="utf-8")
    _git("add", "lover/skatteloven.md", cwd=repo)
    _git("commit", "-m", "add(lov): skatteloven", cwd=repo)

    # Sprint 8 re-render adds the eu_basis frontmatter field in place.
    doc.write_text(
        "---\ntitle: Skatteloven\neu_basis: []\n---\nbody\n",
        encoding="utf-8",
    )
    _git("add", "lover/skatteloven.md", cwd=repo)
    _git("commit", "-m", "migration: backfill eu_basis for 1 documents", cwd=repo)

    record = extract_history(
        repo_path=repo,
        current_path="lover/skatteloven.md",
        doc_id="nl-19990326-014",
        slug="skatteloven",
    )

    assert len(record.events) == 2
    # Newest first: the backfill, then the original add.
    assert record.events[0].type == "updated"
    assert record.events[0].from_path is None
    assert record.events[0].to_path is None
    assert record.events[1].type == "added"


def test_extract_history_raises_on_git_log_failure(tmp_path: Path) -> None:
    """A non-existent path should surface the git failure rather than
    silently returning an empty record — caller must decide whether to
    handle it (e.g. write empty history.json or skip)."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    (repo / "lover" / "real.md").write_text("body\n", encoding="utf-8")
    _git("add", "lover/real.md", cwd=repo)
    _git("commit", "-m", "add(lov): real", cwd=repo)

    # Path not present in any commit — git log on the bad path returns
    # exit code 0 and empty stdout, NOT an error. So extract_history
    # produces an empty event list rather than raising. Document that.
    record = extract_history(
        repo_path=repo,
        current_path="lover/does-not-exist.md",
        doc_id="nl-x",
        slug="does-not-exist",
    )
    assert record.events == []

    # An actual subprocess failure (e.g. cwd is not a repo at all) does
    # raise CalledProcessError so callers see the error.
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        extract_history(
            repo_path=not_a_repo,
            current_path="anything.md",
            doc_id="nl-x",
            slug="x",
        )


def test_extract_history_records_utc_date_for_non_utc_author_timezone(tmp_path: Path) -> None:
    """A commit authored 00:30 at +02:00 is 22:30 the PREVIOUS day in UTC.
    History must record the UTC date (2026-04-15), not the author-local one
    (2026-04-16), so it agrees with get_law_at's end-of-day-UTC cutoff."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    (repo / "lover" / "x.md").write_text("body\n", encoding="utf-8")
    _git("add", "lover/x.md", cwd=repo)
    when = "2026-04-16T00:30:00+02:00"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    subprocess.run(
        ["git", "commit", "-m", "add(lov): x"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )

    record = extract_history(repo_path=repo, current_path="lover/x.md", doc_id="nl-x", slug="x")

    assert record.events[0].date == date(2026, 4, 15)


def test_extract_history_records_raw_non_ascii_rename_paths(tmp_path: Path) -> None:
    """Regression: git C-quotes non-ASCII paths in ``--numstat`` rename rows
    by default, so from/to would be stored as
    ``"lover/oppl\\303\\246ringslova.md"`` (quotes + octal escapes included)
    instead of the real UTF-8 path. ~2,000 corpus files carry æøå slugs."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    (repo / "lover" / "opplæringslova.md").write_text("body\n", encoding="utf-8")
    _git("add", "lover/opplæringslova.md", cwd=repo)
    _git("commit", "-m", "add(lov): opplaeringslova", cwd=repo)

    _git("mv", "lover/opplæringslova.md", "lover/opplæringslova-ny.md", cwd=repo)
    _git("commit", "-m", "migration: rename to new kortform", cwd=repo)

    record = extract_history(
        repo_path=repo,
        current_path="lover/opplæringslova-ny.md",
        doc_id="nl-x",
        slug="opplaeringslova-ny",
    )

    assert record.events[0].type == "renamed"
    assert record.events[0].from_path == "lover/opplæringslova.md"
    assert record.events[0].to_path == "lover/opplæringslova-ny.md"


# ---------- extract_history identity boundary ----------


def _head_sha7(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()[:7]


def _frontmatter_doc(doc_id: str, body: str) -> str:
    """Minimal doc text in the renderer's frontmatter shape: quoted ``id``
    first, then the body."""
    return f'---\nid: "{doc_id}"\ntype: "forskrift"\n---\n\n{body}\n'


def _commit_doc(repo: Path, rel_path: str, text: str, subject: str) -> str:
    """Write ``rel_path``, commit with ``subject``, return the short sha."""
    (repo / rel_path).write_text(text, encoding="utf-8")
    _git("add", rel_path, cwd=repo)
    _git("commit", "-m", subject, cwd=repo)
    return _head_sha7(repo)


def test_extract_history_stops_at_doc_identity_boundary(tmp_path: Path) -> None:
    """RCA defect 3 (``forskrift-om-omregningsfaktorer``): a replacement act
    overwrote a repealed act's file in place (the ``ce3df5a13`` shape — M,
    not A), and ``git log --follow`` fused both identities into one history.
    The walk must stop at the first revision whose frontmatter id differs
    from the record's doc_id: the new act keeps ONLY its own events."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "forskrifter").mkdir()
    path = "forskrifter/forskrift-om-omregningsfaktorer.md"

    # Old act (sf-20090520-0534): born on the path, then updated.
    old_add = _commit_doc(
        repo,
        path,
        _frontmatter_doc("sf-20090520-0534", "old body v1"),
        "add(forskrift): forskrift-om-omregningsfaktorer",
    )
    old_update = _commit_doc(
        repo,
        path,
        _frontmatter_doc("sf-20090520-0534", "old body v2"),
        "update(forskrift): forskrift-om-omregningsfaktorer",
    )

    # Replacement act (sf-20260710-1545) overwrites the SAME path in place,
    # then receives its own update.
    new_add = _commit_doc(
        repo,
        path,
        _frontmatter_doc("sf-20260710-1545", "new body v1"),
        "add(forskrift): forskrift-om-omregningsfaktorer",
    )
    new_update = _commit_doc(
        repo,
        path,
        _frontmatter_doc("sf-20260710-1545", "new body v2"),
        "update(forskrift): forskrift-om-omregningsfaktorer",
    )

    record = extract_history(
        repo_path=repo,
        current_path=path,
        doc_id="sf-20260710-1545",
        slug="forskrift-om-omregningsfaktorer",
    )

    # Newest first: only the new act's own events; count feeds total_changes.
    assert [e.commit for e in record.events] == [new_update, new_add]
    assert [e.type for e in record.events] == ["updated", "added"]
    assert len(record.events) == 2
    # The old identity's events are never attributed to the new act.
    assert {old_add, old_update}.isdisjoint({e.commit for e in record.events})
    assert all(e.from_path is None and e.to_path is None for e in record.events)


def test_extract_history_follows_rename_of_same_identity(tmp_path: Path) -> None:
    """A rename of the SAME doc id remains part of the lineage — the
    identity boundary only cuts revisions whose frontmatter id differs."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "forskrifter").mkdir()
    add_sha = _commit_doc(
        repo,
        "forskrifter/sf-20090520-0534.md",
        _frontmatter_doc("sf-20090520-0534", "body"),
        "sync: 1 new, 0 changed, 0 removed",
    )

    _git(
        "mv",
        "forskrifter/sf-20090520-0534.md",
        "forskrifter/forskrift-om-omregningsfaktorer.md",
        cwd=repo,
    )
    _git("commit", "-m", "migration: rename 1 documents to slug-based filenames", cwd=repo)
    rename_sha = _head_sha7(repo)

    record = extract_history(
        repo_path=repo,
        current_path="forskrifter/forskrift-om-omregningsfaktorer.md",
        doc_id="sf-20090520-0534",
        slug="forskrift-om-omregningsfaktorer",
    )

    assert [e.commit for e in record.events] == [rename_sha, add_sha]
    assert record.events[0].type == "renamed"
    assert record.events[0].from_path == "forskrifter/sf-20090520-0534.md"
    assert record.events[0].to_path == "forskrifter/forskrift-om-omregningsfaktorer.md"
    assert record.events[1].type == "added"


def test_extract_history_boundary_detected_across_rename(tmp_path: Path) -> None:
    """Path tracking through renames: the identity check must read the blob
    at the path the file had AT each revision, so a foreign identity that
    lived at the pre-rename path is still detected and cut."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "forskrifter").mkdir()
    old_path = "forskrifter/sf-19990101-0001.md"

    foreign_add = _commit_doc(
        repo,
        old_path,
        _frontmatter_doc("sf-19990101-0001", "foreign body"),
        "add(forskrift): sf-19990101-0001",
    )
    takeover = _commit_doc(
        repo,
        old_path,
        _frontmatter_doc("sf-20260101-0002", "our body v1"),
        "add(forskrift): sf-19990101-0001",
    )
    _git("mv", old_path, "forskrifter/vår-forskrift.md", cwd=repo)
    _git("commit", "-m", "rename(forskrift): vår-forskrift", cwd=repo)
    rename_sha = _head_sha7(repo)
    update_sha = _commit_doc(
        repo,
        "forskrifter/vår-forskrift.md",
        _frontmatter_doc("sf-20260101-0002", "our body v2"),
        "update(forskrift): vår-forskrift",
    )

    record = extract_history(
        repo_path=repo,
        current_path="forskrifter/vår-forskrift.md",
        doc_id="sf-20260101-0002",
        slug="vår-forskrift",
    )

    assert [e.commit for e in record.events] == [update_sha, rename_sha, takeover]
    assert [e.type for e in record.events] == ["updated", "renamed", "added"]
    assert foreign_add not in {e.commit for e in record.events}


def test_extract_history_treats_idless_history_as_same_identity(tmp_path: Path) -> None:
    """Pre-frontmatter-era revisions (no parseable ``id``) extend the current
    identity when no foreign id was ever seen on the path above them."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    idless_add = _commit_doc(
        repo,
        "lover/skatteloven.md",
        "pre-frontmatter body\n",
        "add(lov): skatteloven",
    )
    update_sha = _commit_doc(
        repo,
        "lover/skatteloven.md",
        _frontmatter_doc("nl-19990326-014", "body v2"),
        "update(lov): skatteloven",
    )

    record = extract_history(
        repo_path=repo,
        current_path="lover/skatteloven.md",
        doc_id="nl-19990326-014",
        slug="skatteloven",
    )

    assert [e.commit for e in record.events] == [update_sha, idless_add]
    assert [e.type for e in record.events] == ["updated", "added"]


def test_extract_history_idless_revision_below_boundary_is_excluded(tmp_path: Path) -> None:
    """An id-less revision is same-identity only while the lineage from HEAD
    is unbroken: once a foreign id appears, the id-less revisions below it
    are cut along with the boundary revision itself."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    idless_add = _commit_doc(
        repo,
        "lover/x.md",
        "pre-frontmatter body of some other doc\n",
        "add(lov): x",
    )
    foreign_update = _commit_doc(
        repo,
        "lover/x.md",
        _frontmatter_doc("nl-19000101-001", "foreign body"),
        "update(lov): x",
    )
    ours_add = _commit_doc(
        repo,
        "lover/x.md",
        _frontmatter_doc("nl-20260101-002", "our body"),
        "add(lov): x",
    )

    record = extract_history(
        repo_path=repo,
        current_path="lover/x.md",
        doc_id="nl-20260101-002",
        slug="x",
    )

    assert [e.commit for e in record.events] == [ours_add]
    assert {idless_add, foreign_update}.isdisjoint({e.commit for e in record.events})


def test_frontmatter_id_at_missing_blob_returns_none(tmp_path: Path) -> None:
    """A revision where the path has no blob (never existed, or deleted in
    that commit) yields ``None`` — treated as same-identity by the walk."""
    repo = tmp_path / "lovverk"
    _setup_repo(repo)
    (repo / "lover").mkdir()
    _commit_doc(repo, "lover/real.md", "body\n", "add(lov): real")
    sha = _head_sha7(repo)

    assert _frontmatter_id_at(repo, sha, "lover/absent.md") is None
    assert _frontmatter_id_at(repo, sha, "lover/real.md") is None  # no frontmatter id
