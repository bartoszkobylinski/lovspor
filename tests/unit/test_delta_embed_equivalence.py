"""Delta embedding against a full rebuild on identical pinned inputs (#919, #936).

The change path carries committed int8 rows instead of paying for them. This
module syncs a corpus state S0 in full, applies S0 -> S1 incrementally (a
modified law whose chunk count grows, an added law, a removed law, a moved
law, an unchanged law, a law gaining a section louder than its maximum),
full-rebuilds S1 from an empty corpus with the same deterministic adapter, and
compares the stored bytes, never search results.

The LSPE format keeps one int8 scale per file, so a carried row is the full
rebuild's row only while the document's scale is unchanged (#936). The delta
keeps a document's rows only when that is provable: the old row holding the
maximum is carried and no paid row needs a larger scale. Otherwise it re-pays
the whole document and quantizes it from its float vectors, exactly as a
rebuild does. Every sidecar of the delta is the full rebuild's, byte for byte.
"""

import hashlib
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import CACHE_DIR_VARIABLE, sync_embedder
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


def _unit_vector(text: str, dim: int = _DIM) -> np.ndarray:
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "little")
    raw = np.random.Generator(np.random.PCG64(seed)).standard_normal(dim)
    return (raw / np.linalg.norm(raw)).astype(np.float32)


class _PinnedEmbedder(_FakeEmbedder):
    """A pure function of the text, like a pinned model; fails when a text contains ``fail_on``."""

    def __init__(self, fail_on: str | None = None, dim: int = _DIM) -> None:
        super().__init__(dim=dim)
        self.paid: list[str] = []
        self.calls: list[list[str]] = []
        self._fail_on = fail_on

    def encode(self, texts: list[str]) -> np.ndarray:
        if self._fail_on is not None and any(self._fail_on in text for text in texts):
            raise OSError("injected provider failure")
        self.calls.append(list(texts))
        self.paid.extend(texts)
        return np.vstack([_unit_vector(text, self.get_dimension()) for text in texts])


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

_CHANGED_MAX = "Paragraf 2 er endret ved lov 2026 nr. 48."
"""Replaces lov-mod § 2, the S0 row that held lov-mod's largest component."""


def _s1() -> dict[str, _UpstreamDoc]:
    changed = (_body("lov-mod", 1), _CHANGED_MAX, _body("lov-mod", 3))
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
    """Run the real sync with ``embedder`` behind the real meter and, when configured, cache."""
    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "datetime", _FrozenDatetime)
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda s: sync_embedder(embedder, s))
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


def _full_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, bytes]:
    rebuilt = _settings(tmp_path)
    _sync(rebuilt, monkeypatch, _s1(), _PinnedEmbedder())
    return _sidecars(rebuilt.lovverk_repo_path)


def _read(repo: Path, slug: str) -> EmbeddingFile:
    return read_embeddings(embeddings_path(repo, _DATASET, slug))


def _texts(repo: Path, doc_id: str) -> list[str]:
    return [item.text for item in build_embedding_inputs(_markdown(repo)[doc_id])]


def _tokens(texts: list[str]) -> int:
    return sum(count_input_tokens(text) for text in texts)


def _committed(repo: Path, slug: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"HEAD:lover/embeddings/{slug}.bin"],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout


def _reset(repo: Path, head: str) -> None:
    """The operator's recovery the CorpusStateError refusal names."""
    subprocess.run(["git", "reset", "-q", "--hard", head], cwd=repo, check=True)
    subprocess.run(["git", "clean", "-qfd"], cwd=repo, check=True)


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


def test_every_sidecar_of_the_delta_is_byte_identical_to_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)

    assert _sidecars(corpora.delta) == _sidecars(corpora.full)


def test_the_modified_law_is_byte_identical_to_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)
    delta, full = _read(corpora.delta, "lov-mod"), _read(corpora.full, "lov-mod")

    assert delta.scale == full.scale
    for (_, a), (_, b) in zip(delta.sections, full.sections, strict=True):
        assert np.array_equal(a, b)


