"""``lovspor promote received refresh``: re-render an unchanged received version (#645).

End to end through the CLI, the way an operator reaches it: a repealed
Lovdata print is received, approved and promoted with ``promote received
local``, then its Markdown is put back the way the renderer wrote it before
#639 (no ``status``, no ``ikraft_end``). ``local`` cannot repair that — the
version is unchanged — and ``refresh`` must, without moving the version.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from click.testing import Result
from pytest_httpx import HTTPXMock

from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from tests.unit.innsyn_support import FIXTURES, fetched
from tests.unit.promotion_cli_fixtures import invoke, make_corpus, tree
from tests.unit.promotion_fixtures import minimal_pdf
from tests.unit.test_promotion_lovdata_validity import REPEALED_PRINT
from tests.unit.test_promotion_received_commands import (
    LOCAL,
    _approve,
    _innsyn,
    _log,
    _receive,
    _received,
)

_VALIDITY = re.compile(r"\n(?:status|ikraft_end): [^\n]*")


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


@pytest.fixture
def promoted(workspace: Path, corpus: Path, tmp_path: Path) -> tuple[str, Path]:
    """The received print, promoted: its SHA-256 and its Markdown file."""
    sha256 = _receive(workspace, minimal_pdf(REPEALED_PRINT), name="utskrift.pdf")
    assert _approve(sha256, tmp_path).exit_code == 0
    result = _received("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    [path] = (corpus / LOCAL / "4646").glob("*.md")
    return sha256, path


def _refresh(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "received", "refresh", "--corpus", str(corpus), *extra)


def _stale(path: Path) -> str:
    """Rewrite ``path`` as the renderer wrote it before #639; the current rendering."""
    current = path.read_text(encoding="utf-8")
    assert '\nstatus: "repealed"\n' in current
    path.write_text(_VALIDITY.sub("", current), encoding="utf-8")
    return current


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _slug(path: Path) -> str:
    return path.stem


