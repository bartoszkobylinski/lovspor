"""End-to-end orchestrator integration tests.

These tests use pytest-httpx to mock the Lovdata API plus a real
temp git repo for the corpus side. The tarballs fed to the mocked
downloader are synthetic, built in-process with the same Lovdata-
style HTML structure we render against in production.
"""

import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from pytest_httpx import HTTPXMock

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.sections import iter_sections, strip_frontmatter
from lovspor.embeddings.store import EMBEDDING_DIM, read_embeddings, write_embeddings
from lovspor.errors import ConfigError
from lovspor.parsing.xml_normalizer import hash_normalized_xml
from lovspor.settings import Settings
from lovspor.sources.lovdata import DEFAULT_BASE_URL
from lovspor.storage.manifest import (
    Manifest,
    ManifestRecord,
    write_manifest,
)
from lovspor.sync.orchestrator import (
    _commit_with_history,
    _DocAction,
    _has_rename_path_overlap,
    run_sync,
)

_COLLISION_TITLE = "Vass og avlopsanleggslova"
_COLLISION_SLUG = "vass-og-avlopsanleggslova"


def _minimal_law_html(doc_id: str, title: str) -> bytes:
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        f'<dd class="refid">lov/{doc_id}</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        f'<article class="legalP" id="ledd-1">Body of {title}.</article>'
        "</main></body></html>"
    ).encode()


def _law_with_extra(title: str, extra_body: str) -> bytes:
    """Variant of _minimal_law_html that lets the test vary body content
    independently of the title (so the slug stays stable across runs)."""
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        '<dd class="refid">lov/x</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        f'<article class="legalP" id="ledd-1">{extra_body}</article>'
        "</main></body></html>"
    ).encode()


def _law_with_section(
    title: str,
    section_body: str,
    section_id: str = "1",
    section_title: str = "Virkeområde",
) -> bytes:
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        '<dd class="refid">lov/x</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        '<article class="legalArticle">'
        '<h3 class="legalArticleHeader">'
        f'<span class="legalArticleValue">§ {section_id}</span>. '
        f'<span class="legalArticleTitle">{section_title}</span>'
        "</h3>"
        f'<article class="legalP">{section_body}</article>'
        "</article>"
        "</main></body></html>"
    ).encode()


