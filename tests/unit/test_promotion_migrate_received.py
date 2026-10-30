"""``promote migrate`` moving a received document to the running extractor (#643).

A version received under offentleglova names its source by ``innsyn_request``
and ``source_sha256``, never by a URL (ADR-0016 A5). ``migrate`` re-reads it
from the innsyn workspace, the read ``promote received preview`` makes, and
carries the owner's standing approval exactly as it does for an observed
document: only when the rendering is byte-identical (owner decision
2026-10-08). Any byte difference still needs a fresh approval.

Every state is reached the way an operator reaches it (the innsyn commands,
``promote received approve`` and ``local``, ``git``, ``promote migrate``),
except the earlier engine, which is the running one with ``EXTRACTOR_VERSION``
set one lower. The tests read ``EXTRACTOR_VERSION``, never a number.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import Result
from pytest_httpx import HTTPXMock

from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.extract import EXTRACTOR_VERSION
from tests.unit.innsyn_support import FIXTURES, fetched
from tests.unit.promotion_cli_fixtures import Decision, git, invoke, make_corpus, tree
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page
from tests.unit.test_promotion_received_commands import (
    FJALER,
    LOCAL,
    REQUESTED,
    _approve,
    _innsyn,
    _received,
)

OLD = EXTRACTOR_VERSION - 1
_VERSIONED = ("extract", "plan", "commands", "received_commands")
LATER = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. mars 2020.")
FIRST_AT, SECOND_AT = "2026-10-07T12:45:06Z", "2026-10-08T09:00:00Z"


@contextmanager
def _earlier_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with monkeypatch.context() as patched:
        for module in _VERSIONED:
            patched.setattr(f"lovspor.promotion.{module}.EXTRACTOR_VERSION", OLD)
        yield


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _innsyn(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    monkeypatch.setenv(ENV_INNSYN_DIR, str(target))
    return target


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "observatory"
    target.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(target))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return target


@pytest.fixture
def corpus(tmp_path: Path, root: Path) -> Path:
    return make_corpus(tmp_path)


def _receive(workspace: Path, lines: tuple[str, ...], at: str) -> str:
    """Record ``lines`` as an HTML answer on Fjaler's request at ``at``, classified; its SHA-256."""
    folder = workspace.parent / "mail"
    folder.mkdir(exist_ok=True)
    path = folder / f"svar-{at[:10]}.html"
    path.write_bytes(html_page(lines))
    output = _innsyn(
        workspace, "record-received", "--request", FJALER, "--file", str(path), "--at", at
    )
    sha256 = output.split("document ")[1].split()[0]
    _innsyn(
        workspace,
        "classify-received",
        *("--request", FJALER, "--document", sha256, "--kind", "regulation_text"),
        *("--answers", REQUESTED),
    )
    return sha256


