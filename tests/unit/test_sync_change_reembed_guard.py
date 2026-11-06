"""The mass-re-embed guard on the change path, before the first paid call (#790).

On 2026-10-20 and 10-21 Lovdata added an ELI row to every document header,
so all 5,886 documents hashed as changed (#679). The ADR-0006 guard covered
only the backfill migration, so the change path re-embedded the whole corpus
twice, and both runs were lost to the job timeout. Here every document of a
corpus above the guard's minimum size changes: a keyed sync must refuse with
no provider call, no file written and no commit.
"""

import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.inputs import build_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.errors import MassReembedError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, read_manifest, write_manifest
from lovspor.sync.document_io import document_path
from lovspor.sync.orchestrator import _UpstreamDoc, _write_one, run_sync
from tests.unit.test_embedding_space_identity import _FakeEmbedder
from tests.unit.test_sync_orchestrator import _disable_migrations, _git, _settings

_OBSERVED = datetime(2026, 8, 1, 4, 0, tzinfo=UTC)
_CORPUS = 100


class _CountingEmbedder(_FakeEmbedder):
    """The provider, counting every text it would have been paid for."""

    def __init__(self) -> None:
        super().__init__()
        self.texts = 0

    def encode(self, texts: list[str]) -> np.ndarray:
        self.texts += len(texts)
        return super().encode(texts)


def _xml(index: int, body: str) -> bytes:
    return (
        f'<!DOCTYPE html><html lang="nb"><head><title>Lov {index}</title></head>'
        f'<body><header class="documentHeader"><dl>'
        f'<dt class="title">Tittel</dt><dd class="title">Lov {index}</dd>'
        f'<dt class="refid">RefID</dt><dd class="refid">lov/{index}</dd>'
        f'</dl></header><main id="dokument"><h1>Lov {index}</h1>'
        f'<article class="legalArticle"><h2 class="legalArticleHeader">'
        f'<span class="legalArticleValue">§ 1</span>. '
        f'<span class="legalArticleTitle">Formål</span></h2>'
        f'<article class="legalP" id="ledd-1">{body}</article></article>'
        f"</main></body></html>"
    ).encode()


def _doc(index: int, body: str = "Body.") -> _UpstreamDoc:
    xml = _xml(index, body)
    return _UpstreamDoc(
        doc_id=f"lov-{index}",
        source_dataset="gjeldende-lover",
        xml_bytes=xml,
        xml_hash=f"{index:04d}{len(body):04d}".ljust(64, "a"),
        slug=f"lov-{index}",
        title=f"Lov {index}",
        eu_basis=(),
    )


def _corpus(tmp_path: Path, size: int) -> Settings:
    """A committed, fully embedded corpus of ``size`` laws."""
    settings = _settings(tmp_path)
    embedder = _FakeEmbedder()
    records = {}
    for index in range(size):
        seeded = orchestrator_module._with_retrieved_at(_doc(index), _OBSERVED)
        records[seeded.doc_id], _paths = _write_one(settings, seeded, _OBSERVED, embedder)
    repo = settings.lovverk_repo_path
    write_manifest(Manifest(generated_at=_OBSERVED, documents=records), repo / "manifest.json")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "publish")
    return settings


def _upstream(
    monkeypatch: pytest.MonkeyPatch, docs: list[_UpstreamDoc], embedder: _CountingEmbedder
) -> None:
    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    monkeypatch.setattr(
        orchestrator_module,
        "_collect_upstream",
        lambda *_args: ({doc.doc_id: doc for doc in docs}, ()),
    )


def _state(repo: Path) -> tuple[str, str]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, text=True
        ).stdout

    return run("rev-parse", "HEAD"), run("status", "--porcelain")


def test_every_document_changed_is_refused_before_any_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    repo = settings.lovverk_repo_path
    before = _state(repo)
    manifest = read_manifest(repo / "manifest.json")
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [_doc(i, "Body. ELI row.") for i in range(_CORPUS)], embedder)

    with pytest.raises(MassReembedError) as refused:
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(repo) == before
    assert read_manifest(repo / "manifest.json") == manifest
    message = str(refused.value)
    assert f"{_CORPUS} changed document(s)" in message
    assert "100.0% of 100 current documents" in message
    assert "No provider call was made" in message
    assert "--allow-mass-reembed" in message


def test_the_explicit_override_embeds_the_changed_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _corpus(tmp_path, 2).model_copy(update={"reembed_guard_max_tokens": 1})
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [_doc(i, "Body. ELI row.") for i in range(2)], embedder)

    report = run_sync(settings, allow_mass_reembed=True)

    assert report.changed_count == 2
    assert embedder.texts > 0
    assert "mass-reembed override active: 2 changed document(s)" in capsys.readouterr().err


def test_the_token_dimension_refuses_a_small_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, 2).model_copy(update={"reembed_guard_max_tokens": 1})
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [_doc(0, "Body. ELI row."), _doc(1)], embedder)

    with pytest.raises(MassReembedError, match="token workload"):
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(settings.lovverk_repo_path) == before


def test_an_ordinary_change_passes_and_embeds_only_that_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    embedder = _CountingEmbedder()
    docs = [_doc(0, "Body. Amended.")] + [_doc(i) for i in range(1, _CORPUS)]
    _upstream(monkeypatch, docs, embedder)

    report = run_sync(settings)

    assert report.changed_count == 1
    assert embedder.texts == 1