def _build_tarball(path: Path, members: list[tuple[str, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode="w:bz2") as tar:
        for name, content in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(content))


def _git_init_corpus(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"],
        cwd=path,
        check=True,
    )


def _register_lovdata_mocks(
    httpx_mock: HTTPXMock,
    lover_tar: Path,
    forskrifter_tar: Path,
) -> None:
    catalogue: list[dict[str, Any]] = [
        {
            "filename": "gjeldende-lover.tar.bz2",
            "description": "Gjeldende lover",
            "sizeBytes": str(lover_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
        {
            "filename": "gjeldende-sentrale-forskrifter.tar.bz2",
            "description": "Gjeldende sentrale forskrifter",
            "sizeBytes": str(forskrifter_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
    ]
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/list",
        json=catalogue,
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-lover.tar.bz2",
        content=lover_tar.read_bytes(),
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-sentrale-forskrifter.tar.bz2",
        content=forskrifter_tar.read_bytes(),
    )


def _install_fake_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[list[str]], list[str]]:
    calls: list[list[str]] = []
    api_keys: list[str] = []

    class FakeOpenAIEmbedder:
        def __init__(self, api_key: str) -> None:
            api_keys.append(api_key)

        def encode(self, texts: list[str]) -> np.ndarray:
            calls.append(list(texts))
            return _fake_embedding_matrix(texts)

    monkeypatch.setattr(orchestrator_module, "OpenAIEmbedder", FakeOpenAIEmbedder)
    return calls, api_keys


def _fake_embedding_matrix(texts: list[str]) -> np.ndarray:
    matrix = np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
    for row_index, text in enumerate(texts):
        digest = np.frombuffer(
            hashlib.sha256(text.encode("utf-8")).digest(),
            dtype=np.uint8,
        ).astype(np.float32)
        matrix[row_index, : digest.size] = (digest - 128.0) / 128.0
    return matrix


def _assert_embedding_matches_markdown(path: Path, rendered_markdown: str) -> None:
    sections = iter_sections(strip_frontmatter(rendered_markdown))
    texts = [section.text for section in sections]
    quantized, scale = quantize_int8(_fake_embedding_matrix(texts))

    actual = read_embeddings(path)
    assert actual.dim == EMBEDDING_DIM
    assert actual.scale == pytest.approx(scale)
    assert [section_id for section_id, _ in actual.sections] == [
        section.section_id for section in sections
    ]
    for index, (_section_id, vector) in enumerate(actual.sections):
        np.testing.assert_array_equal(vector, quantized[index])


def _embedding_path(corpus: Path, slug: str) -> Path:
    return corpus / "lover" / "embeddings" / f"{slug}.bin"


def _write_seed_embedding(path: Path, marker: int) -> None:
    vector = np.zeros(EMBEDDING_DIM, dtype=np.int8)
    vector[0] = marker
    write_embeddings(path, [("old", vector)], scale=1.0)


def _write_markdown_with_section(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"title: {title}",
                "---",
                f"# {title}",
                "",
                "### § 1. Virkeområde",
                "",
                body,
                "",
            ],
        ),
        encoding="utf-8",
    )


def _current_law_record(
    *,
    xml: bytes,
    slug: str | None,
    markdown_path: str | None = None,
    title: str = "Title",
    status: str = "current",
) -> ManifestRecord:
    return ManifestRecord(
        doc_type="lov",
        xml_hash=hash_normalized_xml(xml),
        markdown_path=markdown_path or f"lover/{slug}.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 5, 1, tzinfo=UTC),
        status=status,
        slug=slug,
        title=title,
        eu_basis=[],
    )


def _git_show_name_status(repo: Path, rev: str) -> str:
    return subprocess.run(
        ["git", "show", "--name-status", "--format=", rev],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _action(action: str, doc_id: str, *paths: str) -> _DocAction:
    return _DocAction(
        action=action,
        doc_type="lov",
        doc_id=doc_id,
        slug=doc_id,
        paths=tuple(Path(path) for path in paths),
    )


def _seed_collision_manifest(
    corpus: Path,
    xml_by_doc_id: dict[str, bytes],
    prior_slugs: dict[str, str],
) -> None:
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    records: dict[str, ManifestRecord] = {}
    for doc_id, prior_slug in prior_slugs.items():
        old_path = corpus / "lover" / f"{prior_slug}.md"
        old_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.write_text(f"# Prior {doc_id}\n", encoding="utf-8")
        records[doc_id] = ManifestRecord(
            doc_type="lov",
            xml_hash=hash_normalized_xml(xml_by_doc_id[doc_id]),
            markdown_path=f"lover/{prior_slug}.md",
            source_dataset="gjeldende-lover",
            last_seen=datetime(2026, 4, 30, tzinfo=UTC),
            status="current",
            slug=prior_slug,
            title=_COLLISION_TITLE,
            eu_basis=[],
        )

    write_manifest(
        Manifest(generated_at=datetime(2026, 4, 30, tzinfo=UTC), documents=records),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed collision state"], cwd=corpus, check=True)


_SIDECAR_PROPERTY_DOC_IDS = ("lov-a", "lov-b", "lov-c", "lov-d")
_SIDECAR_PROPERTY_KINDS = ("add", "change", "rename", "remove")
_SIDECAR_PROPERTY_SLUGS = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
)


@st.composite
def _sidecar_action_scenarios(
    draw: Any,
) -> list[dict[str, str]]:
    doc_ids = draw(
        st.lists(
            st.sampled_from(_SIDECAR_PROPERTY_DOC_IDS),
            min_size=1,
            max_size=len(_SIDECAR_PROPERTY_DOC_IDS),
            unique=True,
        ),
    )
    kinds = {doc_id: draw(st.sampled_from(_SIDECAR_PROPERTY_KINDS)) for doc_id in doc_ids}
    prior_doc_ids = [doc_id for doc_id in doc_ids if kinds[doc_id] != "add"]
    upstream_doc_ids = [doc_id for doc_id in doc_ids if kinds[doc_id] != "remove"]
    old_slugs = draw(
        st.lists(
            st.sampled_from(_SIDECAR_PROPERTY_SLUGS),
            min_size=len(prior_doc_ids),
            max_size=len(prior_doc_ids),
            unique=True,
        ),
    )
    old_by_doc = dict(zip(prior_doc_ids, old_slugs, strict=True))

    # PR #45 allows remove collisions: another action may take over a
    # removed doc's old slot, and the removed action must not double-stage
    # or delete that new owner.
    new_slugs = draw(
        st.lists(
            st.sampled_from(_SIDECAR_PROPERTY_SLUGS),
            min_size=len(upstream_doc_ids),
            max_size=len(upstream_doc_ids),
            unique=True,
        ),
    )
    new_by_doc = dict(zip(upstream_doc_ids, new_slugs, strict=True))

    return [
        {
            "doc_id": doc_id,
            "kind": kinds[doc_id],
            "old_slug": old_by_doc.get(doc_id, ""),
            "new_slug": new_by_doc.get(doc_id, ""),
        }
        for doc_id in doc_ids
    ]


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry sleeps out of integration runs."""
    monkeypatch.setattr("lovspor.retry.time.sleep", lambda _seconds: None)


def test_has_rename_path_overlap_ignores_embedding_sidecar_paths() -> None:
    actions = [
        _DocAction(
            action="rename",
            doc_type="lov",
            doc_id="lov-a",
            slug="a-new",
            paths=(Path("lover/a-old.md"), Path("lover/a-new.md")),
            sidecar_paths=(
                Path("lover/embeddings/shared-old.bin"),
                Path("lover/embeddings/a-new.bin"),
            ),
        ),
        _DocAction(
            action="rename",
            doc_type="lov",
            doc_id="lov-b",
            slug="b-new",
            paths=(Path("lover/b-old.md"), Path("lover/b-new.md")),
            sidecar_paths=(
                Path("lover/embeddings/b-old.bin"),
                Path("lover/embeddings/shared-old.bin"),
            ),
        ),
    ]

    assert _has_rename_path_overlap(actions) is False


@given(_sidecar_action_scenarios())
@hypothesis_settings(max_examples=200, deadline=None)
def test_run_sync_actions_do_not_double_stage_embedding_sidecars(
    scenario: list[dict[str, str]],
) -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        root = Path(raw_tmp)
        corpus = root / "lovverk"
        data_dir = root / "data"
        corpus.mkdir()
        prior_docs: dict[str, ManifestRecord] = {}
        upstream_docs: dict[str, orchestrator_module._UpstreamDoc] = {}
        for index, item in enumerate(scenario):
            doc_id = item["doc_id"]
            kind = item["kind"]
            old_hash = f"{index + 1:064x}"
            new_hash = old_hash if kind == "rename" else f"{index + 101:064x}"
            if kind != "add":
                old_slug = item["old_slug"]
                prior_docs[doc_id] = ManifestRecord(
                    doc_type="lov",
                    xml_hash=old_hash,
                    markdown_path=f"lover/{old_slug}.md",
                    source_dataset="gjeldende-lover",
                    last_seen=datetime(2026, 5, 1, tzinfo=UTC),
                    status="current",
                    slug=old_slug,
                    title=doc_id,
                    eu_basis=[],
                )
                _write_seed_embedding(_embedding_path(corpus, old_slug), marker=index + 1)
            if kind != "remove":
                upstream_docs[doc_id] = orchestrator_module._UpstreamDoc(
                    doc_id=doc_id,
                    source_dataset="gjeldende-lover",
                    xml_bytes=b"<xml/>",
                    xml_hash=new_hash,
                    slug=item["new_slug"],
                    title=doc_id,
                    eu_basis=(),
                )

        prior = Manifest(
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            documents=prior_docs,
        )
        captured_actions: list[_DocAction] = []

        def fake_write_one(
            settings: Settings,
            upstream: orchestrator_module._UpstreamDoc,
            now: datetime,
            _embedder: object,
        ) -> tuple[ManifestRecord, list[Path]]:
            md_path = settings.lovverk_repo_path / "lover" / f"{upstream.slug}.md"
            sidecar_path = _embedding_path(settings.lovverk_repo_path, upstream.slug)
            record = ManifestRecord(
                doc_type="lov",
                xml_hash=upstream.xml_hash,
                markdown_path=str(md_path.relative_to(settings.lovverk_repo_path)),
                source_dataset=upstream.source_dataset,
                last_seen=now,
                status="current",
                slug=upstream.slug,
                title=upstream.title,
                eu_basis=[],
            )
            return record, [md_path, sidecar_path]

        def capture_commit(*_args: object, **kwargs: object) -> None:
            captured_actions.extend(kwargs["actions"])

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(orchestrator_module, "_ensure_corpus_git_repo", lambda _path: None)
            monkeypatch.setattr(orchestrator_module, "_ensure_clean_corpus", lambda _path: None)
            monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
            monkeypatch.setattr(
                orchestrator_module, "_collect_upstream", lambda *_args: upstream_docs
            )
            monkeypatch.setattr(
                orchestrator_module,
                "_needs_sprint5_history_migration",
                lambda *_args: False,
            )
            monkeypatch.setattr(
                orchestrator_module,
                "_needs_sprint8_eu_basis_migration",
                lambda *_args: False,
            )
            monkeypatch.setattr(
                orchestrator_module,
                "_needs_sprint9_embeddings_migration",
                lambda *_args: False,
            )
            monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: object())
            monkeypatch.setattr(orchestrator_module, "_write_one", fake_write_one)
            monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)
            run_sync(
                Settings(
                    data_dir=data_dir,
                    lovverk_repo_path=corpus,
                    openai_api_key="sk-test",
                ),
            )

        owners: dict[Path, str] = {}
        for action in captured_actions:
            for sidecar in action.sidecar_paths:
                assert sidecar not in owners, (
                    f"{sidecar} was staged by both {owners[sidecar]} and {action.doc_id}"
                )
                owners[sidecar] = action.doc_id


def test_run_sync_add_rename_change_sidecar_collision_stages_each_sidecar_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    data_dir = tmp_path / "data"
    corpus.mkdir()
    alpha_xml = _law_with_section("Alpha", "New A body.")
    beta_xml = _law_with_section("Beta", "Stable B body.")
    old_gamma_xml = _law_with_section("Old Gamma", "Old C body.")
    new_gamma_xml = _law_with_section("Gamma", "Changed C body.")
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-b": _current_law_record(xml=beta_xml, slug="alpha", title="Beta"),
            "lov-c": _current_law_record(xml=old_gamma_xml, slug="beta", title="Old Gamma"),
        },
    )
    upstream_docs = {
        "lov-a": orchestrator_module._UpstreamDoc(
            doc_id="lov-a",
            source_dataset="gjeldende-lover",
            xml_bytes=alpha_xml,
            xml_hash=hash_normalized_xml(alpha_xml),
            slug="alpha",
            title="Alpha",
            eu_basis=(),
        ),
        "lov-b": orchestrator_module._UpstreamDoc(
            doc_id="lov-b",
            source_dataset="gjeldende-lover",
            xml_bytes=beta_xml,
            xml_hash=hash_normalized_xml(beta_xml),
            slug="beta",
            title="Beta",
            eu_basis=(),
        ),
        "lov-c": orchestrator_module._UpstreamDoc(
            doc_id="lov-c",
            source_dataset="gjeldende-lover",
            xml_bytes=new_gamma_xml,
            xml_hash=hash_normalized_xml(new_gamma_xml),
            slug="gamma",
            title="Gamma",
            eu_basis=(),
        ),
    }
    _write_seed_embedding(_embedding_path(corpus, "alpha"), marker=1)
    _write_seed_embedding(_embedding_path(corpus, "beta"), marker=2)
    captured_actions: list[_DocAction] = []

    def fake_write_one(
        settings: Settings,
        upstream: orchestrator_module._UpstreamDoc,
        now: datetime,
        _embedder: object,
    ) -> tuple[ManifestRecord, list[Path]]:
        md_path = settings.lovverk_repo_path / "lover" / f"{upstream.slug}.md"
        sidecar_path = _embedding_path(settings.lovverk_repo_path, upstream.slug)
        record = ManifestRecord(
            doc_type="lov",
            xml_hash=upstream.xml_hash,
            markdown_path=str(md_path.relative_to(settings.lovverk_repo_path)),
            source_dataset=upstream.source_dataset,
            last_seen=now,
            status="current",
            slug=upstream.slug,
            title=upstream.title,
            eu_basis=[],
        )
        return record, [md_path, sidecar_path]

    def capture_commit(*_args: object, **kwargs: object) -> None:
        captured_actions.extend(kwargs["actions"])

    monkeypatch.setattr(orchestrator_module, "_ensure_corpus_git_repo", lambda _path: None)
    monkeypatch.setattr(orchestrator_module, "_ensure_clean_corpus", lambda _path: None)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: upstream_docs)
    monkeypatch.setattr(
        orchestrator_module, "_needs_sprint5_history_migration", lambda *_args: False
    )
    monkeypatch.setattr(
        orchestrator_module, "_needs_sprint8_eu_basis_migration", lambda *_args: False
    )
    monkeypatch.setattr(
        orchestrator_module, "_needs_sprint9_embeddings_migration", lambda *_args: False
    )
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: object())
    monkeypatch.setattr(orchestrator_module, "_write_one", fake_write_one)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    owners: dict[Path, str] = {}
    for action in captured_actions:
        for sidecar in action.sidecar_paths:
            assert sidecar not in owners
            owners[sidecar] = action.doc_id
    assert owners[_embedding_path(corpus, "alpha")] == "lov-a"
    assert owners[_embedding_path(corpus, "beta")] == "lov-b"
    assert owners[_embedding_path(corpus, "gamma")] == "lov-c"


def test_run_sync_non_collision_changed_and_renamed_keep_per_doc_sequence_and_sidecars(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    _install_fake_embedder(monkeypatch)

    old_alpha_xml = _law_with_section("Alpha", "Old alpha body.")
    new_beta_xml = _law_with_section("Beta", "New beta body.")
    gamma_xml = _law_with_section("Gamma", "Stable gamma body.")
    _write_markdown_with_section(corpus / "lover" / "alpha.md", "Alpha", "Old alpha body.")
    _write_markdown_with_section(
        corpus / "lover" / "gamma-old.md",
        "Gamma",
        "Stable gamma body.",
    )
    _write_seed_embedding(_embedding_path(corpus, "alpha"), marker=1)
    _write_seed_embedding(_embedding_path(corpus, "gamma-old"), marker=2)
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            documents={
                "lov-change": _current_law_record(
                    xml=old_alpha_xml,
                    slug="alpha",
                    title="Alpha",
                ),
                "lov-rename": _current_law_record(
                    xml=gamma_xml,
                    slug="gamma-old",
                    title="Gamma",
                ),
            },
        ),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed corpus"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-change.xml", new_beta_xml),
            ("nl/lov-rename.xml", gamma_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    report = run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    assert report.changed_count == 1
    assert report.unchanged_count == 1
    assert (corpus / "lover" / "beta.md").exists()
    assert (corpus / "lover" / "gamma.md").exists()
    assert not (corpus / "lover" / "alpha.md").exists()
    assert not (corpus / "lover" / "gamma-old.md").exists()
    assert _embedding_path(corpus, "beta").exists()
    assert _embedding_path(corpus, "gamma").exists()
    assert not _embedding_path(corpus, "alpha").exists()
    assert not _embedding_path(corpus, "gamma-old").exists()
    _assert_embedding_matches_markdown(
        _embedding_path(corpus, "beta"),
        (corpus / "lover" / "beta.md").read_text(encoding="utf-8"),
    )
    _assert_embedding_matches_markdown(
        _embedding_path(corpus, "gamma"),
        (corpus / "lover" / "gamma.md").read_text(encoding="utf-8"),
    )

    subjects = _git_log_subjects(corpus).splitlines()
    assert subjects[:3] == [
        "sync: update manifest, index, and history",
        "rename(lov): gamma",
        "update(lov): beta",
    ]
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"]["lov-change"]["markdown_path"] == "lover/beta.md"
    assert manifest["documents"]["lov-rename"]["markdown_path"] == "lover/gamma.md"


def test_maybe_delete_old_embeddings_returns_none_for_absent_sidecar(
    tmp_path: Path,
) -> None:
    result = orchestrator_module._maybe_delete_old_embeddings(
        tmp_path,
        "gjeldende-lover",
        "missing",
        set(),
    )

    assert result is None


def test_maybe_delete_old_embeddings_returns_none_for_protected_sidecar(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lover" / "embeddings" / "skattie.bin"
    _write_seed_embedding(path, marker=7)

    result = orchestrator_module._maybe_delete_old_embeddings(
        tmp_path,
        "gjeldende-lover",
        "skattie",
        {path},
    )

    assert result is None
    assert path.exists()


def test_maybe_delete_old_embeddings_returns_none_for_absent_protected_sidecar(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lover" / "embeddings" / "missing.bin"

    result = orchestrator_module._maybe_delete_old_embeddings(
        tmp_path,
        "gjeldende-lover",
        "missing",
        {path},
    )

    assert result is None
    assert not path.exists()


def test_maybe_delete_old_embeddings_deletes_unprotected_existing_sidecar(
    tmp_path: Path,
) -> None:
    path = tmp_path / "lover" / "embeddings" / "skattie.bin"
    _write_seed_embedding(path, marker=7)

    result = orchestrator_module._maybe_delete_old_embeddings(
        tmp_path,
        "gjeldende-lover",
        "skattie",
        set(),
    )

    assert result == path
    assert not path.exists()


def test_write_embeddings_for_doc_empty_sections_writes_header_only_file(
    tmp_path: Path,
) -> None:
    class FailingEmbedder:
        def encode(self, _texts: list[str]) -> np.ndarray:
            raise AssertionError("empty documents must not call the embedder")

    path = orchestrator_module._write_embeddings_for_doc(
        tmp_path,
        "gjeldende-lover",
        "empty",
        "---\ntitle: Empty\n---\n# Empty\n\nPlain lead-in with no legal sections.\n",
        FailingEmbedder(),
    )

    assert path.stat().st_size == 16
    parsed = read_embeddings(path)
    assert parsed.dim == EMBEDDING_DIM
    assert parsed.scale == pytest.approx(1.0)
    assert parsed.sections == []


def test_write_embeddings_for_doc_chunks_sections_over_token_limit(
    tmp_path: Path,
) -> None:
    """A section longer than the embedding model's input window must
    produce multiple vectors under the same section_id (the tail used
    to be silently truncated and invisible to semantic search)."""
    long_text = " ".join(f"ord{i}" for i in range(9000))
    rendered = (
        "---\ntitle: Lang\n---\n# Lang lov\n\n"
        "## Kapittel 1.\n\n"
        "### § 1-1. Kort\n\nKort innhold.\n\n"
        f"### § 1-2. Lang\n\n{long_text}\n"
    )

    class CountingEmbedder:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def encode(self, texts: list[str]) -> np.ndarray:
            self.texts = list(texts)
            return _fake_embedding_matrix(texts)

    embedder = CountingEmbedder()
    path = orchestrator_module._write_embeddings_for_doc(
        tmp_path,
        "gjeldende-lover",
        "lang-lov",
        rendered,
        embedder,
    )

    parsed = read_embeddings(path)
    ids = [section_id for section_id, _vector in parsed.sections]
    assert ids.count("1-1") == 1
    assert ids.count("1-2") >= 2  # 9000+ tokens -> at least two chunks
    assert len(parsed.sections) == len(embedder.texts)
    # The chunks reassemble the original section text — nothing lost.
    chunk_texts = [
        text for text, section_id in zip(embedder.texts, ids, strict=True) if section_id == "1-2"
    ]
    assert "".join(chunk_texts).endswith("ord8999")


def test_needs_sprint9_embeddings_migration_filters_to_current_slugged_docs(
    tmp_path: Path,
) -> None:
    present_path = tmp_path / "lover" / "embeddings" / "present.bin"
    _write_seed_embedding(present_path, marker=1)
    current_record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/present.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 5, 1, tzinfo=UTC),
        status="current",
        slug="present",
        title="Present",
        eu_basis=[],
    )
    manifest = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-present": current_record,
            "lov-removed": current_record.model_copy(
                update={
                    "markdown_path": "lover/removed.md",
                    "status": "removed",
                    "slug": "removed",
                },
            ),
            "lov-legacy": current_record.model_copy(
                update={"markdown_path": "lover/legacy.md", "slug": None},
            ),
        },
    )

    assert orchestrator_module._needs_sprint9_embeddings_migration(manifest, tmp_path) is False

    missing_manifest = manifest.model_copy(
        update={
            "documents": {
                **manifest.documents,
                "lov-missing": current_record.model_copy(
                    update={"markdown_path": "lover/missing.md", "slug": "missing"},
                ),
            },
        },
    )

    assert (
        orchestrator_module._needs_sprint9_embeddings_migration(missing_manifest, tmp_path) is True
    )


@pytest.mark.parametrize(
    ("actions", "expected"),
    [
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
                _action("rename", "c", "lover/z.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/a1.md", "lover/a2.md"),
                _action("rename", "b", "lover/b1.md", "lover/b2.md"),
            ],
            False,
        ),
        ([_action("rename", "a", "lover/x.md", "lover/y.md")], False),
        ([], False),
        ([_action("rename", "a", "lover/x.md")], False),
        (
            # update+rename overlap: an update with a slug change has
            # paths=(old, new) just like a rename. The detector now
            # catches this variant too (Codex PR-43 round 1 finding).
            [
                _action("update", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            [
                _action("update", "a", "lover/x.md", "lover/y.md"),
                _action("update", "b", "lover/y.md", "lover/x.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("update", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            # add+rename overlap: production crash 2026-05-05 (rename+remove)
            # showed the partial detector missed cross-type collisions. The
            # universal detector now catches add+rename too — add.new_path
            # equals rename.old_path means per-doc commits would corrupt.
            [
                _action("add", "a", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            # Malformed add with two paths colliding with a rename: same
            # collision class as above, detector still catches it.
            [
                _action("add", "a", "lover/x.md", "lover/y.md"),
                _action("rename", "b", "lover/y.md", "lover/z.md"),
            ],
            True,
        ),
        (
            [
                _action("rename", "a", "lover/x.md", "lover/y.md"),
                _action("remove", "b", "lover/y.md"),
            ],
            True,
        ),
        (
            [
                _action("update", "a", "lover/x.md", "lover/y.md"),
                _action("remove", "b", "lover/y.md"),
            ],
            True,
        ),
        (
            [
                _action("add", "a", "lover/y.md"),
                _action("remove", "b", "lover/y.md"),
            ],
            True,
        ),
        (
            [
                _action("add", "a", "lover/x.md"),
                _action("remove", "b", "lover/y.md"),
            ],
            False,
        ),
    ],
)
def test_has_rename_path_overlap_cases(
    actions: list[_DocAction],
    expected: bool,
) -> None:
    assert _has_rename_path_overlap(actions) is expected


@pytest.mark.parametrize(
    (
        "git_commit_mode",
        "is_sprint4_migration",
        "force_bulk_commit",
        "expected_call",
        "expected_message",
    ),
    [
        (
            "per-document",
            False,
            True,
            "bulk",
            "sync: 0 new, 0 changed, 1 renamed, 0 removed",
        ),
        (
            "per-document",
            True,
            False,
            "bulk",
            "migration: rename 1 documents to slug-based filenames",
        ),
        (
            "single",
            False,
            False,
            "bulk",
            "sync: 0 new, 0 changed, 1 renamed, 0 removed",
        ),
        ("per-document", False, False, "per-doc", None),
        (
            "per-document",
            True,
            True,
            "bulk",
            "migration: rename 1 documents to slug-based filenames",
        ),
    ],
)
def test_commit_with_history_routing_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    git_commit_mode: str,
    is_sprint4_migration: bool,
    force_bulk_commit: bool,
    expected_call: str,
    expected_message: str | None,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def record_bulk(
        _repo: Path,
        _actions: list[_DocAction],
        _extra_paths: list[Path],
        message: str,
    ) -> None:
        calls.append(("bulk", message))

    def record_per_doc(_repo: Path, _actions: list[_DocAction]) -> None:
        calls.append(("per-doc", None))

    def record_history(
        _repo: Path,
        _manifest_path: Path,
        _new_records: dict[str, ManifestRecord],
        _target_doc_ids: list[str],
        _now: datetime,
    ) -> None:
        calls.append(("history", None))

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", record_bulk)
    monkeypatch.setattr(orchestrator_module, "_commit_per_doc_actions_only", record_per_doc)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", record_history)
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _target_doc_ids: (records, []),
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )
    monkeypatch.setattr(orchestrator_module, "git_add", lambda _repo, _paths: None)
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: False)

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode=git_commit_mode,
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=is_sprint4_migration,
        force_bulk_commit=force_bulk_commit,
    )

    assert calls[0] == (expected_call, expected_message)
    if expected_call == "bulk":
        assert calls[1:] == [("history", None)]


def test_commit_with_history_default_force_bulk_commit_keeps_per_doc_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        orchestrator_module, "_commit_per_doc_actions_only", lambda *_args: calls.append("per-doc")
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _target_doc_ids: (records, []),
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )
    monkeypatch.setattr(orchestrator_module, "git_add", lambda _repo, _paths: None)
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: False)

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
    )

    assert calls == ["per-doc"]


def test_commit_with_history_bulk_stages_manifest_and_both_indexes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_extra_paths: list[Path] = []

    def record_bulk(
        _repo: Path,
        _actions: list[_DocAction],
        extra_paths: list[Path],
        _message: str,
    ) -> None:
        captured_extra_paths.extend(extra_paths)

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", record_bulk)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/y.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="y",
        title="Y",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[_action("rename", "lov-a", "lover/x.md", "lover/y.md")],
        new_records={"lov-a": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
        force_bulk_commit=True,
    )

    assert captured_extra_paths == [
        settings.lovverk_repo_path / "manifest.json",
        settings.lovverk_repo_path / "gjeldende-lover" / "INDEX.md",
        settings.lovverk_repo_path / "gjeldende-sentrale-forskrifter" / "INDEX.md",
    ]


def test_commit_with_history_bulk_excludes_removed_docs_from_history_followup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_target_doc_ids: list[str] = []

    def record_history(
        _repo: Path,
        _manifest_path: Path,
        _new_records: dict[str, ManifestRecord],
        target_doc_ids: list[str],
        _now: datetime,
    ) -> None:
        captured_target_doc_ids.extend(target_doc_ids)

    monkeypatch.setattr(orchestrator_module, "_commit_bulk", lambda *_args: None)
    monkeypatch.setattr(orchestrator_module, "_commit_history_followup", record_history)
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "generate_index",
        lambda repo, dataset, _manifest: repo / dataset / "INDEX.md",
    )

    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=tmp_path / "lovverk",
        git_commit_mode="per-document",
    )
    record = ManifestRecord(
        doc_type="lov",
        xml_hash="a" * 64,
        markdown_path="lover/added.md",
        source_dataset="gjeldende-lover",
        last_seen=datetime(2026, 4, 30, tzinfo=UTC),
        status="current",
        slug="added",
        title="Added",
        eu_basis=[],
    )

    _commit_with_history(
        settings,
        repo=settings.lovverk_repo_path,
        manifest_path=settings.lovverk_repo_path / "manifest.json",
        actions=[
            _action("add", "lov-added", "lover/added.md"),
            _action("remove", "lov-removed", "lover/removed.md"),
        ],
        new_records={"lov-added": record},
        now=datetime(2026, 4, 30, tzinfo=UTC),
        is_sprint4_migration=False,
        force_bulk_commit=True,
    )

    assert captured_target_doc_ids == ["lov-added"]


def test_commit_history_followup_commits_exact_document_count_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "lovverk"
    repo.mkdir()
    manifest_path = repo / "manifest.json"
    record = _current_law_record(
        xml=_law_with_section("Current", "Body."),
        slug="current",
        title="Current",
    )
    history_paths = [
        repo / "lover" / "history" / "current.json",
        repo / "lover" / "history" / "current.md",
    ]
    staged: list[Path] = []
    messages: list[str] = []

    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        lambda _repo, records, _targets: (records, history_paths),
    )
    monkeypatch.setattr(orchestrator_module, "write_manifest", lambda _manifest, _path: None)
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: staged.extend(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    orchestrator_module._commit_history_followup(
        repo,
        manifest_path,
        {"lov-current": record},
        ["lov-current"],
        datetime(2026, 5, 1, tzinfo=UTC),
    )

    assert staged == [manifest_path, *history_paths]
    assert messages == ["sync: update history for 1 documents"]


def test_run_sync_changed_slug_delete_skips_path_written_by_new_doc(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    old_alpha_xml = _law_with_extra("Alpha", "Old A body.")
    new_alpha_xml = _law_with_extra("Alpha", "New doc body.")
    changed_beta_xml = _law_with_extra("Beta", "Changed A body.")
    legacy_path = corpus / "lover" / "alpha.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Prior alpha\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 30, tzinfo=UTC),
        documents={
            "lov-a": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(old_alpha_xml),
                markdown_path="lover/alpha.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 30, tzinfo=UTC),
                status="current",
                slug="alpha",
                title="Alpha",
                eu_basis=[],
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed alpha"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-a.xml", changed_beta_xml),
            ("nl/lov-new.xml", new_alpha_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", lambda *_args, **_kwargs: None)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert ("write", "alpha.md") in calls
    assert ("write", "beta.md") in calls
    assert ("delete", "alpha.md") not in calls
    assert "New doc body." in (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")
    assert "Changed A body." in (corpus / "lover" / "beta.md").read_text(encoding="utf-8")


def test_run_sync_removed_delete_is_gated_by_written_paths(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production crash 2026-05-05 reproducer + regression guard.

    A doc is removed while another action writes a new doc to the
    SAME path (collision-resolution slug shuffle). Earlier behaviour
    deleted the path unconditionally and then the per-doc commit
    crashed with ``pathspec did not match any files``. The fix
    consults ``written_paths`` in the removed loop: when the slot
    is already taken by another action, the tombstone is purely a
    manifest update, no FS delete and no path in action.paths.
    """
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    old_alpha_xml = _law_with_extra("Alpha", "Removed doc body.")
    new_alpha_xml = _law_with_extra("Alpha", "Replacement doc body.")
    legacy_path = corpus / "lover" / "alpha.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Prior alpha\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 30, tzinfo=UTC),
        documents={
            "lov-old": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(old_alpha_xml),
                markdown_path="lover/alpha.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 30, tzinfo=UTC),
                status="current",
                slug="alpha",
                title="Alpha",
                eu_basis=[],
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed removed alpha"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-new.xml", new_alpha_xml)])
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", lambda *_args, **_kwargs: None)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Only the new doc's write happens. The removed loop sees alpha.md
    # in written_paths and skips the delete to preserve the new content.
    assert calls == [("write", "alpha.md")]
    assert (corpus / "lover" / "alpha.md").exists()
    assert "Replacement doc body." in (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")


def test_run_sync_add_remove_path_collision_commits_manifest_without_remove_path(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    removed_xml = _law_with_section("Alpha", "Removed body.")
    replacement_xml = _law_with_section("Alpha", "Replacement body.")
    _write_markdown_with_section(corpus / "lover" / "alpha.md", "Alpha", "Removed body.")
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            documents={
                "lov-removed": _current_law_record(
                    xml=removed_xml,
                    slug="alpha",
                    title="Alpha",
                ),
            },
        ),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed removed alpha"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-added.xml", replacement_xml)])
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.new_count == 1
    assert report.removed_count == 1
    assert "Replacement body." in (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"]["lov-added"]["status"] == "current"
    assert manifest["documents"]["lov-removed"]["status"] == "removed"
    subjects = _git_log_subjects(corpus)
    assert "add(lov): alpha" in subjects
    assert "remove(lov): alpha" not in subjects
    assert "sync: update manifest, index, and history" in subjects


def test_run_sync_rename_remove_path_collision_preserves_renamed_doc(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    removed_xml = _law_with_section("Alpha", "Removed A body.")
    renamed_xml = _law_with_section("Alpha", "Renamed B body.")
    _write_markdown_with_section(corpus / "lover" / "alpha.md", "Alpha", "Removed A body.")
    _write_markdown_with_section(corpus / "lover" / "alpha-2.md", "Alpha", "Renamed B body.")
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            documents={
                "lov-removed": _current_law_record(
                    xml=removed_xml,
                    slug="alpha",
                    title="Alpha",
                ),
                "lov-renamed": _current_law_record(
                    xml=renamed_xml,
                    slug="alpha-2",
                    title="Alpha",
                ),
            },
        ),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed alpha collision"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-renamed.xml", renamed_xml)])
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    assert report.unchanged_count == 1
    assert "Renamed B body." in (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")
    assert not (corpus / "lover" / "alpha-2.md").exists()
    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"]["lov-renamed"]["markdown_path"] == "lover/alpha.md"
    assert manifest["documents"]["lov-removed"]["status"] == "removed"


def test_run_sync_removed_path_collision_preserves_new_embedding_sidecar(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    _install_fake_embedder(monkeypatch)

    removed_xml = _law_with_section("Alpha", "Removed body.")
    replacement_xml = _law_with_section("Alpha", "Replacement body.")
    _write_markdown_with_section(corpus / "lover" / "alpha.md", "Alpha", "Removed body.")
    _write_seed_embedding(_embedding_path(corpus, "alpha"), marker=1)
    write_manifest(
        Manifest(
            generated_at=datetime(2026, 5, 1, tzinfo=UTC),
            documents={
                "lov-removed": _current_law_record(
                    xml=removed_xml,
                    slug="alpha",
                    title="Alpha",
                ),
            },
        ),
        corpus / "manifest.json",
    )
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed removed alpha sidecar"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-added.xml", replacement_xml)])
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    sidecar = _embedding_path(corpus, "alpha")
    markdown = (corpus / "lover" / "alpha.md").read_text(encoding="utf-8")
    assert sidecar.exists()
    _assert_embedding_matches_markdown(sidecar, markdown)
    add_commit = _git_show_name_status(corpus, "HEAD~1")
    assert "lover/embeddings/alpha.bin" in add_commit


