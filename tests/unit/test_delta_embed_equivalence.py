"""Delta embedding against a full rebuild on identical pinned inputs (#919, PR #933).

The change path carries committed int8 rows instead of paying for them. This
module syncs a corpus state S0 in full, applies S0 -> S1 incrementally (a
modified law whose chunk count grows, an added law, a removed law, a moved
law, an unchanged law), full-rebuilds S1 from an empty corpus with the same
deterministic adapter, and compares the stored arrays, never search results.

What is exact: section ids, chunk text, Markdown, manifest identity fields,
the removed law's sidecar, coverage, and every sidecar whose document was not
re-quantized. What is not: the LSPE format keeps one int8 scale per file, and
the delta path either keeps the prior scale (``_quantize_keeping``) or
re-quantizes already-quantized rows. Both are pinned below by their exact
error bound, so a larger drift fails, and by an assertion that the bytes do
differ, so the day the delta path becomes byte-exact this module says so.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.inputs import build_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.embeddings.store import EmbeddingFile, read_embeddings
from lovspor.errors import CorpusStateError, MassReembedError
from lovspor.settings import Settings
from lovspor.storage.manifest import read_manifest
from lovspor.sync.document_io import embeddings_path
from lovspor.sync.orchestrator import _UpstreamDoc, run_sync
from tests.unit.test_embedding_space_identity import _FakeEmbedder
from tests.unit.test_sync_change_reembed_guard import _state
from tests.unit.test_sync_orchestrator import _disable_migrations, _settings

_DIM = 64
_FROZEN = datetime(2026, 11, 15, 3, 0, tzinfo=UTC)
_DATASET = "gjeldende-lover"


class _FrozenDatetime(datetime):
    """The orchestrator's clock, pinned so both runs stamp identical Markdown."""

    @classmethod
    def now(cls, tz: object = None) -> "_FrozenDatetime":  # type: ignore[override]
        return cls.fromtimestamp(_FROZEN.timestamp(), tz=UTC)


def _unit_vector(text: str) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    raw = np.random.Generator(np.random.PCG64(seed)).standard_normal(_DIM)
    return (raw / np.linalg.norm(raw)).astype(np.float32)


class _PinnedEmbedder(_FakeEmbedder):
    """A pure function of the text, like a pinned model; fails after ``fail_after`` calls."""

    def __init__(self, fail_after: int | None = None) -> None:
        super().__init__(dim=_DIM)
        self.paid: list[str] = []
        self.calls = 0
        self._fail_after = fail_after

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._fail_after is not None and self.calls >= self._fail_after:
            raise OSError("injected provider failure")
        self.calls += 1
        self.paid.extend(texts)
        return np.vstack([_unit_vector(text) for text in texts])


def _body(doc_id: str, n: int) -> str:
    return f"Paragraf {n} i {doc_id} gjelder for hele riket og for norske skip, variant {n}."


def _doc(doc_id: str, bodies: tuple[str, ...], slug: str | None = None) -> _UpstreamDoc:
    articles = "".join(
        f'<article class="legalArticle"><h2 class="legalArticleHeader">'
        f'<span class="legalArticleValue">§ {n}</span>. '
        f'<span class="legalArticleTitle">Paragraf {n}</span></h2>'
        f'<article class="legalP" id="ledd-{n}">{body}</article></article>'
        for n, body in enumerate(bodies, start=1)
    )
    title = f"Lov {doc_id}"
    xml = (
        f'<!DOCTYPE html><html lang="nb"><head><title>{title}</title></head>'
        '<body><header class="documentHeader"><dl>'
        f'<dt class="title">Tittel</dt><dd class="title">{title}</dd>'
        f'<dt class="refid">RefID</dt><dd class="refid">lov/{doc_id}</dd>'
        f'</dl></header><main id="dokument"><h1>{title}</h1>{articles}</main></body></html>'
    ).encode()
    return _UpstreamDoc(
        doc_id=doc_id,
        source_dataset=_DATASET,
        xml_bytes=xml,
        xml_hash=hashlib.sha256(xml).hexdigest(),
        slug=slug or doc_id,
        title=title,
        eu_basis=(),
    )


def _bodies(doc_id: str, numbers: range | tuple[int, ...]) -> tuple[str, ...]:
    return tuple(_body(doc_id, n) for n in numbers)


def _s0() -> dict[str, _UpstreamDoc]:
    docs = (
        _doc("lov-mod", _bodies("lov-mod", range(1, 4))),
        _doc("lov-del", _bodies("lov-del", range(1, 3))),
        _doc("lov-move", _bodies("lov-move", range(1, 3)), slug="lov-move"),
        _doc("lov-same", _bodies("lov-same", range(1, 3))),
        _doc("lov-grow", _bodies("lov-grow", range(1, 3))),
    )
    return {doc.doc_id: doc for doc in docs}


_LOUDER = "Ny paragraf, variant 0, om tilsyn."
"""A paid section whose largest component exceeds lov-grow's S0 maximum (0.341 > 0.295)."""