def _promote_received(sha256: str, corpus: Path, tmp_path: Path) -> None:
    """Approve and promote ``sha256`` with ``promote received``, then commit it as printed."""
    assert _approve(sha256, tmp_path).exit_code == 0
    result = _received("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    git(corpus, "add", "--", LOCAL)
    git(corpus, "commit", "-q", "-m", f"promote(lokal-forskrift): {sha256[:12]}")


@pytest.fixture
def promoted(workspace: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A received text, approved and promoted by the earlier engine; its SHA-256."""
    with _earlier_engine(monkeypatch):
        sha256 = _receive(workspace, REGULATION_LINES, FIRST_AT)
        _promote_received(sha256, corpus, tmp_path)
    return sha256


def _migrate(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "migrate", "--corpus", str(corpus), *extra)


def _markdown_path(corpus: Path) -> Path:
    [path] = (corpus / LOCAL / "4646").glob("*.md")
    return path


def _versions(corpus: Path) -> list[dict[str, object]]:
    [path] = (corpus / LOCAL / "4646" / "observations").glob("*.json")
    versions = json.loads(path.read_text(encoding="utf-8"))["versions"]
    assert isinstance(versions, list)
    return versions


def _record(corpus: Path) -> dict[str, object]:
    manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    return record


def _log(root: Path) -> list[dict[str, object]]:
    lines = (root / "promotions.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


class TestMigratesAReceivedDocument:
    def test_a_fresh_approval_is_not_recorded_as_carried(
        self, promoted: str, corpus: Path, root: Path, tmp_path: Path
    ) -> None:
        assert _approve(promoted, tmp_path).exit_code == 0
        before = len(_log(root))
        markdown = _markdown_path(corpus).read_bytes()

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        assert _markdown_path(corpus).read_bytes() == markdown
        assert _record(corpus)["extractor_version"] == EXTRACTOR_VERSION
        [version] = _versions(corpus)
        promotion = version["promotion"]
        assert isinstance(promotion, dict)
        assert promotion["extractor_version"] == EXTRACTOR_VERSION
        assert promotion.get("approval_carried") is None
        [migrated] = _log(root)[before:]
        assert migrated["kind"] == "migrated"
        assert migrated["artifact"] == {
            "authority_id": "4646",
            "innsyn_request": FJALER,
            "sha256": promoted,
        }

    def test_the_audit_is_the_fresh_approvals_not_the_old_one(
        self, promoted: str, corpus: Path, root: Path, tmp_path: Path
    ) -> None:
        """#722: the re-approval's time, reason and role are published, not the first one's."""
        [old] = _versions(corpus)
        fresh = Decision(reason="Re-approved at the running extractor.", reviewer_role="editor")
        result = invoke(
            "promote",
            "received",
            "approve",
            *(
                "--request",
                FJALER,
                "--document",
                promoted,
                "--decision",
                str(fresh.write(tmp_path)),
            ),
        )
        assert result.exit_code == 0, result.output
        [*_, latest] = [r for r in _log(root) if r.get("kind") == "decision"]

        assert _migrate(corpus).exit_code == 0

        [version] = _versions(corpus)
        promotion, before = version["promotion"], old["promotion"]
        assert isinstance(promotion, dict)
        assert isinstance(before, dict)
        assert promotion["decided_at"] == latest["decided_at"]
        assert promotion["reason"] == "Re-approved at the running extractor."
        assert promotion["reviewed_by_role"] == "editor"
        assert promotion["observations_through"] == before["observations_through"]
        assert promotion["innsyn"] == before["innsyn"]

    def test_it_moves_the_version_metadata_and_carries_the_approval(
        self, promoted: str, corpus: Path, root: Path
    ) -> None:
        markdown = _markdown_path(corpus).read_bytes()
        before = len(_log(root))

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        assert _markdown_path(corpus).read_bytes() == markdown
        assert _record(corpus)["extractor_version"] == EXTRACTOR_VERSION
        [version] = _versions(corpus)
        promotion = version["promotion"]
        assert isinstance(promotion, dict)
        assert promotion["extractor_version"] == EXTRACTOR_VERSION
        assert promotion["approval_carried"]["approved_at_extractor"] == OLD
        assert promotion["approval_carried"]["carried_to_extractor"] == EXTRACTOR_VERSION
        carried, migrated = _log(root)[before:]
        received_key = {"authority_id": "4646", "innsyn_request": FJALER, "sha256": promoted}
        assert carried["kind"] == "carried"
        assert carried["artifact"] == received_key
        assert migrated["kind"] == "migrated"
        assert migrated["artifact"] == received_key
        assert f"carried to v{EXTRACTOR_VERSION}" in result.stdout
        assert "migrated extractor v" in result.stdout

    def test_received_refresh_then_accepts_the_carried_approval(
        self, promoted: str, corpus: Path
    ) -> None:
        assert _migrate(corpus).exit_code == 0
        result = invoke("promote", "received", "refresh", "--corpus", str(corpus))
        assert result.exit_code == 0, result.output
        assert "unchanged; its rendering is current" in result.stdout

    def test_a_rerun_after_the_commit_writes_nothing(self, promoted: str, corpus: Path) -> None:
        assert _migrate(corpus).exit_code == 0
        git(corpus, "add", "--", LOCAL)
        git(corpus, "commit", "-q", "-m", "migration")
        before = tree(corpus)
        result = _migrate(corpus)
        assert result.exit_code == 0, result.output
        assert f"already at extractor v{EXTRACTOR_VERSION}" in result.stdout
        assert tree(corpus) == before

    def test_a_dry_run_writes_and_records_nothing(
        self, promoted: str, corpus: Path, root: Path
    ) -> None:
        before, log = tree(corpus), _log(root)
        result = _migrate(corpus, "--dry-run")
        assert result.exit_code == 0, result.output
        assert f'"extractor_version": {EXTRACTOR_VERSION}' in result.stdout
        assert f"carried to v{EXTRACTOR_VERSION}" in result.stdout
        assert result.stdout.endswith("Dry run: nothing written, nothing recorded.\n")
        assert tree(corpus) == before
        assert _log(root) == log

    def test_earlier_received_versions_carry_on_their_published_bytes(
        self,
        workspace: Path,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _earlier_engine(monkeypatch):
            _promote_received(_receive(workspace, REGULATION_LINES, FIRST_AT), corpus, tmp_path)
            _promote_received(_receive(workspace, LATER, SECOND_AT), corpus, tmp_path)

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        audits = [version["promotion"] for version in _versions(corpus)]
        assert [a["extractor_version"] for a in audits] == [EXTRACTOR_VERSION] * 2  # type: ignore[index]
        assert all(a["approval_carried"] for a in audits)  # type: ignore[index]


class TestRefusals:
    @pytest.mark.parametrize("damage", ["missing", "corrupt"])
    def test_the_named_source_must_exist_and_match_its_hash(
        self, promoted: str, workspace: Path, corpus: Path, root: Path, damage: str
    ) -> None:
        blob = workspace / "received" / promoted[:2] / promoted
        if damage == "missing":
            blob.unlink()
        else:
            blob.write_bytes(html_page(LATER))
        before = _log(root)

        error = self._refused(corpus)

        expected = "missing or unreadable" if damage == "missing" else "does not hash to its name"
        assert expected in error
        assert _log(root) == before

    def test_a_hold_on_an_earlier_version_blocks_the_whole_migration(
        self,
        workspace: Path,
        corpus: Path,
        root: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _earlier_engine(monkeypatch):
            first = _receive(workspace, REGULATION_LINES, FIRST_AT)
            _promote_received(first, corpus, tmp_path)
            _promote_received(_receive(workspace, LATER, SECOND_AT), corpus, tmp_path)
        assert _approve(first, tmp_path, decision="hold").exit_code == 0
        before = _log(root)

        error = self._refused(corpus)

        assert "v1 (approve one of" in error
        assert "the standing decision is hold" in error
        assert _log(root) == before

    def _refused(self, corpus: Path) -> str:
        before = tree(corpus)
        result = _migrate(corpus)
        assert result.exit_code == 1, result.output
        assert "Traceback" not in result.output
        assert tree(corpus) == before
        assert "does not name its source" not in result.stderr
        return result.stderr

    def test_a_rendering_that_is_not_byte_identical(self, promoted: str, corpus: Path) -> None:
        path = _markdown_path(corpus)
        path.write_text(path.read_text(encoding="utf-8") + "\nEt tillegg.\n", encoding="utf-8")
        assert "its rendering changes though its text does not" in self._refused(corpus)

    def test_without_the_innsyn_workspace(
        self, promoted: str, corpus: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENV_INNSYN_DIR)
        assert ENV_INNSYN_DIR in self._refused(corpus)

    def test_a_standing_hold(self, promoted: str, corpus: Path, tmp_path: Path) -> None:
        assert _approve(promoted, tmp_path, decision="hold").exit_code == 0
        assert "the standing decision is hold" in self._refused(corpus)

    def test_an_earlier_version_whose_bytes_changed(
        self,
        workspace: Path,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        with _earlier_engine(monkeypatch):
            _promote_received(_receive(workspace, REGULATION_LINES, FIRST_AT), corpus, tmp_path)
            first = _markdown_path(corpus).read_text(encoding="utf-8")
            _promote_received(_receive(workspace, LATER, SECOND_AT), corpus, tmp_path)
        current = _markdown_path(corpus).read_text(encoding="utf-8")
        _markdown_path(corpus).write_text(first + "\nEt tillegg.\n", encoding="utf-8")
        git(corpus, "commit", "-q", "-am", "rewrite v1 history")
        _markdown_path(corpus).write_text(current, encoding="utf-8")
        git(corpus, "commit", "-q", "-am", "back to v2")
        assert "v1 (approve one of" in self._refused(corpus)