def test_a_forced_rerender_that_reproduces_every_file_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [_doc(i) for i in range(_CORPUS)], embedder)

    run_sync(settings, force_rerender=True)

    assert embedder.texts == 0
    assert _state(settings.lovverk_repo_path)[0] == before[0]


def test_a_keyless_sync_is_not_guarded_because_it_spends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    _disable_migrations(monkeypatch)
    docs = [_doc(i, "Body. ELI row.") for i in range(_CORPUS)]
    monkeypatch.setattr(
        orchestrator_module, "_collect_upstream", lambda *_args: ({d.doc_id: d for d in docs}, ())
    )

    report = run_sync(settings)

    assert report.changed_count == _CORPUS


def _tokens_on_disk(settings: Settings, count: int) -> int:
    total = 0
    for index in range(count):
        path = document_path(settings.lovverk_repo_path, "gjeldende-lover", f"lov-{index}")
        inputs = build_embedding_inputs(path.read_text(encoding="utf-8"))
        total += sum(count_input_tokens(item.text) for item in inputs)
    return total


def test_the_estimate_sums_every_documents_input_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _corpus(tmp_path, 3)
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [_doc(i, "Body. ELI row.") for i in range(3)], embedder)

    run_sync(settings, allow_mass_reembed=True)

    expected = _tokens_on_disk(settings, 3)
    assert expected > 3
    assert f"(100.0% of 3 current documents), ~{expected:,} input tokens" in capsys.readouterr().err


def test_every_document_renamed_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    renamed = [orchestrator_module._with_slug(_doc(i), f"lov-{i}-ny") for i in range(_CORPUS)]
    _upstream(monkeypatch, renamed, embedder)

    with pytest.raises(MassReembedError, match=f"{_CORPUS} changed document"):
        run_sync(settings)

    assert embedder.texts == 0
    assert _state(settings.lovverk_repo_path) == before


def test_a_corpus_worth_of_new_documents_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, _CORPUS)
    embedder = _CountingEmbedder()
    docs = [_doc(i) for i in range(2 * _CORPUS)]
    _upstream(monkeypatch, docs, embedder)

    with pytest.raises(MassReembedError, match=f"{_CORPUS} changed document"):
        run_sync(settings)

    assert embedder.texts == 0


def test_an_unrenderable_document_is_skipped_and_the_rest_still_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _corpus(tmp_path, 2).model_copy(update={"reembed_guard_max_tokens": 1})
    embedder = _CountingEmbedder()
    broken = replace(_doc(0), xml_bytes=b"<html", xml_hash="f" * 64)
    _upstream(monkeypatch, [broken, _doc(1, "Body. ELI row.")], embedder)

    with pytest.raises(MassReembedError, match="1 changed document"):
        run_sync(settings)

    assert embedder.texts == 0


def test_a_noop_rerender_keeps_the_prior_record_itself(tmp_path: Path) -> None:
    upstream = _doc(0)
    record, _paths = _write_one(_settings(tmp_path), upstream, _OBSERVED, None)
    seeded = orchestrator_module._with_retrieved_at(upstream, record.last_seen)

    assert orchestrator_module._noop_rerender_record(record, seeded) is record


@pytest.mark.parametrize("max_fraction", [0.02, 0.03])
def test_new_changed_and_renamed_documents_share_one_fraction_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, max_fraction: float
) -> None:
    """The whole-run scope includes all three write loops, including at equality."""
    settings = _corpus(tmp_path, _CORPUS).model_copy(
        update={"reembed_guard_max_fraction": max_fraction}
    )
    repo = settings.lovverk_repo_path
    before = _state(repo)
    manifest_before = (repo / "manifest.json").read_bytes()
    embedder = _CountingEmbedder()
    docs = [
        _doc(0, "Body. Amended."),
        orchestrator_module._with_slug(_doc(1), "lov-1-renamed"),
        *[_doc(i) for i in range(2, _CORPUS + 1)],
    ]
    _upstream(monkeypatch, docs, embedder)

    if max_fraction == 0.02:
        with pytest.raises(MassReembedError, match="3 changed document"):
            run_sync(settings)
        assert embedder.texts == 0
        assert _state(repo) == before
        assert (repo / "manifest.json").read_bytes() == manifest_before
    else:
        run_sync(settings)
        assert embedder.texts == 3
        manifest = read_manifest(repo / "manifest.json")
        assert manifest.documents["lov-0"].xml_hash == docs[0].xml_hash
        assert manifest.documents["lov-1"].slug == "lov-1-renamed"
        assert "lov-100" in manifest.documents


@pytest.mark.parametrize("budget_offset", [0, -1])
def test_change_token_budget_accepts_equality_and_refuses_one_token_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget_offset: int
) -> None:
    settings = _corpus(tmp_path, 1)
    doc = _doc(0, "Body. Amended.")
    rendered = orchestrator_module.render_full_document(
        doc.xml_bytes, orchestrator_module._frontmatter_context(doc, _OBSERVED)
    )
    tokens = sum(count_input_tokens(item.text) for item in build_embedding_inputs(rendered))
    assert tokens > 1
    settings = settings.model_copy(update={"reembed_guard_max_tokens": tokens + budget_offset})
    before = _state(settings.lovverk_repo_path)
    embedder = _CountingEmbedder()
    _upstream(monkeypatch, [doc], embedder)

    if budget_offset == -1:
        with pytest.raises(MassReembedError, match="token workload"):
            run_sync(settings)
        assert embedder.texts == 0
        assert _state(settings.lovverk_repo_path) == before
    else:
        report = run_sync(settings)
        assert report.changed_count == 1
        assert embedder.texts == 1