def _s1() -> dict[str, _UpstreamDoc]:
    changed = (
        _body("lov-mod", 1),
        "Paragraf 2 er endret ved lov 2026 nr. 48.",
        _body("lov-mod", 3),
    )
    docs = (
        _doc("lov-mod", (*changed, _body("lov-mod", 4), _body("lov-mod", 5))),
        _doc("lov-move", _bodies("lov-move", range(1, 3)), slug="lov-moved"),
        _doc("lov-same", _bodies("lov-same", range(1, 3))),
        _doc("lov-new", _bodies("lov-new", range(1, 4))),
        _doc("lov-grow", (*_bodies("lov-grow", range(1, 3)), _LOUDER)),
    )
    return {doc.doc_id: doc for doc in docs}


def _sync(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    upstream: dict[str, _UpstreamDoc],
    embedder: _PinnedEmbedder,
) -> None:
    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_a: (upstream, ()))
    run_sync(settings)


def _sidecars(repo: Path) -> dict[str, bytes]:
    folder = embeddings_path(repo, _DATASET, "x").parent
    return {path.stem: path.read_bytes() for path in sorted(folder.glob("*.bin"))}


def _markdown(repo: Path) -> dict[str, str]:
    records = read_manifest(repo / "manifest.json").documents
    return {
        doc_id: (repo / r.markdown_path).read_text(encoding="utf-8")
        for doc_id, r in records.items()
        if r.status == "current"
    }


_IDENTITY_FIELDS = ("status", "slug", "xml_hash", "embedding_hash", "embedding_input_hash")


def _identity(repo: Path) -> dict[str, tuple[object, ...]]:
    records = read_manifest(repo / "manifest.json").documents
    return {
        doc_id: (*(getattr(r, name) for name in _IDENTITY_FIELDS), r.embedding_space_id)
        for doc_id, r in records.items()
        if r.status == "current"
    }


@dataclass(frozen=True)
class _Corpora:
    delta: Path
    full: Path
    delta_embedder: _PinnedEmbedder
    full_embedder: _PinnedEmbedder


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Corpora:
    incremental = _settings(tmp_path / "delta")
    _sync(incremental, monkeypatch, _s0(), _PinnedEmbedder())
    delta_embedder = _PinnedEmbedder()
    _sync(incremental, monkeypatch, _s1(), delta_embedder)
    rebuilt = _settings(tmp_path / "full")
    full_embedder = _PinnedEmbedder()
    _sync(rebuilt, monkeypatch, _s1(), full_embedder)
    return _Corpora(
        incremental.lovverk_repo_path, rebuilt.lovverk_repo_path, delta_embedder, full_embedder
    )


def _read(repo: Path, slug: str) -> EmbeddingFile:
    return read_embeddings(embeddings_path(repo, _DATASET, slug))


def _dequantized(sidecar: EmbeddingFile) -> np.ndarray:
    return np.vstack([row.astype(np.float64) for _, row in sidecar.sections]) * sidecar.scale


# --- what must be exact ----------------------------------------------------


def test_delta_and_full_rebuild_agree_on_ids_text_metadata_and_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)

    assert _markdown(corpora.delta) == _markdown(corpora.full)
    assert _identity(corpora.delta) == _identity(corpora.full)
    delta, full = _sidecars(corpora.delta), _sidecars(corpora.full)
    assert (
        sorted(delta) == sorted(full) == ["lov-grow", "lov-mod", "lov-moved", "lov-new", "lov-same"]
    )
    for slug, markdown in _markdown(corpora.full).items():
        inputs = build_embedding_inputs(markdown)
        for repo in (corpora.delta, corpora.full):
            sidecar = _read(repo, slug if slug != "lov-move" else "lov-moved")
            assert [s for s, _ in sidecar.sections] == [i.section_id for i in inputs]


def test_the_removed_law_leaves_no_vectors_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)

    for repo in (corpora.delta, corpora.full):
        assert not embeddings_path(repo, _DATASET, "lov-del").exists()
        assert not embeddings_path(repo, _DATASET, "lov-move").exists()
        assert _state(repo)[1] == ""


def test_documents_the_delta_did_not_requantize_are_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)
    delta, full = _sidecars(corpora.delta), _sidecars(corpora.full)

    for slug in ("lov-same", "lov-moved", "lov-new"):
        assert delta[slug] == full[slug], slug


def test_the_delta_pays_only_for_new_text_and_the_full_rebuild_for_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)
    s1_inputs = {
        doc_id: build_embedding_inputs(md) for doc_id, md in _markdown(corpora.full).items()
    }
    new_mod_texts = [i.text for i in s1_inputs["lov-mod"][1:2] + s1_inputs["lov-mod"][3:]]

    assert sorted(corpora.delta_embedder.paid) == sorted(
        [*new_mod_texts, s1_inputs["lov-grow"][2].text, *(i.text for i in s1_inputs["lov-new"])]
    )
    assert sorted(corpora.full_embedder.paid) == sorted(
        i.text for inputs in s1_inputs.values() for i in inputs
    )


# --- the drift the single-scale format admits, pinned exactly ---------------


@dataclass(frozen=True)
class _Modified:
    prior: EmbeddingFile
    delta: EmbeddingFile
    full: EmbeddingFile
    truth: np.ndarray