def test_run_sync_removed_slugless_doc_builds_remove_action_without_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    data_dir = tmp_path / "data"
    legacy_path = corpus / "lover" / "lov-legacy.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Legacy\n", encoding="utf-8")
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-legacy": _current_law_record(
                xml=_law_with_section("Legacy", "Body."),
                slug=None,
                markdown_path="lover/lov-legacy.md",
                title="Legacy",
            ),
        },
    )
    captured_actions: list[_DocAction] = []

    def capture_commit(*_args: object, **kwargs: object) -> None:
        captured_actions.extend(kwargs["actions"])

    monkeypatch.setattr(orchestrator_module, "_ensure_corpus_git_repo", lambda _path: None)
    monkeypatch.setattr(orchestrator_module, "_ensure_clean_corpus", lambda _path: None)
    monkeypatch.setattr(orchestrator_module, "_load_or_empty_manifest", lambda _path: prior)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_args: {})
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint5_history_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint8_eu_basis_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "_needs_sprint9_embeddings_migration",
        lambda *_args: False,
    )
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert len(captured_actions) == 1
    action = captured_actions[0]
    assert action.action == "remove"
    assert action.slug == "lov-legacy"
    assert action.paths == (legacy_path,)
    assert action.sidecar_paths == ()
    assert not legacy_path.exists()


