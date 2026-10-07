"""``evidence/<slug>.json``: a sidecar, so the rendering and every approval are untouched (S10).

Approvals are pinned to ``content_hash`` and ``extractor_version``. The
evidence lives beside the Markdown, so what the corpus can resolve a
target to changes the sidecar and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.corpus import CentralEntry, CorpusCheckout, LocalManifest
from lovspor.promotion.evidence import target_index
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    Decision,
    approve,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import html_page

LOCAL = "lokale-forskrifter"
SLUG = "forskrift-om-renovasjon-og-slam-eksempel-kommune"
EVIDENCE = f"{LOCAL}/{AUTHORITY}/evidence/{SLUG}.json"
FORURENSNINGSLOVEN = {
    "doc_type": "lov",
    "xml_hash": "1" * 64,
    "markdown_path": "lover/forurensningsloven-forurl.md",
    "source_dataset": "gjeldende-lover",
    "last_seen": "2026-04-29T11:20:30Z",
    "status": "current",
    "slug": "forurensningsloven-forurl",
    "title": "Lov om vern mot forurensninger og om avfall (forurensningsloven)",
}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


def _with_central_law(corpus: Path) -> Path:
    path = corpus / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["documents"]["nl-19810313-006"] = FORURENSNINGSLOVEN
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return corpus


def _promote_into(root: Path, corpus: Path, tmp_path: Path) -> dict[str, bytes]:
    sha256 = store(root, html_page())
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return {path: data for path, data in tree(corpus).items() if path.startswith(LOCAL)}


@pytest.fixture
def promoted(root: Path, tmp_path: Path) -> tuple[dict[str, bytes], dict[str, bytes]]:
    """The local dataset after one promotion, without and with the hjemmel law in the corpus."""
    bare = _promote_into(root, make_corpus(tmp_path / "bare"), tmp_path)
    linked = _promote_into(root, _with_central_law(make_corpus(tmp_path / "linked")), tmp_path)
    return bare, linked


def test_what_the_corpus_resolves_changes_neither_the_markdown_nor_the_approval_pins(
    promoted: tuple[dict[str, bytes], dict[str, bytes]],
) -> None:
    bare, linked = promoted
    pinned = ("content_hash", "extractor_version", "renderer_version", "version", "slug")

    def pins(files: dict[str, bytes]) -> list[tuple[object, ...]]:
        documents = json.loads(files[f"{LOCAL}/manifest.json"])["documents"]
        return [(doc_id, *(r[k] for k in pinned)) for doc_id, r in documents.items()]

    assert bare.keys() == linked.keys()
    assert bare[f"{LOCAL}/{AUTHORITY}/{SLUG}.md"] == linked[f"{LOCAL}/{AUTHORITY}/{SLUG}.md"]
    assert pins(bare) == pins(linked)
    assert bare[EVIDENCE] != linked[EVIDENCE]


def test_the_evidence_names_the_version_and_its_unreviewed_basis(
    promoted: tuple[dict[str, bytes], dict[str, bytes]],
) -> None:
    _, linked = promoted
    evidence = json.loads(linked[EVIDENCE])
    [(doc_id, record)] = json.loads(linked[f"{LOCAL}/manifest.json"])["documents"].items()

    assert (evidence["doc_id"], evidence["version"], evidence["content_hash"]) == (
        doc_id,
        record["version"],
        record["content_hash"],
    )
    assert (evidence["basis"], evidence["reviewed"], evidence["evidence_version"]) == (
        "source_explicit",
        False,
        1,
    )
    assert (evidence["vedtatt"]["status"], evidence["vedtatt"]["value"]) == ("stated", "2019-12-12")
    assert (evidence["ikraft"]["status"], evidence["ikraft"]["value"]) == ("stated", "2020-01-01")


def test_a_hjemmel_resolves_when_the_corpus_has_the_law_and_stays_text_otherwise(
    promoted: tuple[dict[str, bytes], dict[str, bytes]],
) -> None:
    bare, linked = promoted
    bare_relations = json.loads(bare[EVIDENCE])["relations"]
    linked_relations = json.loads(linked[EVIDENCE])["relations"]

    assert [r["target_text"] for r in linked_relations] == [
        "lov 13. mars 1981 nr. 6",
        "forurensningsloven",
    ]
    assert {r["kind"] for r in linked_relations} == {"hjemmel"}
    assert all(r["target"]["doc_id"] == "nl-19810313-006" for r in linked_relations)
    assert all(r["evidence"].startswith("Vedtatt av kommunestyret") for r in linked_relations)
    assert [(r["target"], r["unresolved"]) for r in bare_relations] == [
        (None, "not_in_corpus"),
        (None, "not_in_corpus"),
    ]


def test_the_markdown_carries_no_evidence(
    promoted: tuple[dict[str, bytes], dict[str, bytes]],
) -> None:
    _, linked = promoted
    markdown = linked[f"{LOCAL}/{AUTHORITY}/{SLUG}.md"].decode("utf-8")

    assert "relations" not in markdown
    assert "evidence" not in markdown


def test_only_a_current_central_record_in_a_dataset_directory_is_a_target() -> None:
    entry = CentralEntry.model_validate(FORURENSNINGSLOVEN)
    central = {
        "nl-19810313-006": entry,
        "nl-19810313-007": entry.model_copy(update={"markdown_path": "other/x.md"}),
        "nl-19810313-008": entry.model_copy(update={"slug": None}),
        "nl-19811399-009": entry.model_copy(update={"slug": "bad-date"}),
    }

    index = target_index(central, LocalManifest())

    assert [linked.doc_id for linked in index.by_lovdata.values()] == ["nl-19810313-006"]
    assert [d.doc_id for d in index.by_short_name["forurensningsloven"]] == [
        "nl-19810313-006",
        "nl-19811399-009",
    ]


@pytest.mark.parametrize("contents", ["{", "[]", '{"documents": []}', '{"documents": {"x": 1}}'])
def test_an_unreadable_central_manifest_refuses(tmp_path: Path, contents: str) -> None:
    corpus = make_corpus(tmp_path)
    (corpus / "manifest.json").write_text(contents, encoding="utf-8")

    with pytest.raises(PromotionRefusedError, match="central manifest"):
        CorpusCheckout(corpus, []).central_entries()
