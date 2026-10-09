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
from datetime import date, timedelta
from pathlib import Path

import pytest
from typer.testing import Result

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion import extract
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import DECISIONS_FILENAME
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.fields import read_regulation
from lovspor.promotion.migrate import Published, published, published_klass_version
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    REVIEWER,
    REVIEWER_ROLE,
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
    @pytest.mark.parametrize("dry_run", [False, True])
    def test_a_refused_document_does_not_prevent_other_documents_migrating(
        self,
        root: Path,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        dry_run: bool,
    ) -> None:
        """operations.md: report refusals, migrate the others, and exit 1."""
        refused_blob = _promoted_once(root, corpus, tmp_path, monkeypatch)
        refused_id = next(iter(_manifest(corpus)))
        other = store(root, html_page(OTHER_LINES), url=OTHER_URL)
        with _earlier_engine(monkeypatch):
            _approve(other, tmp_path)
            promoted = promote("local", other, corpus)
            assert promoted.exit_code == 0, promoted.output
            _commit_printed(corpus, promoted.output)
        # The first document's standing decision becomes a hold; the second's approval
        # carries, its bytes being identical (owner decision, 2026-10-08).
        held = approve(refused_blob, Decision(decision="hold").write(tmp_path))
        assert held.exit_code == 0, held.output
        records_before = _manifest(corpus)
        before, log_before = tree(corpus), _log(root)
        head = git(corpus, "rev-parse", "HEAD")

        result = _migrate(corpus, *(["--dry-run"] if dry_run else []))

        assert result.exit_code == 1, result.output
        assert f"Refused: {refused_id}:" in result.output
        assert "standing decision is hold" in result.output
        assert git(corpus, "rev-parse", "HEAD") == head
        if dry_run:
            assert "Dry run: nothing written, nothing recorded" in result.output
            assert f'+      "extractor_version": {EXTRACTOR_VERSION},' in result.output
            assert tree(corpus) == before
            assert _log(root) == log_before
        else:
            records_after = _manifest(corpus)
            assert records_after[refused_id] == records_before[refused_id]
            [migrated_id] = records_before.keys() - {refused_id}
            assert records_after[migrated_id] == {
                **records_before[migrated_id],
                "extractor_version": EXTRACTOR_VERSION,
            }
            slug = records_before[migrated_id]["slug"]
            after = tree(corpus)
            assert {p for p in after if after[p] != before.get(p)} == {
                f"{LOCAL}/manifest.json",
                f"{LOCAL}/{AUTHORITY}/observations/{slug}.json",
            }
            assert _markdown(after) == _markdown(before)
            assert _log(root).startswith(log_before)
            carry, record = _log(root)[len(log_before) :].decode().splitlines()
            assert json.loads(carry)["kind"] == "carried"
            outcome = json.loads(record)
            assert outcome["kind"] == "migrated"
            assert outcome["doc_id"] == migrated_id
            assert outcome["artifact"]["sha256"] == other
            assert outcome["artifact"]["sha256"] != refused_blob
            assert outcome["written"] == [
                f"{LOCAL}/{AUTHORITY}/observations/{slug}.json",
                f"{LOCAL}/manifest.json",
            ]

    def test_a_standing_hold_refuses_without_writing_or_recording(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """operations.md explicitly refuses a standing hold as well as reject."""
        a = _promoted_once(root, corpus, tmp_path, monkeypatch)
        result = approve(a, Decision(decision="hold").write(tmp_path))
        assert result.exit_code == 0, result.output

        refused = _refused_untouched(root, corpus)

        assert "standing decision is hold" in refused.output

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

    def test_without_an_approval_at_the_running_extractor_identical_bytes_carry(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Owner decision, 2026-10-08: identical bytes need no re-approval; the carry says so."""
        _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus)

        assert result.exit_code == 0, result.output
        assert _markdown(tree(corpus)) == _markdown(before)
        kinds = [json.loads(line)["kind"] for line in _log(root)[len(log_before) :].splitlines()]
        assert kinds == ["carried", "carried", "carried", "migrated"]

    def test_an_earlier_version_rejected_since_refuses(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        a, b = _promoted_earlier(root, corpus, tmp_path, monkeypatch)
        _approve(a, tmp_path)
        rejected = approve(b, Decision(decision="reject").write(tmp_path))
        assert rejected.exit_code == 0, rejected.output
        before, log_before = tree(corpus), _log(root)

        result = _migrate_one(corpus)

        assert result.exit_code == 1
        assert f"v2 (approve one of {b})" in result.output
        assert "standing decision is reject" in result.output
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


def _promoted_once(
    root: Path, corpus: Path, tmp_path: Path, mp: pytest.MonkeyPatch, reads: object = None
) -> str:
    """One capture, promoted with `promote local` by the earlier engine reading it as ``reads``."""
    a = store(root, html_page())
    with _earlier_engine(mp, reads):  # type: ignore[arg-type]
        _approve(a, tmp_path)
        result = promote("local", a, corpus)
        assert result.exit_code == 0, result.output
        _commit_printed(corpus, result.output)
    return a


def _refused_untouched(root: Path, corpus: Path) -> Result:
    before, log_before = tree(corpus), _log(root)
    result = _migrate_one(corpus)
    assert result.exit_code == 1, result.output
    assert tree(corpus) == before
    assert _log(root) == log_before
    return result


class TestRefusalsOfWhatIsNotAMigration:
    def test_a_rendering_that_would_change_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def other_ikraft_text(lines: tuple[str, ...]) -> object:
            regulation, fields = read_regulation(lines)  # type: ignore[misc]
            return regulation, fields.model_copy(update={"ikraft_text": "straks"})

        a = _promoted_once(root, corpus, tmp_path, monkeypatch, other_ikraft_text)
        _approve(a, tmp_path)

        result = _refused_untouched(root, corpus)

        assert "its rendering changes though its text does not" in result.output

    def test_a_text_named_by_another_id_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def other_date(lines: tuple[str, ...]) -> object:
            regulation, fields = read_regulation(lines)  # type: ignore[misc]
            return regulation.model_copy(update={"vedtaksdato": date(2018, 1, 1)}), fields

        a = _promoted_once(root, corpus, tmp_path, monkeypatch, other_date)
        _approve(a, tmp_path)

        result = _refused_untouched(root, corpus)

        assert "names its text otherwise" in result.output

    def test_a_source_the_running_extractor_holds_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def always_the_regulation(lines: tuple[str, ...]) -> object:
            return read_regulation(REGULATION_LINES)

        # No section heading: the running extractor holds it (no body).
        a = store(root, html_page(tuple(x for x in REGULATION_LINES if not x.startswith("§"))))
        with _earlier_engine(monkeypatch, always_the_regulation):
            _approve(a, tmp_path)
            _commit_printed(corpus, promote("local", a, corpus).output)

        result = _refused_untouched(root, corpus)

        assert f"extractor v{EXTRACTOR_VERSION} holds its source" in result.output

    def test_a_version_the_log_does_not_reproduce_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store(root, html_page(CHANGED), observed_at=FIRST_SEEN)
        b = store(root, html_page(), observed_at=FIRST_SEEN + DAY)
        with _earlier_engine(monkeypatch):
            _approve(b, tmp_path)
            _commit_printed(corpus, promote("local", b, corpus).output)
        _approve(b, tmp_path)

        result = _refused_untouched(root, corpus)

        assert "v1 at extractor" in result.output
        assert "another text; that is not a migration" in result.output

    def test_a_withdrawn_document_is_refused_and_skipped_in_a_full_run(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _promoted_once(root, corpus, tmp_path, monkeypatch)
        slug = str(_record(corpus)["slug"])
        withdrawal = tmp_path / "withdrawal.json"
        withdrawal.write_text(
            json.dumps(
                {
                    "decision": "withdraw",
                    "removed_reason": "withdrawn_misclassified",
                    "decided_by": REVIEWER,
                    "reviewer_role": REVIEWER_ROLE,
                    "reason": "Siden er en høring, ikke en vedtatt forskrift.",
                }
            ),
            encoding="utf-8",
        )
        withdrawn = invoke(
            "promote",
            "withdraw",
            "--authority",
            AUTHORITY,
            "--slug",
            slug,
            "--corpus",
            str(corpus),
            "--decision",
            str(withdrawal),
        )
        assert withdrawn.exit_code == 0, withdrawn.output
        _commit_printed(corpus, withdrawn.output)

        one = _refused_untouched(root, corpus)
        every = _migrate(corpus)

        assert "is withdrawn; a withdrawn document is never migrated" in one.output
        assert every.exit_code == 0, every.output
        assert "Nothing to migrate" in every.output


class TestPublishedDocument:
    def _published(self, corpus: Path) -> Published:
        [(doc_id, record)] = CorpusCheckout(corpus, []).local_manifest().documents.items()
        return published(CorpusCheckout(corpus, []), doc_id, record)

    def test_the_klass_version_is_read_from_the_front_matter(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _promoted_once(root, corpus, tmp_path, monkeypatch)

        document = self._published(corpus)

        assert published_klass_version(document) == "131-2024"
        bare = document.model_copy(update={"markdown": '---\nid: "x"\n---\n'})
        with pytest.raises(PromotionRefusedError, match="names no klass_version"):
            published_klass_version(bare)

    def test_observations_that_do_not_read_are_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _promoted_once(root, corpus, tmp_path, monkeypatch)
        [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
        path.write_text("{}", encoding="utf-8")

        with pytest.raises(PromotionRefusedError, match="do not read"):
            self._published(corpus)

    def test_a_rendering_that_is_not_utf8_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A decode error is a named refusal, not a traceback (Codex test on PR #594)."""
        _promoted_once(root, corpus, tmp_path, monkeypatch)
        [(_, record)] = CorpusCheckout(corpus, []).local_manifest().documents.items()
        (corpus / record.markdown_path).write_bytes(b"\xff")

        with pytest.raises(PromotionRefusedError, match="do not read"):
            self._published(corpus)


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