def test_run_sync_writes_all_rename_targets_before_deleting_old_paths(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml_by_doc_id = {
        "lov-a": _law_with_extra(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_extra(_COLLISION_TITLE, "Current B body."),
        "lov-c": _law_with_extra(_COLLISION_TITLE, "Current C body."),
    }
    prior_slugs = {
        "lov-a": f"{_COLLISION_SLUG}-2",
        "lov-b": f"{_COLLISION_SLUG}-3",
        "lov-c": f"{_COLLISION_SLUG}-4",
    }
    _seed_collision_manifest(corpus, xml_by_doc_id, prior_slugs)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    calls: list[tuple[str, str]] = []

    def tracking_write(path: Path, content: str) -> None:
        calls.append(("write", path.name))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def tracking_delete(path: Path) -> None:
        calls.append(("delete", path.name))
        path.unlink(missing_ok=True)

    monkeypatch.setattr(orchestrator_module, "write_document", tracking_write)
    monkeypatch.setattr(orchestrator_module, "delete_document", tracking_delete)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert calls == [
        ("write", f"{_COLLISION_SLUG}.md"),
        ("write", f"{_COLLISION_SLUG}-2.md"),
        ("write", f"{_COLLISION_SLUG}-3.md"),
        ("delete", f"{_COLLISION_SLUG}-4.md"),
    ]


def test_run_sync_handles_two_way_collision_suffix_swap_with_bulk_commit(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml_by_doc_id = {
        "lov-a": _law_with_extra(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_extra(_COLLISION_TITLE, "Current B body."),
    }
    _seed_collision_manifest(
        corpus,
        xml_by_doc_id,
        {
            "lov-a": f"{_COLLISION_SLUG}-2",
            "lov-b": _COLLISION_SLUG,
        },
    )
    commits_before = _git_commit_count(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            git_commit_mode="per-document",
        ),
    )

    assert report.new_count == 0
    assert report.changed_count == 0
    assert report.removed_count == 0
    assert report.unchanged_count == 2

    base_path = corpus / "lover" / f"{_COLLISION_SLUG}.md"
    suffixed_path = corpus / "lover" / f"{_COLLISION_SLUG}-2.md"
    assert "Current A body." in base_path.read_text(encoding="utf-8")
    assert "Current B body." in suffixed_path.read_text(encoding="utf-8")

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["documents"]["lov-a"]["markdown_path"] == (f"lover/{_COLLISION_SLUG}.md")
    assert manifest["documents"]["lov-b"]["markdown_path"] == (f"lover/{_COLLISION_SLUG}-2.md")
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "sync: 0 new, 0 changed, 2 renamed, 0 removed" in log
    assert "sync: update history for 2 documents" in log
    assert f"rename(lov): {_COLLISION_SLUG}" not in log


def test_run_sync_handles_changed_slug_overlapping_rename_old_path(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    old_alpha_xml = _law_with_extra("Alpha", "Old A body.")
    new_beta_xml = _law_with_extra("Beta", "New A body.")
    gamma_xml = _law_with_extra("Gamma", "Stable B body.")
    _seed_collision_manifest(
        corpus,
        {
            "lov-a": old_alpha_xml,
            "lov-b": gamma_xml,
        },
        {
            "lov-a": "alpha",
            "lov-b": "beta",
        },
    )
    commits_before = _git_commit_count(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-a.xml", new_beta_xml),
            ("nl/lov-b.xml", gamma_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert "New A body." in (corpus / "lover" / "beta.md").read_text(encoding="utf-8")
    assert "Stable B body." in (corpus / "lover" / "gamma.md").read_text(encoding="utf-8")
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "sync: 0 new, 1 changed, 1 renamed, 0 removed" in log
    assert "sync: update history for 2 documents" in log
    assert "update(lov): beta" not in log
    assert "rename(lov): gamma" not in log


def test_run_sync_without_openai_key_skips_embedding_sidecars(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    def fail_openai_embedder(**_kwargs: object) -> None:
        raise AssertionError("OpenAIEmbedder should not be constructed without a key")

    captured_actions: list[_DocAction] = []
    original_commit = orchestrator_module._commit_with_history

    def capture_commit(*args: object, **kwargs: object) -> object:
        captured_actions.extend(kwargs["actions"])
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "OpenAIEmbedder", fail_openai_embedder)
    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_section("Skattie", "Seed body."))],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert not (corpus / "lover" / "embeddings").exists()
    assert captured_actions
    assert all(action.sidecar_paths == () for action in captured_actions)


def test_run_sync_with_openai_key_stages_new_and_changed_sidecars_with_markdown(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    calls, api_keys = _install_fake_embedder(monkeypatch)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_section("Skattie", "First body."))],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        openai_api_key="sk-test",
    )
    run_sync(settings)

    embed_path = _embedding_path(corpus, "skattie")
    first_bytes = embed_path.read_bytes()
    embedding = read_embeddings(embed_path)
    assert embedding.dim == EMBEDDING_DIM
    assert [section_id for section_id, _ in embedding.sections] == ["1"]
    add_commit = _git_show_name_status(corpus, "HEAD~1")
    assert "lover/skattie.md" in add_commit
    assert "lover/embeddings/skattie.bin" in add_commit

    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_section("Skattie", "Second body."))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    assert embed_path.read_bytes() != first_bytes
    update_commit = _git_show_name_status(corpus, "HEAD~1")
    assert "lover/skattie.md" in update_commit
    assert "lover/embeddings/skattie.bin" in update_commit
    assert api_keys == ["sk-test", "sk-test", "sk-test", "sk-test"]
    assert sum(len(batch) for batch in calls) == 2


def test_sprint9_embeddings_migration_backfills_existing_markdown_once(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-1.xml", _law_with_section("Alpha", "Alpha body.")),
            ("nl/lov-2.xml", _law_with_section("Beta", "Beta body.")),
        ],
    )
    _build_tarball(
        forskrifter_tar,
        [("sf/sf-1.xml", _law_with_section("Gamma", "Gamma body."))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    first_settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    run_sync(first_settings)

    assert not (corpus / "lover" / "embeddings").exists()
    md_paths = [
        corpus / "lover" / "alpha.md",
        corpus / "lover" / "beta.md",
        corpus / "forskrifter" / "gamma.md",
    ]
    md_before = {path: path.read_text(encoding="utf-8") for path in md_paths}
    commits_before = _git_commit_count(corpus)

    calls, _api_keys = _install_fake_embedder(monkeypatch)

    def fail_write_document(_path: Path, _content: str) -> None:
        raise AssertionError("Sprint 9 migration must read markdown, not re-render")

    monkeypatch.setattr(orchestrator_module, "write_document", fail_write_document)
    keyed_settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        openai_api_key="sk-test",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(keyed_settings)

    assert _git_commit_count(corpus) == commits_before + 1
    log = _git_log_subjects(corpus)
    assert "migration: backfill embeddings for 3 documents" in log
    for path in md_paths:
        assert path.read_text(encoding="utf-8") == md_before[path]
    assert _embedding_path(corpus, "alpha").exists()
    assert _embedding_path(corpus, "beta").exists()
    assert (corpus / "forskrifter" / "embeddings" / "gamma.bin").exists()
    assert sum(len(batch) for batch in calls) == 3

    commits_after_backfill = _git_commit_count(corpus)
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(keyed_settings)

    assert _git_commit_count(corpus) == commits_after_backfill


def test_sprint9_embeddings_migration_skips_non_targets_and_commits_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    corpus.mkdir()
    base_xml = _law_with_section("Base", "Body.")
    _write_markdown_with_section(corpus / "lover" / "removed.md", "Removed", "Removed body.")
    _write_markdown_with_section(corpus / "lover" / "slugless.md", "Slugless", "Slugless body.")
    _write_markdown_with_section(corpus / "lover" / "present.md", "Present", "Present body.")
    _write_markdown_with_section(corpus / "lover" / "needs.md", "Needs", "Needs body.")
    present_sidecar = _embedding_path(corpus, "present")
    _write_seed_embedding(present_sidecar, marker=3)
    present_bytes = present_sidecar.read_bytes()
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-removed": _current_law_record(
                xml=base_xml,
                slug="removed",
                title="Removed",
                status="removed",
            ),
            "lov-slugless": _current_law_record(
                xml=base_xml,
                slug=None,
                markdown_path="lover/slugless.md",
                title="Slugless",
            ),
            "lov-present": _current_law_record(xml=base_xml, slug="present", title="Present"),
            "lov-missing": _current_law_record(xml=base_xml, slug="missing", title="Missing"),
            "lov-needs": _current_law_record(xml=base_xml, slug="needs", title="Needs"),
        },
    )
    writes: list[tuple[str, str]] = []
    staged: list[Path] = []
    messages: list[str] = []

    def fake_write_embeddings_for_doc(
        repo: Path,
        _dataset: str,
        slug: str,
        rendered_markdown: str,
        _embedder: object,
    ) -> Path:
        writes.append((slug, rendered_markdown))
        path = _embedding_path(repo, slug)
        _write_seed_embedding(path, marker=9)
        return path

    monkeypatch.setattr(
        orchestrator_module,
        "_write_embeddings_for_doc",
        fake_write_embeddings_for_doc,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: staged.extend(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    orchestrator_module._run_sprint9_embeddings_migration(
        Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus),
        prior,
        object(),
    )

    assert writes == [("needs", (corpus / "lover" / "needs.md").read_text(encoding="utf-8"))]
    assert staged == [_embedding_path(corpus, "needs")]
    assert messages == ["migration: backfill embeddings for 1 documents"]
    assert present_sidecar.read_bytes() == present_bytes
    assert not _embedding_path(corpus, "removed").exists()
    assert not _embedding_path(corpus, "None").exists()
    assert not _embedding_path(corpus, "missing").exists()


def test_sprint9_embeddings_migration_noops_when_no_current_doc_needs_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    corpus.mkdir()
    base_xml = _law_with_section("Base", "Body.")
    _write_markdown_with_section(corpus / "lover" / "present.md", "Present", "Present body.")
    _write_markdown_with_section(corpus / "lover" / "removed.md", "Removed", "Removed body.")
    _write_seed_embedding(_embedding_path(corpus, "present"), marker=4)
    prior = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={
            "lov-present": _current_law_record(xml=base_xml, slug="present", title="Present"),
            "lov-removed": _current_law_record(
                xml=base_xml,
                slug="removed",
                title="Removed",
                status="removed",
            ),
            "lov-slugless": _current_law_record(
                xml=base_xml,
                slug=None,
                markdown_path="lover/slugless.md",
                title="Slugless",
            ),
        },
    )
    staged: list[Path] = []
    messages: list[str] = []

    def fail_write_embeddings(*_args: object) -> Path:
        raise AssertionError("no current doc should need an embedding backfill")

    monkeypatch.setattr(orchestrator_module, "_write_embeddings_for_doc", fail_write_embeddings)
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: staged.extend(paths),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    orchestrator_module._run_sprint9_embeddings_migration(
        Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus),
        prior,
        object(),
    )

    assert staged == []
    assert messages == []


