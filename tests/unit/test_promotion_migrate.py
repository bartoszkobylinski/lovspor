"""``lovspor promote migrate`` end to end (ADR-0016 4e, issue #588).

A document promoted under an earlier extractor whose text the running
extractor reads byte-identically is migrated in place: only the version
metadata moves, the Markdown never does. Every state is reached the way an
operator reaches it — captures by the observatory's writer, decisions by
``promote approve``, versions by ``promote backfill``, commits by ``git`` —
except the earlier engine itself, which is the running one with its
``EXTRACTOR_VERSION`` set one lower (the code of an earlier release is not
importable here). The tests read ``EXTRACTOR_VERSION``, never a number.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import Result

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion import extract
from lovspor.promotion.decisions import DECISIONS_FILENAME
from lovspor.promotion.extract import EXTRACTOR_VERSION
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
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
OLD = EXTRACTOR_VERSION - 1
DAY = timedelta(days=1)
CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
OTHER_URL = "https://eksempel.kommune.invalid/forskrifter/skolerute"
OTHER_LINES = (
    "Forskrift om skolerute, Eksempel kommune",
    "Vedtatt av kommunestyret i møte 14.03.2021 med hjemmel i lov 17. juli 1998 nr. 61 om "
    "grunnskolen og den vidaregåande opplæringa (opplæringslova) § 3-2.",
    "§ 1 Formål",
    "Forskriften fastsetter skoleruten for grunnskolene i kommunen.",
    "§ 2 Ikrafttredelse",
    "Forskriften trer i kraft 1. august 2021.",
)
_VERSIONED = ("extract", "plan", "commands", "backfill", "intervals")


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


@contextmanager
def _earlier_engine(
    monkeypatch: pytest.MonkeyPatch, reads: Callable[..., object] | None = None
) -> Iterator[None]:
    """The engine of the previous release: one extractor version lower.

    ``reads`` stands in for that release's reading of a page, when it read
    the same bytes as another text.
    """
    with monkeypatch.context() as patched:
        for module in _VERSIONED:
            patched.setattr(f"lovspor.promotion.{module}.EXTRACTOR_VERSION", OLD)
        if reads is not None:
            patched.setattr(extract, "read_regulation", reads)
        yield


def _approve(sha256: str, tmp_path: Path) -> None:
    result = approve(sha256, Decision().write(tmp_path))
    assert result.exit_code == 0, result.output


def _commit_printed(corpus: Path, output: str) -> str:
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("  git -C") and " commit -m " in line
    )
    subject = line.split(" commit -m ", 1)[1].strip("'")
    git(corpus, "add", "--", LOCAL)
    git(corpus, "commit", "-q", "-m", subject)
    return subject


def _backfill_all(corpus: Path, sha256: str) -> None:
    for _ in range(5):
        result = promote("backfill", sha256, corpus)
        assert result.exit_code == 0, result.output
        if "Nothing to write" in result.output:
            return
        _commit_printed(corpus, result.output)
    raise AssertionError("backfill did not finish")


def _promoted_earlier(
    root: Path, corpus: Path, tmp_path: Path, mp: pytest.MonkeyPatch
) -> tuple[str, str]:
    """A on days 0-1, B on day 2, A on day 3, backfilled as v1-v3 under the earlier engine."""
    a = store(root, html_page(), observed_at=FIRST_SEEN)
    store(root, html_page(), observed_at=FIRST_SEEN + DAY)
    b = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + 2 * DAY)
    store(root, html_page(), observed_at=FIRST_SEEN + 3 * DAY)
    with _earlier_engine(mp):
        _approve(a, tmp_path)
        _approve(b, tmp_path)
        _backfill_all(corpus, a)
    return a, b


def _reapprove(blobs: tuple[str, ...], tmp_path: Path) -> None:
    """The owner's re-approval of every version's text at the running extractor."""
    for sha256 in blobs:
        _approve(sha256, tmp_path)


def _manifest(corpus: Path) -> dict[str, dict[str, object]]:
    return json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))["documents"]


def _record(corpus: Path) -> dict[str, object]:
    [record] = _manifest(corpus).values()
    return record


def _observations(corpus: Path) -> dict[str, object]:
    [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _migrate(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "migrate", "--corpus", str(corpus), *extra)


def _migrate_one(corpus: Path, *extra: str) -> Result:
    slug = str(_record(corpus)["slug"])
    return _migrate(corpus, "--authority", AUTHORITY, "--slug", slug, *extra)


def _log(root: Path) -> bytes:
    return (root / DECISIONS_FILENAME).read_bytes()


def _markdown(snapshot: dict[str, bytes]) -> dict[str, bytes]:
    return {path: data for path, data in snapshot.items() if path.endswith(".md")}


class TestMigratingAnUnchangedDocument:
    def test_only_the_version_fields_move(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        record_before, observations_before = _record(corpus), _observations(corpus)
        before = tree(corpus)
        _reapprove(blobs, tmp_path)

        result = _migrate_one(corpus)

        assert result.exit_code == 0, result.output
        after = tree(corpus)
        assert _markdown(after) == _markdown(before)
        assert sorted(p for p in after if after[p] != before.get(p)) == sorted(
            [
                f"{LOCAL}/manifest.json",
                f"{LOCAL}/{AUTHORITY}/observations/{record_before['slug']}.json",
            ]
        )
        assert _record(corpus) == {**record_before, "extractor_version": EXTRACTOR_VERSION}
        versions = observations_before["versions"]
        assert isinstance(versions, list)
        assert [v["promotion"]["extractor_version"] for v in versions] == [OLD, OLD, OLD]
        for version in versions:
            version["promotion"]["extractor_version"] = EXTRACTOR_VERSION
        assert _observations(corpus) == observations_before

    def test_prints_the_migration_commit_and_never_commits(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        head = git(corpus, "rev-parse", "HEAD")
        _reapprove(blobs, tmp_path)

        result = _migrate_one(corpus)

        slug = _record(corpus)["slug"]
        subject = (
            f"migration(lokal-forskrift): {AUTHORITY}/{slug} extractor v{OLD}→v{EXTRACTOR_VERSION}"
        )
        assert subject in result.output
        assert f"git -C {corpus} add -- lokale-forskrifter" in result.output
        assert git(corpus, "rev-parse", "HEAD") == head

    def test_records_the_migration_in_the_decision_log(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        _reapprove(blobs, tmp_path)

        result = _migrate_one(corpus)

        assert result.exit_code == 0, result.output
        last = json.loads(_log(root).decode().splitlines()[-1])
        assert last["kind"] == "migrated"
        assert last["artifact"]["sha256"] == blobs[0]
        assert (last["from_extractor_version"], last["to_extractor_version"]) == (
            OLD,
            EXTRACTOR_VERSION,
        )
        assert last["version"] == 3
        slug = _record(corpus)["slug"]
        assert last["written"] == [
            f"{LOCAL}/{AUTHORITY}/observations/{slug}.json",
            f"{LOCAL}/manifest.json",
        ]

    def test_a_rerun_after_the_commit_writes_and_records_nothing(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        _reapprove(blobs, tmp_path)
        _commit_printed(corpus, _migrate_one(corpus).output)
        before, log_before = tree(corpus), _log(root)

        rerun = _migrate_one(corpus)

        assert rerun.exit_code == 0, rerun.output
        assert f"already at extractor v{EXTRACTOR_VERSION}" in rerun.output
        assert tree(corpus) == before
        assert git(corpus, "status", "--porcelain") == ""
        assert _log(root) == log_before

    def test_writes_a_missing_evidence_sidecar(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        slug = _record(corpus)["slug"]
        evidence = corpus / LOCAL / AUTHORITY / "evidence" / f"{slug}.json"
        # A document promoted before the sidecar existed (#577) has none.
        git(corpus, "rm", "-q", "--", str(evidence))
        git(corpus, "commit", "-q", "-m", "test: a promotion from before #577")
        _reapprove(blobs, tmp_path)

        result = _migrate_one(corpus)

        assert result.exit_code == 0, result.output
        written = json.loads(evidence.read_text(encoding="utf-8"))
        assert (written["doc_id"], written["version"]) == (next(iter(_manifest(corpus))), 3)

    def test_dry_run_prints_the_diff_and_writes_nothing(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        _reapprove(blobs, tmp_path)
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus, "--dry-run")

        assert result.exit_code == 0, result.output
        assert f'-      "extractor_version": {OLD},' in result.output
        assert f'+      "extractor_version": {EXTRACTOR_VERSION},' in result.output
        assert "nothing written" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_without_a_document_every_current_document_is_migrated(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        other = store(root, html_page(OTHER_LINES), url=OTHER_URL)
        with _earlier_engine(monkeypatch):
            _approve(other, tmp_path)
            _commit_printed(corpus, promote("local", other, corpus).output)
        _reapprove((*blobs, other), tmp_path)

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        assert f"2 documents to extractor v{EXTRACTOR_VERSION}" in result.output
        assert {r["extractor_version"] for r in _manifest(corpus).values()} == {EXTRACTOR_VERSION}


class TestRefusals:
    def test_a_text_the_running_extractor_reads_differently_is_a_new_version(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a = store(root, html_page())

        def dropped_last_line(lines: tuple[str, ...]) -> object:
            return reader(lines[:-1])

        reader = extract.read_regulation
        with _earlier_engine(monkeypatch, dropped_last_line):
            _approve(a, tmp_path)
            _commit_printed(corpus, promote("local", a, corpus).output)
        _approve(a, tmp_path)
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus)

        assert result.exit_code == 1
        assert "new version" in result.output
        assert "promote backfill" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_without_an_approval_at_the_running_extractor_nothing_moves(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus)

        assert result.exit_code == 1
        assert "another text or extractor" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_an_earlier_version_not_approved_again_refuses(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        _approve(a, tmp_path)
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus)

        assert result.exit_code == 1
        assert f"v2 (approve one of {b})" in result.output
        assert "another text or extractor" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_a_standing_reject_refuses(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        result = approve(blobs[0], Decision(decision="reject").write(tmp_path))
        assert result.exit_code == 0, result.output
        before = tree(corpus)

        refused = _migrate_one(corpus)

        assert refused.exit_code == 1
        assert "standing decision is reject" in refused.output
        assert tree(corpus) == before

    def test_an_authority_without_a_slug_is_refused(self, root: Path, corpus: Path) -> None:
        result = _migrate(corpus, "--authority", AUTHORITY)

        assert result.exit_code == 1
        assert "--authority and --slug" in result.output

    def test_an_unknown_document_is_refused(self, root: Path, corpus: Path) -> None:
        result = _migrate(corpus, "--authority", AUTHORITY, "--slug", "finnes-ikke")

        assert result.exit_code == 1
        assert f"no local regulation {AUTHORITY}/finnes-ikke" in result.output


class TestTheMigratedStateIsReachableThroughSupportedInterfacesOnly:
    """Can a promoted document at the running extractor be reached using only commands?"""

    def test_approve_migrate_commit_reaches_a_document_observe_refreshes_again(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blobs = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        stuck = invoke("promote", "observe", "--corpus", str(corpus))
        assert "that is a migration, not a refresh" in stuck.output
        derived = invoke("promote", "history", "--corpus", str(corpus))
        _commit_printed(corpus, derived.output)
        _reapprove(blobs, tmp_path)

        subject = _commit_printed(corpus, _migrate_one(corpus).output)

        assert git(corpus, "log", "-1", "--format=%s").strip() == subject
        assert _record(corpus)["extractor_version"] == EXTRACTOR_VERSION
        observed = invoke("promote", "observe", "--corpus", str(corpus))
        assert observed.exit_code == 0, observed.output
        assert "skipped" not in observed.output
        unchanged = promote("local", blobs[0], corpus)
        assert unchanged.exit_code == 0, unchanged.output
        assert "Unchanged" in unchanged.output
        history = invoke("promote", "history", "--corpus", str(corpus))
        assert history.exit_code == 0, history.output
        assert "History is current" in history.output