def _modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Modified:
    corpora = _build(tmp_path, monkeypatch)
    s0 = _settings(tmp_path / "s0-only")
    _sync(s0, monkeypatch, _s0(), _PinnedEmbedder())
    inputs = build_embedding_inputs(_markdown(corpora.full)["lov-mod"])
    return _Modified(
        prior=_read(s0.lovverk_repo_path, "lov-mod"),
        delta=_read(corpora.delta, "lov-mod"),
        full=_read(corpora.full, "lov-mod"),
        truth=np.vstack([_unit_vector(i.text) for i in inputs]).astype(np.float64),
    )


def test_the_modified_law_drifts_from_the_full_rebuild_within_its_proven_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delta and full rebuild each stay within their rounding of the true vectors.

    Here the changed section held the document's largest component, so the
    delta keeps the prior, coarser scale ``s0`` (``_quantize_keeping``) while
    the full rebuild derives a finer one from S1: every row sits on another
    int8 grid. Full: one rounding, |full - truth| <= s_full/2. Delta: a carried
    row was rounded at ``s0`` and, after a rescale, rounded again at
    ``s_delta``; so |delta - truth| <= (s0 + s_delta)/2.
    """
    m = _modified(tmp_path, monkeypatch)

    assert m.delta.scale == m.prior.scale > m.full.scale
    assert np.abs(_dequantized(m.full) - m.truth).max() <= m.full.scale / 2 * (1 + 1e-6)
    bound = (m.prior.scale + m.delta.scale) / 2 * (1 + 1e-6)
    assert np.abs(_dequantized(m.delta) - m.truth).max() <= bound


def test_a_louder_paid_row_rescales_carried_rows_off_the_full_rebuild_by_one_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rescale branch: same scale as the rebuild, carried rows rounded twice.

    The paid row sets the new scale exactly as in the full rebuild, so it and
    the scale agree. Each carried row was rounded at the S0 scale, then
    re-rounded at the new one: double rounding moves some components by one
    int8 unit (749e4163's "within half a step" is measured against the prior
    dequantized row, never against the rebuild or the true vector).
    """
    corpora = _build(tmp_path, monkeypatch)
    delta, full = _read(corpora.delta, "lov-grow"), _read(corpora.full, "lov-grow")
    a = np.vstack([row for _, row in delta.sections]).astype(np.int16)
    b = np.vstack([row for _, row in full.sections]).astype(np.int16)

    assert delta.scale == full.scale
    assert np.array_equal(a[2], b[2])
    assert int(np.abs(a - b).max()) == 1
    assert np.count_nonzero(a[:2] != b[:2]) > 0


@pytest.mark.xfail(
    strict=True,
    reason="PR #933: one int8 scale per LSPE file; the delta keeps the prior scale or "
    "re-quantizes carried int8 rows, so a re-quantized document is not the full rebuild's bytes",
)
def test_the_modified_law_is_byte_identical_to_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    m = _modified(tmp_path, monkeypatch)

    assert m.delta.scale == m.full.scale
    for (_, a), (_, b) in zip(m.delta.sections, m.full.sections, strict=True):
        assert np.array_equal(a, b)


# --- interruption, retry, publication ---------------------------------------


def test_a_failure_mid_embed_publishes_nothing_and_blocks_a_dirty_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "crash")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    head = _state(repo)[0]
    published = _sidecars(repo)

    with pytest.raises(OSError, match="injected provider failure"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder(fail_after=1))

    assert _state(repo)[0] == head
    committed = subprocess.run(
        ["git", "show", "HEAD:lover/embeddings/lov-mod.bin"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    assert committed == published["lov-mod"]
    with pytest.raises(CorpusStateError, match="uncommitted changes"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder())


def test_a_reset_retry_converges_to_the_uninterrupted_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "retry")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    head = _state(repo)[0].strip()
    with pytest.raises(OSError, match="injected provider failure"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder(fail_after=1))
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=repo, check=True)
    subprocess.run(["git", "clean", "-qfd"], cwd=repo, check=True)

    _sync(settings, monkeypatch, _s1(), _PinnedEmbedder())

    uninterrupted = _build(tmp_path / "reference", monkeypatch)
    assert _sidecars(repo) == _sidecars(uninterrupted.delta)
    assert _markdown(repo) == _markdown(uninterrupted.delta)


# --- the 1M-token ceiling per execution -------------------------------------


def test_the_token_ceiling_counts_the_whole_runs_paid_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "ceiling")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    probe = _build(tmp_path / "probe", monkeypatch)
    paid = sum(count_input_tokens(text) for text in probe.delta_embedder.paid)
    tight = settings.model_copy(update={"reembed_guard_max_tokens": paid - 1})
    before = _state(repo)
    embedder = _PinnedEmbedder()

    with pytest.raises(MassReembedError, match=f"token workload {paid:,} > {paid - 1:,}"):
        _sync(tight, monkeypatch, _s1(), embedder)

    assert embedder.paid == []
    assert _state(repo) == before
    assert Settings.model_fields["reembed_guard_max_tokens"].default == 1_000_000