def test_run_sync_removed_document_stages_existing_embedding_sidecar_deletion(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    _install_fake_embedder(monkeypatch)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_section("Skattie", "Body."))],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )
    assert _embedding_path(corpus, "skattie").exists()

    _build_tarball(lover_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    assert not _embedding_path(corpus, "skattie").exists()
    remove_commit = _git_show_name_status(corpus, "HEAD~1")
    assert "lover/skattie.md" in remove_commit
    assert "lover/embeddings/skattie.bin" in remove_commit


def test_run_sync_removed_document_without_sidecar_does_not_stage_absent_embedding_path(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_section("Skattie", "Body."))],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert not _embedding_path(corpus, "skattie").exists()

    _build_tarball(lover_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    remove_commit = _git_show_name_status(corpus, "HEAD~1")
    assert "lover/skattie.md" in remove_commit
    assert "lover/embeddings/skattie.bin" not in remove_commit


def test_run_sync_changed_slug_delete_preserves_sidecar_written_by_new_doc(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    _install_fake_embedder(monkeypatch)
    captured_actions: list[_DocAction] = []
    original_commit = orchestrator_module._commit_with_history

    def capture_commit(*args: object, **kwargs: object) -> object:
        captured_actions.extend(kwargs["actions"])
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)

    old_alpha_xml = _law_with_section("Alpha", "Old A body.")
    new_alpha_xml = _law_with_section("Alpha", "Replacement doc body.")
    changed_beta_xml = _law_with_section("Beta", "Changed A body.")
    legacy_path = corpus / "lover" / "alpha.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Prior alpha\n", encoding="utf-8")
    _write_seed_embedding(_embedding_path(corpus, "alpha"), marker=11)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 30, tzinfo=UTC),
        documents={
            "lov-a": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(old_alpha_xml),
                markdown_path="lover/alpha.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 30, tzinfo=UTC),
                status="current",
                slug="alpha",
                title="Alpha",
                eu_basis=[],
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed alpha sidecar"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-a.xml", changed_beta_xml),
            ("nl/lov-new.xml", new_alpha_xml),
        ],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    alpha_sidecar = _embedding_path(corpus, "alpha")
    beta_sidecar = _embedding_path(corpus, "beta")
    assert alpha_sidecar.exists()
    assert beta_sidecar.exists()
    _assert_embedding_matches_markdown(
        alpha_sidecar,
        (corpus / "lover" / "alpha.md").read_text(encoding="utf-8"),
    )
    _assert_embedding_matches_markdown(
        beta_sidecar,
        (corpus / "lover" / "beta.md").read_text(encoding="utf-8"),
    )

    add_action = next(action for action in captured_actions if action.action == "add")
    update_action = next(action for action in captured_actions if action.action == "update")
    assert alpha_sidecar in add_action.sidecar_paths
    assert alpha_sidecar not in update_action.sidecar_paths
    assert beta_sidecar in update_action.sidecar_paths


