"""``history/<slug>.json`` across several local documents (ADR-0016 3, S3).

A document that is withdrawn, not yet committed or already current is
skipped — and only that document: every one after it in the manifest is
still derived.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.local_history import derive_local_history
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    Decision,
    approve,
    git,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LOCAL = "lokale-forskrifter"
LF_LINES = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
LF_ID = "lf-20191212-2077"


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


def _promote(root: Path, corpus: Path, tmp_path: Path, payload: bytes, url: str) -> str:
    """Promote ``payload`` served at ``url``; the markdown path it was written to."""
    sha256 = store(root, payload, url=url, observed_at=FIRST_SEEN + timedelta(minutes=len(url)))
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return result.stdout.split(" -> ", 1)[1].splitlines()[0]


def _commit(corpus: Path, *paths: str) -> None:
    git(corpus, "add", "--", *paths)
    git(corpus, "commit", "-q", "-m", "chore: commit promoted files")


def _manifest(corpus: Path) -> dict[str, dict[str, object]]:
    path = corpus / LOCAL / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))["documents"]


def _history_path(corpus: Path, doc_id: str) -> str:
    record = _manifest(corpus)[doc_id]
    return f"{LOCAL}/{record['authority_id']}/history/{record['slug']}.json"


def _two_documents(root: Path, corpus: Path, tmp_path: Path) -> tuple[str, str]:
    """An ``lf-`` document and an ``lk-`` one, in that (manifest) order; their ids."""
    _promote(root, corpus, tmp_path, html_page(LF_LINES), f"{PAGE_URL}-lf")
    _promote(root, corpus, tmp_path, html_page(), PAGE_URL)
    first, second = sorted(_manifest(corpus))
    assert first == LF_ID
    assert second.startswith("lk-")
    return first, second


def _derive(corpus: Path) -> tuple[str, ...]:
    return derive_local_history(CorpusCheckout(corpus, []))


def test_a_withdrawn_document_is_skipped_and_the_next_one_still_derived(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    first, second = _two_documents(root, corpus, tmp_path)
    _commit(corpus, LOCAL)
    manifest_path = corpus / LOCAL / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"][first]["status"] = "removed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert _derive(corpus) == (_history_path(corpus, second),)


def test_an_uncommitted_document_is_skipped_and_the_next_one_still_derived(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    first, second = _two_documents(root, corpus, tmp_path)
    second_markdown = str(_manifest(corpus)[second]["markdown_path"])
    _commit(corpus, second_markdown)

    assert _derive(corpus) == (_history_path(corpus, second),)
    assert not (corpus / _history_path(corpus, first)).exists()


def test_a_current_history_is_skipped_and_the_next_one_still_derived(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    lf_markdown = _promote(root, corpus, tmp_path, html_page(LF_LINES), f"{PAGE_URL}-lf")
    _commit(corpus, LOCAL)
    _derive(corpus)
    _promote(root, corpus, tmp_path, html_page(), PAGE_URL)
    _commit(corpus, LOCAL)
    [second] = [doc_id for doc_id in _manifest(corpus) if doc_id != LF_ID]

    assert lf_markdown.startswith(f"{LOCAL}/{AUTHORITY}/")
    assert _derive(corpus) == (_history_path(corpus, second),)


def test_history_recreates_the_authority_directory_from_git(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    _promote(root, corpus, tmp_path, html_page(), PAGE_URL)
    _commit(corpus, LOCAL)
    [doc_id] = _manifest(corpus)
    shutil.rmtree(corpus / LOCAL / AUTHORITY)

    assert _derive(corpus) == (_history_path(corpus, doc_id),)
    assert (corpus / _history_path(corpus, doc_id)).is_file()


def test_an_existing_history_is_compared_as_utf8_under_any_locale(
    root: Path, corpus: Path, tmp_path: Path, c_locale: None
) -> None:
    _promote(root, corpus, tmp_path, html_page(), PAGE_URL)
    _commit(corpus, LOCAL)
    manifest_path = corpus / LOCAL / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    record["slug"] = "renovasjon-og-slamtømming"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    [written] = _derive(corpus)

    assert "slamtømming" in (corpus / written).read_bytes().decode("utf-8")
    assert _derive(corpus) == ()


def test_two_new_histories_are_both_reported(root: Path, corpus: Path, tmp_path: Path) -> None:
    first, second = _two_documents(root, corpus, tmp_path)
    _commit(corpus, LOCAL)

    assert _derive(corpus) == (_history_path(corpus, first), _history_path(corpus, second))