class TestRefresh:
    def test_local_cannot_repair_the_stale_rendering(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        sha256, path = promoted
        _stale(path)
        before = tree(corpus)
        result = _received("local", sha256, corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout.startswith("Unchanged: ")
        assert tree(corpus) == before

    def test_it_rewrites_only_the_markdown_and_prints_the_commit(
        self, promoted: tuple[str, Path], corpus: Path, root: Path
    ) -> None:
        _, path = promoted
        current = _stale(path)
        before, log = tree(corpus), _log(root)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert path.read_text(encoding="utf-8") == current
        changed = {p for p, v in tree(corpus).items() if before.get(p) != v}
        assert changed == {p for p in before if p.endswith(f"{_slug(path)}.md")}
        assert _log(root) == log
        subject = f"migration(lokal-forskrift): re-render 4646/{_slug(path)} front matter"
        assert f"commit -m '{subject}'" in result.stdout
        assert f"wrote {LOCAL}/4646/{path.name}" in result.stdout
        assert "version, content_hash and provenance unchanged" in result.stdout

    def test_a_current_rendering_is_left_alone(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout == (
            "lf-20010203-9001: unchanged; its rendering is current\n"
            "Nothing to refresh; nothing written, nothing to commit.\n"
        )
        assert tree(corpus) == before

    def test_a_dry_run_prints_the_diff_and_writes_nothing(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _stale(promoted[1])
        before = tree(corpus)
        result = _refresh(corpus, "--dry-run")
        assert result.exit_code == 0, result.output
        assert '\n+status: "repealed"\n' in result.stdout
        assert result.stdout.endswith("Dry run: nothing written.\n")
        assert tree(corpus) == before

    def test_an_observed_document_is_not_read(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _, path = promoted
        _stale(path)
        _edit(path, '\nbasis: "received"\n', '\nbasis: "observed"\n')
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 0, result.output
        assert result.stdout == "Nothing to refresh; nothing written, nothing to commit.\n"
        assert tree(corpus) == before


class TestRefusals:
    def _refused(self, corpus: Path) -> str:
        before = tree(corpus)
        result = _refresh(corpus)
        assert result.exit_code == 1, result.output
        assert "Traceback" not in result.output
        assert tree(corpus) == before
        assert result.stderr.startswith("Refused: lf-20010203-9001: ")
        return result.stderr

    def test_without_a_standing_approval(
        self, promoted: tuple[str, Path], corpus: Path, tmp_path: Path
    ) -> None:
        sha256, path = promoted
        _stale(path)
        assert _approve(sha256, tmp_path, decision="hold").exit_code == 0
        assert "the standing decision is hold" in self._refused(corpus)

    def test_a_changed_body(self, promoted: tuple[str, Path], corpus: Path) -> None:
        _, path = promoted
        _stale(path)
        path.write_text(path.read_text(encoding="utf-8") + "\nEt tillegg.\n", encoding="utf-8")
        assert "its body would change" in self._refused(corpus)

    def test_a_moved_provenance_field(self, promoted: tuple[str, Path], corpus: Path) -> None:
        _, path = promoted
        _stale(path)
        text = path.read_text(encoding="utf-8")
        received_at = re.search(r'\nreceived_at: "([^"]+)"\n', text)
        assert received_at is not None
        _edit(path, received_at.group(1), "2026-01-01T00:00:00Z")
        assert "would change received_at;" in self._refused(corpus)

    def test_a_new_text_is_a_new_version(self, promoted: tuple[str, Path], corpus: Path) -> None:
        manifest_path = corpus / LOCAL / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        [record] = manifest["documents"].values()
        record["content_hash"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        reason = self._refused(corpus)
        assert "That is a new version, not a re-render" in reason
        assert "`lovspor promote received local`" in reason

    def test_front_matter_naming_no_received_source(
        self, promoted: tuple[str, Path], corpus: Path
    ) -> None:
        _, path = promoted
        text = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"\ninnsyn_request: [^\n]*", "", text), encoding="utf-8")
        assert "does not name its received source (KeyError)" in self._refused(corpus)


@pytest.mark.parametrize("mode", ["write", "dry-run", "refusal"])
def test_batch_refresh_preserves_other_files_and_continues_after_a_refusal(
    mode: str,
    promoted: tuple[str, Path],
    workspace: Path,
    corpus: Path,
    root: Path,
    tmp_path: Path,
) -> None:
    """Operations #645 promises per-document refusal and Markdown-only batch writes."""
    _, first = promoted
    lines = tuple(line.replace("2001-02-03-9001", "2001-02-03-9006") for line in REPEALED_PRINT)
    sha256 = _receive(workspace, minimal_pdf(lines), name="second.pdf")
    assert _approve(sha256, tmp_path).exit_code == 0
    result = _received("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    [second] = [path for path in first.parent.glob("*.md") if path != first]
    first_current, second_current = _stale(first), _stale(second)
    if mode == "refusal":
        # The lower id is visited first: its refusal must not stop the later refresh.
        first.write_text(first.read_text(encoding="utf-8") + "\nEt tillegg.\n", encoding="utf-8")
    before, log, held = tree(corpus), _log(root), tree(workspace)
    result = _refresh(corpus, *(("--dry-run",) if mode == "dry-run" else ()))
    assert result.exit_code == (1 if mode == "refusal" else 0), result.output
    expected = before.copy()
    if mode != "dry-run":
        expected[second.relative_to(corpus).as_posix()] = second_current.encode("utf-8")
        if mode == "write":
            expected[first.relative_to(corpus).as_posix()] = first_current.encode("utf-8")
    assert tree(corpus) == expected
    assert _log(root) == log
    assert tree(workspace) == held
    if mode == "refusal":
        assert "Refused: lf-20010203-9001: its body would change" in result.stderr
        assert f"re-render 4646/{second.stem} front matter" in result.stdout
    elif mode == "dry-run":
        assert result.stdout.count('+status: "repealed"') == 2
        assert result.stdout.endswith("Dry run: nothing written.\n")
    else:
        assert "re-render front matter of 2 received documents" in result.stdout
        again = _refresh(corpus)
        assert again.exit_code == 0, again.output
        assert again.stdout.count("unchanged; its rendering is current") == 2
        assert "nothing to commit" in again.stdout
        assert tree(corpus) == expected


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_refresh_refuses_a_missing_or_corrupt_named_source(
    damage: str, promoted: tuple[str, Path], workspace: Path, corpus: Path
) -> None:
    """A refresh must use the same hash-verified held source as received local."""
    sha256, path = promoted
    _stale(path)
    blob = workspace / "received" / sha256[:2] / sha256
    assert blob.is_file()
    if damage == "missing":
        blob.unlink()
    else:
        blob.write_bytes(b"not the received document")
    reason = TestRefusals()._refused(corpus)
    assert (
        "missing or unreadable" if damage == "missing" else "does not hash to its name"
    ) in reason