def test_run_sync_two_way_collision_suffix_swap_preserves_embedding_sidecars(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    _install_fake_embedder(monkeypatch)
    captured_actions: list[_DocAction] = []
    original_commit = orchestrator_module._commit_with_history

    def capture_commit(*args: object, **kwargs: object) -> object:
        captured_actions.extend(kwargs["actions"])
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    xml_by_doc_id = {
        "lov-a": _law_with_section(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_section(_COLLISION_TITLE, "Current B body."),
    }
    _seed_collision_manifest(
        corpus,
        xml_by_doc_id,
        {
            "lov-a": f"{_COLLISION_SLUG}-2",
            "lov-b": _COLLISION_SLUG,
        },
    )
    _write_seed_embedding(_embedding_path(corpus, f"{_COLLISION_SLUG}-2"), marker=21)
    _write_seed_embedding(_embedding_path(corpus, _COLLISION_SLUG), marker=22)
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed collision sidecars"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    report = run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    assert report.unchanged_count == 2
    base_sidecar = _embedding_path(corpus, _COLLISION_SLUG)
    suffixed_sidecar = _embedding_path(corpus, f"{_COLLISION_SLUG}-2")
    assert base_sidecar.exists()
    assert suffixed_sidecar.exists()
    _assert_embedding_matches_markdown(
        base_sidecar,
        (corpus / "lover" / f"{_COLLISION_SLUG}.md").read_text(encoding="utf-8"),
    )
    _assert_embedding_matches_markdown(
        suffixed_sidecar,
        (corpus / "lover" / f"{_COLLISION_SLUG}-2.md").read_text(encoding="utf-8"),
    )

    staged_sidecars = [
        path
        for action in captured_actions
        for path in action.sidecar_paths
        if path.suffix == ".bin"
    ]
    assert sorted(staged_sidecars) == sorted({base_sidecar, suffixed_sidecar})


def test_run_sync_three_way_collision_cycle_preserves_embedding_sidecars(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)
    _install_fake_embedder(monkeypatch)
    captured_actions: list[_DocAction] = []
    original_commit = orchestrator_module._commit_with_history

    def capture_commit(*args: object, **kwargs: object) -> object:
        captured_actions.extend(kwargs["actions"])
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "_commit_with_history", capture_commit)

    xml_by_doc_id = {
        "lov-a": _law_with_section(_COLLISION_TITLE, "Current A body."),
        "lov-b": _law_with_section(_COLLISION_TITLE, "Current B body."),
        "lov-c": _law_with_section(_COLLISION_TITLE, "Current C body."),
    }
    _seed_collision_manifest(
        corpus,
        xml_by_doc_id,
        {
            "lov-a": f"{_COLLISION_SLUG}-2",
            "lov-b": f"{_COLLISION_SLUG}-3",
            "lov-c": _COLLISION_SLUG,
        },
    )
    for marker, slug in enumerate(
        [_COLLISION_SLUG, f"{_COLLISION_SLUG}-2", f"{_COLLISION_SLUG}-3"],
        start=31,
    ):
        _write_seed_embedding(_embedding_path(corpus, slug), marker=marker)
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed three-way sidecars"], cwd=corpus, check=True)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [(f"nl/{doc_id}.xml", xml) for doc_id, xml in xml_by_doc_id.items()],
    )
    _build_tarball(forskrifter_tar, [])
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    report = run_sync(
        Settings(
            data_dir=data_dir,
            lovverk_repo_path=corpus,
            openai_api_key="sk-test",
        ),
    )

    assert report.unchanged_count == 3
    expected_sidecars = {
        _embedding_path(corpus, _COLLISION_SLUG),
        _embedding_path(corpus, f"{_COLLISION_SLUG}-2"),
        _embedding_path(corpus, f"{_COLLISION_SLUG}-3"),
    }
    assert all(path.exists() for path in expected_sidecars)
    for sidecar in expected_sidecars:
        markdown = sidecar.with_suffix(".md")
        markdown = markdown.parent.parent / markdown.name
        _assert_embedding_matches_markdown(
            sidecar,
            markdown.read_text(encoding="utf-8"),
        )

    staged_sidecars = [
        path
        for action in captured_actions
        for path in action.sidecar_paths
        if path.suffix == ".bin"
    ]
    assert sorted(staged_sidecars) == sorted(expected_sidecars)