# --- what is paid, and why ---------------------------------------------------


def test_a_changed_row_that_held_the_maximum_makes_the_whole_document_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the old maximum the rebuild's scale is unknowable from int8 rows (case 1)."""
    s0 = _settings(tmp_path / "s0")
    _sync(s0, monkeypatch, _s0(), _PinnedEmbedder())
    prior = _read(s0.lovverk_repo_path, "lov-mod")
    corpora = _build(tmp_path / "pair", monkeypatch)

    assert int(np.abs(prior.sections[1][1].astype(np.int16)).max()) == 127
    assert _read(corpora.full, "lov-mod").scale < prior.scale
    assert [c for c in corpora.delta_embedder.calls if any(_CHANGED_MAX in t for t in c)] == [
        _texts(corpora.full, "lov-mod")
    ]


def test_a_louder_paid_row_re_pays_the_carried_rows_instead_of_re_rounding_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new scale would round already-rounded rows twice (case 2): their floats are paid."""
    corpora = _build(tmp_path, monkeypatch)
    grow = _texts(corpora.full, "lov-grow")

    assert _LOUDER in grow[2]
    assert [call for call in corpora.delta_embedder.calls if set(call) & set(grow)] == [
        grow[2:],
        grow[:2],
    ]
    assert _sidecars(corpora.delta)["lov-grow"] == _sidecars(corpora.full)["lov-grow"]


def test_the_delta_pays_new_text_and_re_pays_only_documents_it_cannot_keep_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpora = _build(tmp_path, monkeypatch)
    expected = [
        *_texts(corpora.full, "lov-mod"),
        *_texts(corpora.full, "lov-grow"),
        *_texts(corpora.full, "lov-new"),
    ]

    assert sorted(corpora.delta_embedder.paid) == sorted(expected)
    assert sorted(corpora.full_embedder.paid) == sorted(
        text for doc_id in _markdown(corpora.full) for text in _texts(corpora.full, doc_id)
    )


def test_a_cached_full_rebuild_after_a_delta_equals_an_uncached_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delta's own rows never become an entry, so a cached rebuild cannot serve them."""
    monkeypatch.setenv(CACHE_DIR_VARIABLE, str(tmp_path / "cache"))
    incremental = _settings(tmp_path / "delta")
    _sync(incremental, monkeypatch, _s0(), _PinnedEmbedder())
    _sync(incremental, monkeypatch, _s1(), _PinnedEmbedder())
    cached = _settings(tmp_path / "cached-full")
    cached_embedder = _PinnedEmbedder()
    _sync(cached, monkeypatch, _s1(), cached_embedder)
    monkeypatch.delenv(CACHE_DIR_VARIABLE)

    reference = _full_rebuild(tmp_path / "uncached-full", monkeypatch)

    assert _sidecars(incremental.lovverk_repo_path) == reference
    assert _sidecars(cached.lovverk_repo_path) == reference
    assert sorted(cached_embedder.paid) == sorted(_texts(cached.lovverk_repo_path, "lov-grow"))


# --- interruption, retry, publication ---------------------------------------


def test_a_failure_mid_embed_publishes_nothing_and_names_the_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "crash")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    head = _state(repo)[0]
    published = _sidecars(repo)

    with pytest.raises(OSError, match="injected provider failure"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder(fail_on=_body("lov-new", 1)))

    assert _state(repo)[0] == head
    assert _committed(repo, "lov-mod") == published["lov-mod"]
    with pytest.raises(CorpusStateError, match=r"git reset --hard.*git clean -fd"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder())


