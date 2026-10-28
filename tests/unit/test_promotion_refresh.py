"""``lovspor promote refresh``: re-render an unchanged observed version's front matter (#595).

The operator's question: an extractor bump changed what a published
document's front matter says but not its text; ``local`` stops at
``Unchanged``, ``migrate`` refuses the rendering change — can lovverk be
brought up to date with a supported command, keeping the version? Every
state is reached the way an operator reaches it, as in
``test_promotion_migrate.py``: the earlier engine is the running one with
``EXTRACTOR_VERSION`` one lower, reading the page's ``ikraft_text`` as
``straks``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import Result

from lovspor.innsyn.workspace import ENV_INNSYN_DIR
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion import observed_refresh
from lovspor.promotion.fields import read_regulation
from lovspor.promotion.migrate import front_matter
from tests.unit.promotion_cli_fixtures import AUTHORITY, invoke, make_corpus, register, tree
from tests.unit.test_promotion_migrate import (
    _approve,
    _log,
    _migrate_one,
    _promoted_once,
    _record,
)

LOCAL = "lokale-forskrifter"


def _straks(lines: tuple[str, ...]) -> object:
    regulation, fields = read_regulation(lines)  # type: ignore[misc]
    return regulation, fields.model_copy(update={"ikraft_text": "straks"})


def _refresh(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "refresh", "--corpus", str(corpus), *extra)


def _markdown(corpus: Path) -> tuple[Path, str]:
    [path] = (corpus / LOCAL / AUTHORITY).glob("*.md")
    return path, path.read_text(encoding="utf-8")


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


@pytest.fixture
def stale(root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """A capture the earlier engine promoted; the running one renders its front matter otherwise."""
    monkeypatch.delenv(ENV_INNSYN_DIR, raising=False)
    return _promoted_once(root, corpus, tmp_path, monkeypatch, _straks)


class TestRefresh:
    def test_refresh_keeps_published_provenance_independent_of_run_time(
        self, stale: str, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(stale, tmp_path)
        path, before = _markdown(corpus)
        original = path.read_bytes()
        log = _log(root)
        request = observed_refresh.RefreshAllRequest(corpus, dry_run=False)

        observed_refresh.refresh_all_impl(request, datetime(2000, 1, 1, tzinfo=UTC))
        first = tree(corpus)
        after = path.read_text(encoding="utf-8")
        assert after != before
        for key in ("observed_at_first", "source_url", "source_sha256", "version"):
            assert front_matter(after)[key] == front_matter(before)[key]

        path.write_bytes(original)
        observed_refresh.refresh_all_impl(request, datetime(2099, 12, 31, tzinfo=UTC))

        assert tree(corpus) == first
        assert _log(root) == log

    def test_it_rewrites_the_front_matter_alone_and_keeps_the_version(
        self, stale: str, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(stale, tmp_path)
        path, before = _markdown(corpus)
        assert front_matter(before)["ikraft_text"] == '"straks"'
        files, log = tree(corpus), _log(root)

        result = _refresh(corpus)

        assert result.exit_code == 0, result.output
        after = path.read_text(encoding="utf-8")
        assert front_matter(after)["ikraft_text"] != '"straks"'
        kept = ("id", "version", "content_hash", "source_url", "source_sha256", "observed_at_first")
        assert {k: front_matter(after)[k] for k in kept} == {
            k: front_matter(before)[k] for k in kept
        }
        assert after.partition("\n---\n")[2] == before.partition("\n---\n")[2]
        changed = {p for p, v in tree(corpus).items() if files.get(p) != v}
        assert changed == {p for p in files if p.endswith(path.name)}
        assert _log(root) == log
        assert _record(corpus)["version"] == 1
        slug = path.stem
        subject = f"migration(lokal-forskrift): re-render {AUTHORITY}/{slug} front matter"
        assert f"commit -m '{subject}'" in result.stdout
        assert f"wrote {LOCAL}/{AUTHORITY}/{path.name}" in result.stdout
        assert "v1: front matter re-rendered; version, content_hash and provenance unchanged" in (
            result.stdout
        )

    def test_a_refreshed_document_then_migrates(
        self, stale: str, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(stale, tmp_path)
        refused = _migrate_one(corpus)
        assert refused.exit_code == 1
        assert "its rendering changes though its text does not" in refused.output
        assert "lovspor promote refresh" in refused.output

        assert _refresh(corpus).exit_code == 0

        migrated = _migrate_one(corpus)
        assert migrated.exit_code == 0, migrated.output
        assert "the Markdown is unchanged" in migrated.stdout

    def test_a_current_rendering_is_left_alone(
        self, stale: str, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(stale, tmp_path)
        assert _refresh(corpus).exit_code == 0
        before = tree(corpus)

        result = _refresh(corpus)

        assert result.exit_code == 0, result.output
        assert "unchanged; its rendering is current" in result.stdout
        assert "Nothing to refresh; nothing written, nothing to commit." in result.stdout
        assert tree(corpus) == before

    def test_a_dry_run_prints_the_diff_and_writes_nothing(
        self, stale: str, corpus: Path, tmp_path: Path
    ) -> None:
        _approve(stale, tmp_path)
        before = tree(corpus)

        result = _refresh(corpus, "--dry-run")

        assert result.exit_code == 0, result.output
        assert '-ikraft_text: "straks"' in result.stdout
        assert "Dry run: nothing written." in result.stdout
        assert tree(corpus) == before


class TestRefusals:
    @pytest.mark.parametrize("key", ["observed_at_first", "source_url"])
    def test_observed_provenance_cannot_move(
        self,
        key: str,
        stale: str,
        root: Path,
        corpus: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Operations #595 requires every provenance field to remain byte-identical."""
        _approve(stale, tmp_path)
        rendered = observed_refresh.render_published

        def moved(p, s):
            markdown = rendered(p, s)
            value = front_matter(markdown)[key]
            return markdown.replace(f"{key}: {value}", f'{key}: "changed"', 1)

        monkeypatch.setattr(observed_refresh, "render_published", moved)
        before, log = tree(corpus), _log(root)

        result = _refresh(corpus)

        assert result.exit_code == 1, result.output
        assert f"re-rendering would change {key};" in result.stderr
        assert (tree(corpus), _log(root)) == (before, log)

    def test_without_an_approval_at_the_running_extractor(
        self, stale: str, root: Path, corpus: Path
    ) -> None:
        before, log = tree(corpus), _log(root)

        result = _refresh(corpus)

        assert result.exit_code == 1, result.output
        assert "approval was given for another text or extractor" in result.output
        assert (tree(corpus), _log(root)) == (before, log)

    def test_a_body_that_would_change(
        self, stale: str, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _approve(stale, tmp_path)
        rendered = observed_refresh.render_published
        monkeypatch.setattr(
            observed_refresh, "render_published", lambda p, s: rendered(p, s) + "\nmore\n"
        )
        before = tree(corpus)

        result = _refresh(corpus)

        assert result.exit_code == 1, result.output
        assert "its body would change though its text does not" in result.output
        assert tree(corpus) == before

    def test_a_received_document_needs_the_innsyn_workspace(
        self, stale: str, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _approve(stale, tmp_path)
        monkeypatch.setattr(observed_refresh, "is_received", lambda document: True)
        before = tree(corpus)

        result = _refresh(corpus)

        assert result.exit_code == 1, result.output
        assert f"set {ENV_INNSYN_DIR}" in result.output
        assert tree(corpus) == before