def test_run_sync_writes_index_files_for_both_datasets(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """End-to-end: a successful sync writes lover/INDEX.md and
    forskrifter/INDEX.md listing every current doc."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_extra("Skatteloven", "body"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    lover_index = corpus / "lover" / "INDEX.md"
    forskrifter_index = corpus / "forskrifter" / "INDEX.md"
    assert lover_index.exists()
    assert forskrifter_index.exists()
    assert "skatteloven" in lover_index.read_text(encoding="utf-8")
    assert "_0 current documents_" in forskrifter_index.read_text(encoding="utf-8")


def test_run_sync_seeds_empty_corpus_with_single_law(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    tar_dir = tmp_path / "tarballs"
    lover_tar = tar_dir / "lover.tar.bz2"
    forskrifter_tar = tar_dir / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-19990326-014.xml", _minimal_law_html("19990326-014", "Skatteloven"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)

    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    report = run_sync(settings)

    assert report.new_count == 1
    assert report.changed_count == 0
    assert report.removed_count == 0
    assert report.unchanged_count == 0

    md_path = corpus / "lover" / "skatteloven.md"
    assert md_path.exists()
    md = md_path.read_text(encoding="utf-8")
    assert 'title: "Skatteloven"' in md
    assert "# Skatteloven" in md

    manifest_path = corpus / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["version"] == 1
    assert "lov-19990326-014" in manifest["documents"]


def test_run_sync_is_idempotent_on_unchanged_state(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """HIGH regression guard: running sync twice without upstream changes
    produces 0 changed docs on the second run AND no new commit (previously
    the second run rewrote manifest.last_seen and created a sync commit).
    Codex PR #15 reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-17410217-000.xml", _minimal_law_html("17410217-000", "Vimpel"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    first = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert first.new_count == 1
    commit_count_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    second = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert second.new_count == 0
    assert second.changed_count == 0
    assert second.unchanged_count == 1
    assert _git_commit_count(corpus) == commit_count_after_first

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=corpus,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_per_document_commit_mode_creates_one_commit_per_change(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """In per-document mode each changed/new/removed/renamed doc gets
    its own commit, plus a final 'sync: update manifest' commit."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Stable body content."
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-1.xml", _law_with_extra("First", body)),
            ("nl/lov-2.xml", _law_with_extra("Second", body)),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="per-document",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    # Initial seed: 2 add commits + 1 manifest commit = 3
    assert _git_commit_count(corpus) == 3
    log = _git_log_subjects(corpus)
    assert "add(lov): first" in log
    assert "add(lov): second" in log
    assert "sync: update manifest" in log


def test_single_commit_mode_creates_bulk_commit_plus_history_followup(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Single mode produces TWO commits in Sprint 5+: the bulk
    docs+meta commit (the "single" semantic, unchanged) and a
    follow-up commit that adds per-act history. The follow-up is
    required because history extraction needs the docs commit to
    exist before ``git log`` can see it (chicken-and-egg). See
    decisions.md §12d."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [
            ("nl/lov-1.xml", _law_with_extra("Alpha", "body")),
            ("nl/lov-2.xml", _law_with_extra("Beta", "body")),
        ],
    )
    _build_tarball(forskrifter_tar, [])

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="single",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    assert _git_commit_count(corpus) == 2
    log = _git_log_subjects(corpus)
    assert "sync: 2 new" in log
    assert "sync: update history for 2 documents" in log


def test_migration_creates_bulk_commit_plus_history_followup_in_per_document_mode(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Sprint 3 -> Sprint 4 transition: a manifest with slug=None
    records triggers a single 'migration: rename ...' commit covering
    all renames + manifest + INDEX, overriding per-document mode.
    Sprint 5 PR-B added a second commit ('sync: update history for N
    documents') because history extraction has to wait for the
    migration commit to land before ``git log`` can see it."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Body that does not change."
    xml = _law_with_extra("Skattie", body)
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-x.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-3-style manifest: same hash, no slug,
    # markdown_path uses old doc_id naming.
    legacy_path = corpus / "lover" / "lov-x.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Legacy file\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 25, tzinfo=UTC),
        documents={
            "lov-x": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/lov-x.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 25, tzinfo=UTC),
                status="current",
                # slug=None and title=None — Sprint 3 record
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "Sprint 3 seed"], cwd=corpus, check=True)
    commits_before = _git_commit_count(corpus)

    settings = Settings(
        data_dir=data_dir,
        lovverk_repo_path=corpus,
        git_commit_mode="per-document",
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)

    # Migration commit + history follow-up commit = 2 new commits.
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "migration: rename" in log
    assert "sync: update history for 1 documents" in log

    assert (corpus / "lover" / "skattie.md").exists()
    assert not (corpus / "lover" / "lov-x.md").exists()


def _git_log_subjects(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_collision_resolution_is_scoped_per_dataset(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: a law and a regulation that slugify to the
    same name must coexist as lover/<slug>.md and forskrifter/<slug>.md.
    They live in different subdirectories so there is no real filename
    conflict. Codex PR #17 reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    body = "body"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_extra("Skatteloven", body))],
    )
    _build_tarball(
        forskrifter_tar,
        [("sf/sf-1.xml", _law_with_extra("Skatteloven", body))],
    )

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Both keep bare 'skatteloven' — different subdirs, no conflict.
    assert (corpus / "lover" / "skatteloven.md").exists()
    assert (corpus / "forskrifter" / "skatteloven.md").exists()
    # Confirm no avoidable -2 suffix was applied.
    assert not (corpus / "lover" / "skatteloven-2.md").exists()
    assert not (corpus / "forskrifter" / "skatteloven-2.md").exists()


def test_tombstone_preserves_slug_and_title_for_audit_trail(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """LOW regression guard: when a doc is removed upstream, its
    manifest tombstone must keep slug and title (along with the other
    historical fields) so the audit trail and any downstream INDEX-
    style historical view remain reconstructable. Codex PR #17
    reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _law_with_extra("Disappearingloven", "body"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["documents"]["lov-gone"]
    assert record["status"] == "removed"
    assert record["slug"] == "disappearingloven"
    assert record["title"] == "Disappearingloven"


def test_run_sync_renames_when_upstream_slug_changes(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """If Lovdata renames a kortform (or fixes a typo in title), the
    same content gets a different slug. The orchestrator must delete
    the old path and write the new path even though xml_hash is
    unchanged. Migration from a Sprint 3 manifest with slug=None goes
    through the same code path."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    body = "Same body, only the title (and thus slug) changes."
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Oldname", body))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert (corpus / "lover" / "oldname.md").exists()

    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Newname", body))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert not (corpus / "lover" / "oldname.md").exists()
    assert (corpus / "lover" / "newname.md").exists()


def test_run_sync_retains_removed_docs_as_tombstones(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: a removed doc must remain in the manifest
    with status='removed' rather than vanishing. Preserves the audit
    trail and matches the contract read by detect_changes. Codex PR #15
    reproducer."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _minimal_law_html("gone", "Goes Away"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert "lov-gone" in manifest["documents"]
    assert manifest["documents"]["lov-gone"]["status"] == "removed"


def _git_commit_count(repo: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def test_run_sync_detects_and_commits_changed_document(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Content change with stable slug: same title → same slug → file
    is overwritten in place (no rename). Verifies that change_detector
    + orchestrator correctly updates only the file content."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Stableloven", "First version note."))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    _build_tarball(
        lover_tar,
        [("nl/lov-x.xml", _law_with_extra("Stableloven", "Second version note."))],
    )
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.changed_count == 1
    assert report.new_count == 0
    md = (corpus / "lover" / "stableloven.md").read_text(encoding="utf-8")
    assert "Second version note" in md
    assert "First version note" not in md


def test_run_sync_removes_disappearing_document(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-gone.xml", _minimal_law_html("gone", "To Be Removed"))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))
    assert (corpus / "lover" / "to-be-removed.md").exists()

    _build_tarball(lover_tar, [])  # upstream dropped the doc
    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    report = run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    assert report.removed_count == 1
    assert not (corpus / "lover" / "to-be-removed.md").exists()


def test_run_sync_raises_config_error_when_corpus_not_a_git_repo(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "not-a-repo"
    corpus.mkdir()
    settings = Settings(
        data_dir=tmp_path / "data",
        lovverk_repo_path=corpus,
    )
    with pytest.raises(ConfigError, match="not a git repository"):
        run_sync(settings)


def test_run_sync_raises_config_error_on_missing_upstream_archive(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """If Lovdata's /list catalogue no longer includes one of our tracked
    datasets, that's a configuration mismatch we should surface loudly
    rather than silently skip."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    _build_tarball(lover_tar, [])

    # Catalogue missing gjeldende-sentrale-forskrifter
    catalogue: list[dict[str, Any]] = [
        {
            "filename": "gjeldende-lover.tar.bz2",
            "description": "Gjeldende lover",
            "sizeBytes": str(lover_tar.stat().st_size),
            "lastModified": "2026-04-22T01:31:00Z",
        },
    ]
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/list",
        json=catalogue,
    )
    httpx_mock.add_response(
        url=f"{DEFAULT_BASE_URL}/get/gjeldende-lover.tar.bz2",
        content=lover_tar.read_bytes(),
    )

    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    with pytest.raises(ConfigError, match="missing expected archive"):
        run_sync(settings)


# ---------- Sprint 5: per-act history generation ----------


def test_history_files_generated_for_added_doc_in_per_document_mode(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A normal incremental sync in per-doc mode (default) writes
    history/<slug>.json + history/<slug>.md alongside the doc, and
    bundles them into the final 'sync: update manifest, index, and
    history' commit."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    history_json = corpus / "lover" / "history" / "skattie.json"
    history_md = corpus / "lover" / "history" / "skattie.md"
    assert history_json.exists()
    assert history_md.exists()

    payload = json.loads(history_json.read_text(encoding="utf-8"))
    assert payload["slug"] == "skattie"
    assert payload["doc_id"] == "lov-1"
    assert payload["schema_version"] == 1
    assert len(payload["events"]) >= 1
    assert payload["events"][0]["type"] == "added"

    log = _git_log_subjects(corpus)
    assert "sync: update manifest, index, and history" in log


def test_manifest_records_total_changes_and_last_changed_after_sync(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """After a sync that adds a doc, the manifest record for that doc
    carries Sprint 5 history metadata so future MCP-style queries
    (e.g. list_recent_changes) can sort without loading every
    history.json."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    manifest = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["documents"]["lov-1"]
    assert record["total_changes"] >= 1
    # last_changed is an ISO date string like "2026-04-27"
    assert isinstance(record["last_changed"], str)
    assert len(record["last_changed"]) == 10
    assert record["last_changed"][4] == "-"


def test_sprint5_history_migration_triggers_on_first_sync_after_prb(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A pre-Sprint-5 corpus (manifest with current docs but no
    history/ dirs anywhere) gets a one-time 'migration: generate
    history for N documents' commit on the first sync after PR-B
    ships, before any regular sync work for that day."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    body = "Body that does not change."
    xml = _law_with_extra("Skattie", body)
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-4-style manifest: slug populated but no
    # history/ directory on disk.
    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing file\n", encoding="utf-8")
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "Sprint 4 seed"], cwd=corpus, check=True)
    commits_before = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Two migrations fire on this Sprint-4 baseline: Sprint 5 backfills
    # history (no history/ dir on disk), Sprint 8 backfills eu_basis
    # (no eu_basis field on the seeded record). Upstream is unchanged
    # so no regular sync commit follows.
    assert _git_commit_count(corpus) == commits_before + 2
    log = _git_log_subjects(corpus)
    assert "migration: generate history for 1 documents" in log
    assert "migration: backfill eu_basis for 1 documents" in log
    assert (corpus / "lover" / "history" / "skattie.json").exists()


def test_sprint5_history_migration_skipped_when_history_dirs_already_exist(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A Sprint-5-ready corpus (history/ already populated) must NOT
    re-run the migration on every sync — the no-op contract from
    decisions.md §5 still holds when upstream is unchanged."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", _law_with_extra("Skattie", "body"))])
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    run_sync(settings)  # first sync populates history/ for both datasets
    commits_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)  # second sync: upstream unchanged, history exists

    assert _git_commit_count(corpus) == commits_after_first
    log = _git_log_subjects(corpus)
    assert "migration: generate history" not in log


def test_migration_triggers_return_false_for_empty_manifest(tmp_path: Path) -> None:
    empty = Manifest(
        generated_at=datetime(2026, 5, 1, tzinfo=UTC),
        documents={},
    )

    assert orchestrator_module._needs_sprint5_history_migration(tmp_path, empty) is False
    assert orchestrator_module._needs_sprint8_eu_basis_migration(empty) is False


def test_sprint5_history_migration_filters_targets_and_commits_exact_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    corpus.mkdir()
    manifest_path = corpus / "manifest.json"
    now = datetime(2026, 5, 1, tzinfo=UTC)
    xml = _law_with_section("History", "Body.")
    prior = Manifest(
        generated_at=now,
        documents={
            "lov-current": _current_law_record(xml=xml, slug="current", title="Current"),
            "lov-removed": _current_law_record(
                xml=xml,
                slug="removed",
                title="Removed",
                status="removed",
            ),
            "lov-slugless": _current_law_record(
                xml=xml,
                slug=None,
                markdown_path="lover/slugless.md",
                title="Slugless",
            ),
        },
    )
    history_paths = [
        corpus / "lover" / "history" / "current.json",
        corpus / "lover" / "history" / "current.md",
    ]
    staged_calls: list[list[Path]] = []
    messages: list[str] = []

    def fake_generate_and_apply_history(
        repo: Path,
        records: dict[str, ManifestRecord],
        target_doc_ids: list[str],
    ) -> tuple[dict[str, ManifestRecord], list[Path]]:
        assert repo == corpus
        assert records == prior.documents
        assert target_doc_ids == ["lov-current"]
        return records, history_paths

    monkeypatch.setattr(
        orchestrator_module,
        "_generate_and_apply_history",
        fake_generate_and_apply_history,
    )
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: staged_calls.append(list(paths)),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    result = orchestrator_module._run_sprint5_history_migration(
        corpus,
        manifest_path,
        prior,
        now,
    )

    assert result.documents == prior.documents
    assert staged_calls == [[manifest_path, *history_paths]]
    assert messages == ["migration: generate history for 1 documents"]


def test_generate_and_apply_history_skips_non_targets_without_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "lovverk"
    repo.mkdir()
    xml = _law_with_section("History", "Body.")
    records = {
        "lov-removed": _current_law_record(
            xml=xml,
            slug="removed",
            title="Removed",
            status="removed",
        ),
        "lov-slugless": _current_law_record(
            xml=xml,
            slug=None,
            markdown_path="lover/slugless.md",
            title="Slugless",
        ),
        "lov-current": _current_law_record(xml=xml, slug="current", title="Current"),
    }
    extracted: list[tuple[str, str, str]] = []

    def fake_extract_history(
        *,
        repo_path: Path,
        current_path: str,
        doc_id: str,
        slug: str,
    ) -> orchestrator_module.HistoryRecord:
        assert repo_path == repo
        assert doc_id == "lov-current"
        assert slug == "current"
        extracted.append((current_path, doc_id, slug))
        return orchestrator_module.HistoryRecord(slug=slug, doc_id=doc_id, events=[])

    def fake_write_history(
        history: orchestrator_module.HistoryRecord,
        target_dir: Path,
    ) -> tuple[Path, Path]:
        assert history.doc_id == "lov-current"
        assert target_dir == repo / "lover"
        return (
            target_dir / "history" / f"{history.slug}.json",
            target_dir / "history" / f"{history.slug}.md",
        )

    monkeypatch.setattr(orchestrator_module, "extract_history", fake_extract_history)
    monkeypatch.setattr(orchestrator_module, "write_history", fake_write_history)

    updated, written_paths = orchestrator_module._generate_and_apply_history(
        repo,
        records,
        ["lov-missing", "lov-removed", "lov-slugless", "lov-current"],
    )

    assert extracted == [("lover/current.md", "lov-current", "current")]
    assert written_paths == [
        repo / "lover" / "history" / "current.json",
        repo / "lover" / "history" / "current.md",
    ]
    assert updated["lov-removed"] == records["lov-removed"]
    assert updated["lov-slugless"] == records["lov-slugless"]
    assert updated["lov-current"].total_changes == 0


def _law_with_eea(title: str, celex_list: list[str]) -> bytes:
    """Variant of _minimal_law_html that adds a <dd class='eeaReferences'>
    block with one anchor per CELEX. Used by Sprint 8 PR-D tests so the
    fake upstream XML matches Lovdata's actual EEA-references shape."""
    anchors = "".join(f'<a href="eu/{celex.lower()}">label</a>' for celex in celex_list)
    return (
        '<!DOCTYPE html><html lang="nb"><head><title>'
        f"{title}</title></head>"
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt>'
        f'<dd class="title">{title}</dd>'
        '<dt class="refid">RefID</dt>'
        '<dd class="refid">lov/x</dd>'
        f'<dd class="eeaReferences">{anchors}</dd>'
        "</dl></header>"
        '<main id="dokument">'
        f"<h1>{title}</h1>"
        '<article class="legalP" id="ledd-1">body.</article>'
        "</main></body></html>"
    ).encode()


def test_sprint8_eu_basis_migration_skips_deferred_records_and_stages_exact_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    corpus = tmp_path / "lovverk"
    corpus.mkdir()
    manifest_path = corpus / "manifest.json"
    now = datetime(2026, 5, 1, tzinfo=UTC)
    base_xml = _law_with_eea("Base", [])
    write_xml = _law_with_eea("Write", ["32016R0679"])
    prior = Manifest(
        generated_at=now,
        documents={
            "lov-removed": _current_law_record(
                xml=base_xml,
                slug="removed",
                title="Removed",
                status="removed",
            ).model_copy(update={"eu_basis": None}),
            "lov-slugless": _current_law_record(
                xml=base_xml,
                slug=None,
                markdown_path="lover/slugless.md",
                title="Slugless",
            ).model_copy(update={"eu_basis": None}),
            "lov-missing-upstream": _current_law_record(
                xml=base_xml,
                slug="missing",
                title="Missing",
            ).model_copy(update={"eu_basis": None}),
            "lov-renamed": _current_law_record(
                xml=base_xml,
                slug="old-slug",
                title="Renamed",
            ).model_copy(update={"eu_basis": None}),
            "lov-write": _current_law_record(
                xml=write_xml,
                slug="write",
                title="Write",
            ).model_copy(update={"eu_basis": None}),
        },
    )
    upstream = {
        "lov-removed": orchestrator_module._UpstreamDoc(
            doc_id="lov-removed",
            source_dataset="gjeldende-lover",
            xml_bytes=base_xml,
            xml_hash=hash_normalized_xml(base_xml),
            slug="removed",
            title="Removed",
            eu_basis=(),
        ),
        "lov-slugless": orchestrator_module._UpstreamDoc(
            doc_id="lov-slugless",
            source_dataset="gjeldende-lover",
            xml_bytes=base_xml,
            xml_hash=hash_normalized_xml(base_xml),
            slug="slugless",
            title="Slugless",
            eu_basis=(),
        ),
        "lov-renamed": orchestrator_module._UpstreamDoc(
            doc_id="lov-renamed",
            source_dataset="gjeldende-lover",
            xml_bytes=base_xml,
            xml_hash=hash_normalized_xml(base_xml),
            slug="new-slug",
            title="Renamed",
            eu_basis=(),
        ),
        "lov-write": orchestrator_module._UpstreamDoc(
            doc_id="lov-write",
            source_dataset="gjeldende-lover",
            xml_bytes=write_xml,
            xml_hash=hash_normalized_xml(write_xml),
            slug="write",
            title="Write",
            eu_basis=("32016R0679",),
        ),
    }
    written_doc_ids: list[str] = []
    staged_calls: list[list[Path]] = []
    messages: list[str] = []

    def fake_write_one(
        settings: Settings,
        upstream_doc: orchestrator_module._UpstreamDoc,
        seen_at: datetime,
        embedder: object | None = None,
    ) -> tuple[ManifestRecord, list[Path]]:
        assert embedder is None
        written_doc_ids.append(upstream_doc.doc_id)
        path = settings.lovverk_repo_path / "lover" / f"{upstream_doc.slug}.md"
        record = _current_law_record(
            xml=upstream_doc.xml_bytes,
            slug=upstream_doc.slug,
            title=upstream_doc.title,
        ).model_copy(
            update={
                "eu_basis": list(upstream_doc.eu_basis),
                "last_seen": seen_at,
            },
        )
        return record, [path]

    monkeypatch.setattr(orchestrator_module, "_write_one", fake_write_one)
    monkeypatch.setattr(
        orchestrator_module,
        "git_add",
        lambda _repo, paths: staged_calls.append(list(paths)),
    )
    monkeypatch.setattr(orchestrator_module, "has_staged_changes", lambda _repo: True)
    monkeypatch.setattr(
        orchestrator_module,
        "git_commit_msg",
        lambda _repo, message: messages.append(message),
    )

    result = orchestrator_module._run_sprint8_eu_basis_migration(
        Settings(data_dir=tmp_path / "data", lovverk_repo_path=corpus),
        manifest_path,
        prior,
        upstream,
        now,
    )

    assert written_doc_ids == ["lov-write"]
    assert staged_calls == [[manifest_path, corpus / "lover" / "write.md"]]
    assert messages == ["migration: backfill eu_basis for 1 documents"]
    assert result.documents["lov-removed"] == prior.documents["lov-removed"]
    assert result.documents["lov-slugless"] == prior.documents["lov-slugless"]
    assert result.documents["lov-missing-upstream"] == prior.documents["lov-missing-upstream"]
    assert result.documents["lov-renamed"] == prior.documents["lov-renamed"]
    assert result.documents["lov-write"].eu_basis == ["32016R0679"]
    persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert persisted["documents"]["lov-write"]["eu_basis"] == ["32016R0679"]


def test_sprint8_eu_basis_migration_backfills_existing_corpus(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """A Sprint-7-or-earlier corpus (manifest with eu_basis=None on every
    current record) gets a one-time
    'migration: backfill eu_basis for N documents' commit on the first
    sync after PR-D ships. Re-renders the markdown so frontmatter
    carries the new field too. Subsequent syncs see populated
    eu_basis (possibly []) and skip the migration."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_eea("Skattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Pre-write a Sprint-7-style manifest: slug + history fields populated
    # but eu_basis omitted (defaults to None). Also seed history/ so
    # the Sprint 5 migration does not also fire.
    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing file\n", encoding="utf-8")
    history_dir = corpus / "lover" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "skattie.json").write_text("{}", encoding="utf-8")
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
                total_changes=1,
                last_changed="2026-04-27",
                # eu_basis omitted -> None -> Sprint 8 trigger
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Sprint 7 seed"],
        cwd=corpus,
        check=True,
    )
    commits_before = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Sprint 8 migration emits exactly one new commit; upstream xml_hash
    # matches the seeded manifest record so no regular sync work
    # follows.
    assert _git_commit_count(corpus) == commits_before + 1
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis for 1 documents" in log

    # Manifest record now carries the extracted CELEX.
    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert written["documents"]["lov-1"]["eu_basis"] == ["32016R0679"]

    # Frontmatter of the rendered markdown also carries it (re-render
    # is the whole point of the migration — manifest alone is not
    # enough; downstream MCP / search tools may read either source).
    body = (corpus / "lover" / "skattie.md").read_text(encoding="utf-8")
    assert "eu_basis:" in body
    assert "32016R0679" in body


def test_sprint8_eu_basis_migration_skipped_when_already_backfilled(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """After the first sync post-PR-D, every record carries eu_basis
    (possibly []). Subsequent syncs with unchanged upstream must not
    re-fire the backfill — the no-op contract from decisions.md §5
    still holds."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(
        lover_tar,
        [("nl/lov-1.xml", _law_with_eea("Skattie", ["32016R0679"]))],
    )
    _build_tarball(forskrifter_tar, [])

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    settings = Settings(data_dir=data_dir, lovverk_repo_path=corpus)
    run_sync(settings)  # initial sync populates eu_basis from upstream
    commits_after_first = _git_commit_count(corpus)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(settings)  # second sync: upstream unchanged

    assert _git_commit_count(corpus) == commits_after_first
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis" not in log


def test_sprint8_eu_basis_migration_records_empty_list_when_no_eea_block(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Acts whose upstream XML has no <dd class='eeaReferences'> block
    must still get eu_basis populated — as an empty list. Empty list
    is the canonical 'no EU basis' value; only None means 'pre-Sprint-8
    record, unknown'."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_extra("Skattie", "body")  # no eeaReferences block
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=corpus, check=True)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    assert written["documents"]["lov-1"]["eu_basis"] == []


def test_sprint8_eu_basis_migration_preserves_tombstones(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """Removed records keep their pre-migration shape (eu_basis=None
    stays None) because their files do not exist on disk and the
    migration cannot re-render them. Reverse-lookup tools skip None
    records; dropping the record entirely would lose audit trail."""
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    xml = _law_with_eea("Skattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    legacy_path = corpus / "lover" / "skattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Existing\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/skattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="skattie",
                title="Skattie",
            ),
            "lov-old": ManifestRecord(
                doc_type="lov",
                xml_hash="b" * 64,
                markdown_path="lover/old.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="removed",
                slug="old",
                title="Old",
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=corpus, check=True)

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    # Current doc gets backfilled.
    assert written["documents"]["lov-1"]["eu_basis"] == ["32016R0679"]
    # Tombstone keeps its pre-migration shape — eu_basis omitted entirely
    # (Pydantic excludes None defaults from model_dump unless asked
    # otherwise) or null.
    tombstone = written["documents"]["lov-old"]
    assert tombstone.get("eu_basis") is None


def test_sprint8_eu_basis_migration_defers_slug_renames_to_rename_flow(
    tmp_path: Path,
    httpx_mock: HTTPXMock,
) -> None:
    """MEDIUM regression guard: when a pre-Sprint-8 record has both
    eu_basis=None AND an upstream slug change (Lovdata renamed the
    kortform), the backfill must NOT rewrite the file at the new slug
    path — that would update prior.markdown_path to the new path and
    the subsequent rename detector would see prior.slug already
    matching upstream.slug and skip the rename, orphaning the old
    <old-slug>.md on disk.

    Expected behavior: Sprint 8 migration skips the record (defers to
    the rename flow), the rename flow writes the new path AND deletes
    the old, and _write_one populates eu_basis as a side effect.
    Codex PR-D round 1 reproducer.
    """
    data_dir = tmp_path / "data"
    corpus = tmp_path / "lovverk"
    _git_init_corpus(corpus)

    # Upstream XML derives slug "newskattie" from its title.
    xml = _law_with_eea("Newskattie", ["32016R0679"])
    lover_tar = tmp_path / "tarballs" / "lover.tar.bz2"
    forskrifter_tar = tmp_path / "tarballs" / "forskrifter.tar.bz2"
    _build_tarball(lover_tar, [("nl/lov-1.xml", xml)])
    _build_tarball(forskrifter_tar, [])

    # Seed a Sprint-7 manifest with the OLD slug "oldskattie" but the
    # SAME xml_hash that the upstream XML produces (Lovdata changed
    # the kortform, not the body).
    legacy_path = corpus / "lover" / "oldskattie.md"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text("# Legacy old-slug file\n", encoding="utf-8")
    (corpus / "lover" / "history").mkdir(parents=True, exist_ok=True)
    (corpus / "forskrifter" / "history").mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        generated_at=datetime(2026, 4, 27, tzinfo=UTC),
        documents={
            "lov-1": ManifestRecord(
                doc_type="lov",
                xml_hash=hash_normalized_xml(xml),
                markdown_path="lover/oldskattie.md",
                source_dataset="gjeldende-lover",
                last_seen=datetime(2026, 4, 27, tzinfo=UTC),
                status="current",
                slug="oldskattie",
                title="Oldskattie",
                # eu_basis omitted -> None -> would have triggered the
                # buggy Sprint 8 rewrite at the new slug path.
            ),
        },
    )
    write_manifest(manifest, corpus / "manifest.json")
    subprocess.run(["git", "add", "."], cwd=corpus, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Sprint 7 seed (slug-renamed upstream)"],
        cwd=corpus,
        check=True,
    )

    _register_lovdata_mocks(httpx_mock, lover_tar, forskrifter_tar)
    run_sync(Settings(data_dir=data_dir, lovverk_repo_path=corpus))

    # Old slug file is gone; new slug file exists.
    assert not (corpus / "lover" / "oldskattie.md").exists()
    assert (corpus / "lover" / "newskattie.md").exists()

    # Manifest record carries the new slug AND the extracted CELEX —
    # rename flow's _write_one populated eu_basis as a side effect.
    written = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))
    record = written["documents"]["lov-1"]
    assert record["slug"] == "newskattie"
    assert record["markdown_path"] == "lover/newskattie.md"
    assert record["eu_basis"] == ["32016R0679"]

    # No standalone backfill commit — the rename flow handled it.
    log = _git_log_subjects(corpus)
    assert "migration: backfill eu_basis" not in log
    assert "rename(lov): newskattie" in log