def test_a_failure_while_re_paying_leaves_the_sidecar_and_head_untouched_and_retry_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "repay-crash")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    head = _state(repo)[0].strip()
    published = _sidecars(repo)
    embedder = _PinnedEmbedder(fail_on=_body("lov-grow", 1))

    with pytest.raises(OSError, match="injected provider failure"):
        _sync(settings, monkeypatch, _s1(), embedder)

    assert any(_LOUDER in text for text in embedder.paid)
    assert _state(repo)[0].strip() == head
    assert _committed(repo, "lov-grow") == published["lov-grow"]
    assert _sidecars(repo)["lov-grow"] == published["lov-grow"]
    _reset(repo, head)
    _sync(settings, monkeypatch, _s1(), _PinnedEmbedder())
    assert _sidecars(repo) == _full_rebuild(tmp_path / "reference", monkeypatch)


def test_a_reset_retry_converges_to_the_full_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path / "retry")
    repo = settings.lovverk_repo_path
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    head = _state(repo)[0].strip()
    with pytest.raises(OSError, match="injected provider failure"):
        _sync(settings, monkeypatch, _s1(), _PinnedEmbedder(fail_on=_body("lov-new", 1)))
    _reset(repo, head)

    _sync(settings, monkeypatch, _s1(), _PinnedEmbedder())

    uninterrupted = _build(tmp_path / "reference", monkeypatch)
    assert _sidecars(repo) == _sidecars(uninterrupted.delta) == _sidecars(uninterrupted.full)
    assert _markdown(repo) == _markdown(uninterrupted.delta)


# --- the 1M-token ceiling per execution -------------------------------------


@dataclass(frozen=True)
class _Workload:
    before_spend: int
    re_paid: int


def _workload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Workload:
    probe = _build(tmp_path, monkeypatch)
    grow = _texts(probe.full, "lov-grow")
    known = [*_texts(probe.full, "lov-mod"), *_texts(probe.full, "lov-new"), grow[2]]
    return _Workload(before_spend=_tokens(known), re_paid=_tokens(grow[:2]))


def _ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tokens: int) -> Settings:
    settings = _settings(tmp_path)
    _sync(settings, monkeypatch, _s0(), _PinnedEmbedder())
    return settings.model_copy(update={"reembed_guard_max_tokens": tokens})


def test_the_guard_counts_the_whole_document_it_knows_must_be_re_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _workload(tmp_path / "probe", monkeypatch)
    limit = work.before_spend
    tight = _ceiling(tmp_path / "ceiling", monkeypatch, limit - 1)
    before = _state(tight.lovverk_repo_path)
    embedder = _PinnedEmbedder()

    with pytest.raises(MassReembedError, match=f"token workload {limit:,} > {limit - 1:,}"):
        _sync(tight, monkeypatch, _s1(), embedder)

    assert embedder.paid == []
    assert _state(tight.lovverk_repo_path) == before
    assert Settings.model_fields["reembed_guard_max_tokens"].default == 1_000_000


def test_a_re_pay_beyond_the_ceiling_is_refused_before_it_is_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _workload(tmp_path / "probe", monkeypatch)
    tight = _ceiling(tmp_path / "ceiling", monkeypatch, work.before_spend + work.re_paid - 1)
    repo = tight.lovverk_repo_path
    head, published = _state(repo)[0], _sidecars(repo)
    embedder = _PinnedEmbedder()

    with pytest.raises(MassReembedError, match="re-pa"):
        _sync(tight, monkeypatch, _s1(), embedder)

    assert not any(_body("lov-grow", 1) in text for text in embedder.paid)
    assert _tokens(embedder.paid) <= work.before_spend
    assert _state(repo)[0] == head
    assert _committed(repo, "lov-grow") == published["lov-grow"]


def test_a_re_pay_within_the_ceiling_completes_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = _workload(tmp_path / "probe", monkeypatch)
    exact = _ceiling(tmp_path / "ceiling", monkeypatch, work.before_spend + work.re_paid)
    embedder = _PinnedEmbedder()

    _sync(exact, monkeypatch, _s1(), embedder)

    assert _tokens(embedder.paid) == work.before_spend + work.re_paid
    assert _sidecars(exact.lovverk_repo_path) == _full_rebuild(tmp_path / "ref", monkeypatch)
