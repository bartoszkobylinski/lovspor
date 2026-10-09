"""Decisions §19: earlier published bytes come from the matching git revision."""

from pathlib import Path

import pytest

from lovspor.promotion.carry import historical_markdown
from lovspor.promotion.corpus import CorpusCheckout
from tests.unit.promotion_cli_fixtures import git, make_corpus


def _markdown(version: int, content_hash: str, body: str) -> str:
    return f'---\nversion: {version}\ncontent_hash: "{content_hash}"\n---\n{body}\n'


@pytest.mark.parametrize("later_version,later_hash", [(1, "b" * 64), (2, "a" * 64)])
def test_history_skips_revisions_matching_only_version_or_hash(
    tmp_path: Path, later_version: int, later_hash: str
) -> None:
    corpus = make_corpus(tmp_path)
    relative = "lokale-forskrifter/example.md"
    markdown = corpus / relative
    published = _markdown(1, "a" * 64, "Opprinnelig forskrift: blåbær.")
    markdown.write_text(published, encoding="utf-8")
    git(corpus, "add", relative)
    git(corpus, "commit", "-q", "-m", "publish first version")
    markdown.write_text(_markdown(later_version, later_hash, "Later text"), encoding="utf-8")
    git(corpus, "commit", "-q", "-am", "publish later text")

    checkout = CorpusCheckout(corpus, [])
    assert historical_markdown(checkout, relative, 1, "a" * 64) == published
    assert historical_markdown(checkout, relative, 3, "c" * 64) is None


def test_history_reads_the_published_revision_before_a_rename(tmp_path: Path) -> None:
    corpus = make_corpus(tmp_path)
    original = "lokale-forskrifter/original.md"
    renamed = "lokale-forskrifter/renamed.md"
    published = _markdown(1, "a" * 64, "Forskrift før navneendring.")
    (corpus / original).write_text(published, encoding="utf-8")
    git(corpus, "add", original)
    git(corpus, "commit", "-q", "-m", "publish first version")
    git(corpus, "mv", original, renamed)
    git(corpus, "commit", "-q", "-m", "rename document")
    (corpus / renamed).write_text(_markdown(2, "b" * 64, "Ny tekst."), encoding="utf-8")
    git(corpus, "commit", "-q", "-am", "publish second version")

    assert historical_markdown(CorpusCheckout(corpus, []), renamed, 1, "a" * 64) == published
