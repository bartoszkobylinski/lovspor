"""``lovspor promote backfill``, ``backfill-preview`` and ``observe`` end to end (ADR-0016 S6).

Every state is reached the way an operator reaches it: captures appended by
the observatory's own writer, decisions by ``promote approve``, versions by
``promote backfill``, commits by ``git`` in the temporary ``lovverk``
checkout — one per version, in the order the command prints.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import Tombstone
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.commands import HELD_EXIT_CODE
from lovspor.promotion.decisions import DECISIONS_FILENAME
from lovspor.promotion.extract import EXTRACTOR_VERSION
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LOCAL = "lokale-forskrifter"
CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
RETITLED = ("Forskrift om renovasjon, slam og septik, Eksempel kommune", *REGULATION_LINES[1:])
DAY = timedelta(days=1)
EMPTY_PAGE = b"<!doctype html><html><body><main><p>Laster ...</p></main></body></html>"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path)


def _approve(sha256: str, tmp_path: Path, decision: str = "approve") -> None:
    result = approve(sha256, Decision(decision=decision).write(tmp_path))
    assert result.exit_code == 0, result.output


def _aba(root: Path, tmp_path: Path) -> tuple[str, str]:
    """A on days 0 and 1, B on day 2, A again on day 3; both texts approved."""
    a = store(root, html_page(), observed_at=FIRST_SEEN)
    store(root, html_page(), observed_at=FIRST_SEEN + DAY)
    b = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
    store(root, html_page(), observed_at=FIRST_SEEN + 3 * DAY)
    _approve(a, tmp_path)
    _approve(b, tmp_path)
    return a, b


def _commit_printed(corpus: Path, output: str) -> str:
    """Commit exactly as the command said to — its first commit line; the subject used."""
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("  git -C") and " commit -m " in line
    )
    subject = line.split(" commit -m ", 1)[1].strip("'")
    git(corpus, "add", "--", LOCAL)
    git(corpus, "commit", "-q", "-m", subject)
    return subject


def _manifest(corpus: Path) -> dict[str, dict[str, object]]:
    return json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))["documents"]


def _observations(corpus: Path) -> dict[str, object]:
    [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _backfill_all(corpus: Path, sha256: str) -> list[str]:
    subjects = []
    for _ in range(5):
        result = promote("backfill", sha256, corpus)
        if "Nothing to write" in result.output:
            assert result.exit_code == 0, result.output
            return subjects
        assert result.exit_code == 0, result.output
        subjects.append(_commit_printed(corpus, result.output))
    raise AssertionError("backfill did not finish")


class TestBackfillWritesOneVersionPerRunInOrder:
    def test_captures_after_approval_do_not_change_backfill_files(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path)
        other = make_corpus(tmp_path / "other")
        first = promote("backfill", a, corpus)
        assert first.exit_code == 0, first.output
        store(root, html_page(), observed_at=datetime.now(UTC) + DAY)

        second = promote("backfill", a, other)

        assert second.exit_code == 0, second.output
        assert tree(other) == tree(corpus)
        [version] = _observations(other)["versions"]
        assert version["observation_count"] == 1
        assert version["observed_at_last"] == "2026-08-19T15:17:23Z"

    def test_a_then_b_then_a_is_committed_as_v1_v2_v3(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)

        subjects = _backfill_all(corpus, a)

        [record] = _manifest(corpus).values()
        slug = record["slug"]
        assert subjects == [f"promote(lokal-forskrift): {AUTHORITY}/{slug} v{n}" for n in (1, 2, 3)]
        assert record["version"] == 3
        log = git(corpus, "log", "--format=%s", "-n", "3").splitlines()
        assert log == list(reversed(subjects))

    def test_the_intervals_match_the_log(self, root: Path, corpus: Path, tmp_path: Path) -> None:
        a, b = _aba(root, tmp_path)

        _backfill_all(corpus, a)

        versions = _observations(corpus)["versions"]
        intervals = [
            (v["observed_at_first"], v["observed_at_last"], v["observation_count"])
            for v in versions
        ]
        assert intervals == [
            ("2026-08-19T15:17:23Z", "2026-08-20T15:17:23Z", 2),
            ("2026-08-21T15:17:23Z", "2026-08-21T15:17:23Z", 1),
            ("2026-08-22T15:17:23Z", "2026-08-22T15:17:23Z", 1),
        ]
        assert [v["source_sha256s"] for v in versions] == [[a], [b], [a]]

    def test_commit_dates_are_promotion_time_never_observed_at(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)

        _backfill_all(corpus, a)

        dates = git(corpus, "log", "--format=%cI", "-n", "3").splitlines()
        observed = {v["observed_at_first"][:10] for v in _observations(corpus)["versions"]}
        assert not {d[:10] for d in dates} & observed

    def test_the_first_run_prints_every_further_step_in_order(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        out = result.output
        assert out.index(" v1'") < out.index(" v2'") < out.index(" v3'")
        assert out.count("lovspor promote backfill --authority") == 2
        assert "Holds by reason: none" in out

    def test_history_reads_the_three_commits_newest_first_as_updated_updated_added(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        _backfill_all(corpus, a)

        result = invoke("promote", "history", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        [path] = (corpus / LOCAL / AUTHORITY / "history").glob("*.json")
        events = json.loads(path.read_text(encoding="utf-8"))["events"]
        assert [e["type"] for e in events] == ["updated", "updated", "added"]

    def test_a_finished_backfill_rerun_writes_nothing_and_records_nothing(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        _backfill_all(corpus, a)
        log_before = (root / DECISIONS_FILENAME).read_bytes()
        before = tree(corpus)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0
        assert tree(corpus) == before
        assert git(corpus, "status", "--porcelain") == ""
        assert (root / DECISIONS_FILENAME).read_bytes() == log_before

    def test_two_checkouts_get_byte_identical_files(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        other = make_corpus(tmp_path / "other")

        _backfill_all(corpus, a)
        _backfill_all(other, a)

        assert tree(corpus) == tree(other)


class TestHolds:
    @pytest.mark.parametrize(
        "decision, reason", [("reject", "rejected"), ("hold", "held_by_reviewer")]
    )
    def test_a_later_review_revokes_approval_without_writing(
        self, root: Path, corpus: Path, tmp_path: Path, decision: str, reason: str
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path)
        _approve(a, tmp_path, decision=decision)
        before = tree(corpus)
        log_before = (root / DECISIONS_FILENAME).read_bytes()

        result = promote("backfill", a, corpus)

        assert result.exit_code == HELD_EXIT_CODE, result.output
        assert f"Holds by reason: {reason}: 1" in result.output
        assert tree(corpus) == before
        assert (root / DECISIONS_FILENAME).read_bytes() == log_before

    def test_reapproval_replaces_a_rejection(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        _approve(a, tmp_path, decision="reject")
        _approve(a, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert "Holds by reason: none" in result.output
        [record] = _manifest(corpus).values()
        assert record["version"] == 1

    def test_approval_of_a_later_blob_covers_the_same_text_run(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        first = store(root, html_page(updated="Sist oppdatert 01.09.2026"))
        later = store(
            root, html_page(updated="Sist oppdatert 02.09.2026"), observed_at=FIRST_SEEN + DAY
        )
        _approve(later, tmp_path)

        result = promote("backfill", first, corpus)

        assert result.exit_code == 0, result.output
        [version] = _observations(corpus)["versions"]
        assert version["source_sha256s"] == [first, later]
        assert version["observation_count"] == 2

    def test_approval_at_another_url_does_not_cover_the_primary(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store(root, html_page())
        copy_url = "https://eksempel.kommune.invalid/kopi/renovasjon"
        store(root, html_page(), url=copy_url, observed_at=FIRST_SEEN + DAY)
        result = approve(copy_url, Decision().write(tmp_path))
        assert result.exit_code == 0, result.output
        before = tree(corpus)

        result = promote("backfill", PAGE_URL, corpus)

        assert result.exit_code == HELD_EXIT_CODE, result.output
        assert "Holds by reason: not_approved: 1" in result.output
        assert tree(corpus) == before

    def test_an_unapproved_version_is_held_and_stops_every_later_one(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        _approve(a, tmp_path)
        _commit_printed(corpus, promote("backfill", a, corpus).output)

        result = promote("backfill", a, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert "Holds by reason: after_earlier_hold: 1, not_approved: 1" in result.output
        assert _manifest(corpus)[next(iter(_manifest(corpus)))]["version"] == 1

    def test_a_rejected_text_is_held_as_rejected(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        b = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)
        _approve(b, tmp_path, decision="reject")

        result = promote("backfill-preview", a, corpus)

        assert "v2  2026-08-20T15:17:23Z .. 2026-08-20T15:17:23Z  (1 observations)  rejected" in (
            result.output
        )

    def test_an_approval_given_before_the_text_came_back_does_not_cover_it(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        b = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)
        _approve(b, tmp_path)
        store(root, html_page(), observed_at=datetime.now(UTC) + DAY)

        result = promote("backfill-preview", a, corpus)

        assert "Holds by reason: approval_stale: 1" in result.output
        assert "before this version was first observed" in promote("backfill", a, corpus).output

    def test_an_approval_under_another_extractor_does_not_cover_the_text(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = store(root, html_page())
        with monkeypatch.context() as patched:
            patched.setattr("lovspor.promotion.commands.EXTRACTOR_VERSION", EXTRACTOR_VERSION - 1)
            _approve(a, tmp_path)

        result = promote("backfill-preview", a, corpus)

        assert "v1" in result.output
        assert "Holds by reason: approval_stale: 1" in result.output
        assert f"approved under extractor {EXTRACTOR_VERSION - 1}" in result.output

    def test_a_held_text_is_held_by_its_extraction_reason(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        store(root, EMPTY_PAGE, observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)

        result = promote("backfill-preview", a, corpus)

        assert "Holds by reason: extraction:empty_text: 1" in result.output

    def test_a_text_named_as_another_document_is_held(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        b = store(root, html_page(RETITLED), observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)
        _approve(b, tmp_path)
        _commit_printed(corpus, promote("backfill", a, corpus).output)
        before = tree(corpus)

        result = promote("backfill", a, corpus)

        assert result.exit_code == HELD_EXIT_CODE
        assert "Held: v2 identity_changed" in result.output
        assert tree(corpus) == before

    def test_a_tombstoned_blob_is_excluded_and_listed(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())
        gone = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(), observed_at=FIRST_SEEN + 2 * DAY)
        _approve(a, tmp_path)
        removal = Tombstone(
            sha256=gone, removed_at=FIRST_SEEN + 3 * DAY, basis="privacy", authorised_by="owner"
        )
        ObservationLog(ObservatoryRoot(root, [])).append(removal)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert f"excluded: 2026-08-20T15:17:23Z {gone} (tombstoned)" in result.output
        observations = _observations(corpus)
        assert [e["sha256"] for e in observations["excluded"]] == [gone]
        assert len(observations["versions"]) == 1


class TestRefusals:
    def test_a_corpus_whose_v1_is_not_the_logs_v1_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        b = store(root, html_page(CHANGED))
        a = store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)
        assert promote("local", a, corpus).exit_code == 0
        _approve(b, tmp_path)
        before = tree(corpus)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 1
        assert "does not match the log" in result.output
        assert tree(corpus) == before


class TestPreview:
    def test_writes_nothing_and_names_the_next_version(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        before = tree(corpus)
        log_before = (root / DECISIONS_FILENAME).read_bytes()

        result = promote("backfill-preview", a, corpus)

        assert result.exit_code == 0, result.output
        assert "Next: v1 -> lokale-forskrifter/0301/" in result.output
        assert "  v3  2026-08-22T15:17:23Z" in result.output
        assert tree(corpus) == before
        assert (root / DECISIONS_FILENAME).read_bytes() == log_before


class TestObserveRefresh:
    def test_a_new_text_waits_for_backfill_and_only_observations_are_written(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        self._promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
        before = tree(corpus)
        log_before = (root / DECISIONS_FILENAME).read_bytes()

        result = invoke("promote", "observe", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        after = tree(corpus)
        changed = {
            path for path in before.keys() | after.keys() if before.get(path) != after.get(path)
        }
        assert len(changed) == 1
        [path] = changed
        assert path.startswith(f"{LOCAL}/{AUTHORITY}/observations/")
        assert path.endswith(".json")
        [version] = _observations(corpus)["versions"]
        assert version["observed_at_last"] == "2026-08-20T15:17:23Z"
        assert version["observation_count"] == 2
        assert version["version"] == 1
        assert (root / DECISIONS_FILENAME).read_bytes() == log_before

    def _promoted(self, root: Path, corpus: Path, tmp_path: Path) -> str:
        a = store(root, html_page())
        _approve(a, tmp_path)
        _commit_printed(corpus, promote("backfill", a, corpus).output)
        return a

    def test_a_later_observation_extends_the_interval_without_a_new_version(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        self._promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + 7 * DAY)
        markdown_before = {p: d for p, d in tree(corpus).items() if p.endswith(".md")}

        result = invoke("promote", "observe", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        [version] = _observations(corpus)["versions"]
        assert version["observed_at_last"] == "2026-08-26T15:17:23Z"
        assert version["observation_count"] == 2
        assert "observe: refresh observation intervals (1 documents)" in result.output
        assert {p: d for p, d in tree(corpus).items() if p.endswith(".md")} == markdown_before

    def test_a_rerun_with_nothing_new_writes_nothing(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        self._promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + 7 * DAY)
        invoke("promote", "observe", "--corpus", str(corpus))
        before = tree(corpus)

        result = invoke("promote", "observe", "--corpus", str(corpus))

        assert "Observation intervals are current" in result.output
        assert tree(corpus) == before

    def test_the_observe_commit_is_no_history_event(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        self._promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + 7 * DAY)
        _commit_printed(corpus, invoke("promote", "observe", "--corpus", str(corpus)).output)

        invoke("promote", "history", "--corpus", str(corpus))

        [path] = (corpus / LOCAL / AUTHORITY / "history").glob("*.json")
        events = json.loads(path.read_text(encoding="utf-8"))["events"]
        assert [e["type"] for e in events] == ["added"]

    def test_a_document_the_log_does_not_reproduce_is_skipped_with_the_reason(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        store(root, html_page(CHANGED))
        a = store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        _approve(a, tmp_path)
        promote("local", a, corpus)
        before = tree(corpus)

        result = invoke("promote", "observe", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        assert "skipped: promoted version 1 does not match the log" in result.output
        assert tree(corpus) == before

    def test_another_authority_is_left_alone(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        self._promoted(root, corpus, tmp_path)
        store(root, html_page(), observed_at=FIRST_SEEN + 7 * DAY)
        before = tree(corpus)

        result = invoke("promote", "observe", "--corpus", str(corpus), "--authority", "4601")

        assert result.exit_code == 0, result.output
        assert tree(corpus) == before


class TestIdentityAndCorpusChecks:
    def test_a_first_version_without_an_identity_is_held_and_recorded(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        unnamed = (REGULATION_LINES[0], *REGULATION_LINES[2:])
        a = store(root, html_page(unnamed))
        _approve(a, tmp_path)

        preview = promote("backfill-preview", a, corpus)
        result = promote("backfill", a, corpus)

        assert "Held: v1 identity:no_identity" in preview.output
        assert result.exit_code == HELD_EXIT_CODE
        lines = (root / DECISIONS_FILENAME).read_text(encoding="utf-8").splitlines()
        held = [json.loads(line) for line in lines if '"kind":"held"' in line]
        assert [(h["stage"], h["reason"]) for h in held] == [("identity", "no_identity")]

    def test_nothing_approved_previews_nothing_to_write(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a = store(root, html_page())

        preview = promote("backfill-preview", a, corpus)
        result = promote("backfill", a, corpus)

        assert "Next: nothing to write." in preview.output
        assert "Holds by reason: not_approved: 1" in preview.output
        assert result.exit_code == HELD_EXIT_CODE

    def test_an_unreadable_observations_file_refuses_the_run(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _aba(root, tmp_path)
        _commit_printed(corpus, promote("backfill", a, corpus).output)
        [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
        path.unlink()

        result = promote("backfill", a, corpus)

        assert result.exit_code == 1
        assert "does not read" in result.output
