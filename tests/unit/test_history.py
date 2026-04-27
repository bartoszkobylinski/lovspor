"""Tests for lovspor.history."""

import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.history import (
    HISTORY_SCHEMA_VERSION,
    HistoryEvent,
    HistoryRecord,
    _classify_commit,
    _parse_events,
    _parse_line_stats,
    _parse_rename_paths,
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
    with pytest.raises(ValidationError):
        HistoryRecord(slug=bad_slug, doc_id="nl-x", events=[])


def test_history_record_accepts_realistic_lovdata_slugs() -> None:
    for slug in ("skatteloven", "opplæringslova", "agenturloven-agl"):
        record = HistoryRecord(slug=slug, doc_id="nl-x", events=[])
        assert record.slug == slug


# ---------- _parse_line_stats ----------


def test_parse_line_stats_sums_added_and_removed() -> None:
    lines = ["52\t18\tlover/skatteloven.md"]
    assert _parse_line_stats(lines) == (52, 18)


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


def test_parse_rename_paths_empty_input() -> None:
    assert _parse_rename_paths([]) == (None, None)


def test_parse_rename_paths_brace_form_with_suffix() -> None:
    lines = ["0\t0\tlover/{old => new}/file.md"]
    assert _parse_rename_paths(lines) == ("lover/old/file.md", "lover/new/file.md")


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


def test_classify_strips_iso_timezone_to_date() -> None:
    """Different timezone offsets produce the same calendar date."""
    event = _classify_commit(
        "abc",
        "2026-04-27T23:59:59-08:00",
        "add(lov): x",
        [],
    )
    assert event is not None
    assert event.date == date(2026, 4, 27)


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


def test_render_empty_history() -> None:
    record = HistoryRecord(slug="x", doc_id="nl-x", events=[])
    md = render_history_markdown(record)
    assert md.startswith("# x — Change history\n")
    assert "_No history available; doc_id `nl-x`._" in md
    assert md.endswith("\n")


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
    md = render_history_markdown(record)
    assert "# skatteloven — Change history" in md
    assert "_1 events; doc_id `nl-19990326-014`._" in md
    assert "## 2024-01-15 — Content updated" in md
    assert "Lines: +52 -18." in md
    assert "Commit: `def5678`." in md


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
    md = render_history_markdown(record)
    assert "Renamed: `lover/nl-19990326-014.md` → `lover/skatteloven.md`." in md


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
