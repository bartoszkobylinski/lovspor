"""Changed-fragment embedding on the change path (#919).

On 2026-11-14 the nightly sync was refused by the mass-re-embed guard: 57
documents, 2,797,261 input tokens, while only about 5% of those inputs had
changed text. The change path now pays only for inputs whose text the verified
committed sidecar does not already embed, the guard counts exactly those, and
a document whose committed rows cannot be proven falls back to a full,
fully counted re-embed.
"""

import hashlib
import subprocess
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import lovspor.sync.orchestrator as orchestrator_module
from lovspor.embeddings.cache import CachingEmbedder, with_embedding_cache
from lovspor.embeddings.inputs import build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.model import count_input_tokens
from lovspor.embeddings.quantize import quantize_int8
from lovspor.embeddings.store import read_embeddings, write_embeddings
from lovspor.errors import EmbeddingResponseError, MassReembedError, UnsupportedSidecarVersionError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord, read_manifest, write_manifest
from lovspor.sync.delta_embed import (
    EmbeddingTarget,
    PriorVectors,
    RepayHeadroom,
    ReusingEmbedder,
    document_vectors,
    embedding_target,
    exact_prior,
    load_prior_vectors,
    paid_inputs,
    paid_tokens,
)
from lovspor.sync.orchestrator import _embeddings_path, _UpstreamDoc, _write_one, run_sync
from tests.unit.test_embedding_space_identity import _FakeEmbedder
from tests.unit.test_sync_change_reembed_guard import _OBSERVED, _state
from tests.unit.test_sync_orchestrator import _disable_migrations, _git, _settings

_SECTION_ONE = "Loven gjelder for hele riket."
"""Its vector holds the largest component of every fixture law (0.83), so it sets the scale."""
_SECTION_TWO = "Endres ved lov 19 juni 2026 nr. 48 (i kraft fra den tid Kongen bestemmer)."
_SECTION_TWO_NEW = "Endres ved lov 19 juni 2026 nr. 48 (i kraft 1 jan 2027 iflg. res. 13 nov 2026)."


class _TextEmbedder(_FakeEmbedder):
    """A deterministic adapter whose vector depends on the text, recording every paid text."""

    def __init__(self) -> None:
        super().__init__()
        self.paid: list[str] = []

    def encode(self, texts: list[str]) -> np.ndarray:
        self.paid.extend(texts)
        return np.vstack([_vector(text, self.get_dimension()) for text in texts])


class _BrokenEmbedder(_TextEmbedder):
    """An adapter whose answer has one row too few."""

    def encode(self, texts: list[str]) -> np.ndarray:
        return super().encode(texts)[1:]


def _vector(text: str, dim: int) -> np.ndarray:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = np.frombuffer(digest[: dim * 4], dtype="<u4").astype(np.float64) / 2**32 - 0.5
    return (raw / np.linalg.norm(raw)).astype(np.float32)


def _article(number: int, body: str) -> str:
    return (
        f'<article class="legalArticle"><h2 class="legalArticleHeader">'
        f'<span class="legalArticleValue">§ {number}</span>. '
        f'<span class="legalArticleTitle">Paragraf {number}</span></h2>'
        f'<article class="legalP" id="ledd-{number}">{body}</article></article>'
    )


def _law(bodies: tuple[str, ...], slug: str = "lov-a") -> _UpstreamDoc:
    articles = "".join(_article(n, body) for n, body in enumerate(bodies, start=1))
    xml = (
        '<!DOCTYPE html><html lang="nb"><head><title>Lov A</title></head>'
        '<body><header class="documentHeader"><dl>'
        '<dt class="title">Tittel</dt><dd class="title">Lov A</dd>'
        '<dt class="refid">RefID</dt><dd class="refid">lov/a</dd>'
        f'</dl></header><main id="dokument"><h1>Lov A</h1>{articles}</main></body></html>'
    ).encode()
    return _UpstreamDoc(
        doc_id="lov-a",
        source_dataset="gjeldende-lover",
        xml_bytes=xml,
        xml_hash=hashlib.sha256(xml).hexdigest(),
        slug=slug,
        title="Lov A",
        eu_basis=(),
    )


def _inputs(doc: _UpstreamDoc) -> list:
    rendered = orchestrator_module.render_full_document(
        doc.xml_bytes, orchestrator_module._frontmatter_context(doc, _OBSERVED)
    )
    return build_embedding_inputs(rendered)


def _published(tmp_path: Path, bodies: tuple[str, ...] = (_SECTION_ONE, _SECTION_TWO)) -> Settings:
    """A committed corpus of one law, embedded by the text-dependent adapter."""
    settings = _settings(tmp_path)
    seeded = orchestrator_module._with_retrieved_at(_law(bodies), _OBSERVED)
    record, _paths = _write_one(settings, seeded, _OBSERVED, _TextEmbedder())
    repo = settings.lovverk_repo_path
    write_manifest(
        Manifest(generated_at=_OBSERVED, documents={"lov-a": record}), repo / "manifest.json"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "publish")
    return settings


def _record(settings: Settings) -> ManifestRecord:
    return read_manifest(settings.lovverk_repo_path / "manifest.json").documents["lov-a"]


def _target() -> EmbeddingTarget:
    target = embedding_target(_TextEmbedder())
    assert target is not None
    return target


def _sync(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, doc: _UpstreamDoc, embedder: _TextEmbedder
) -> None:
    _disable_migrations(monkeypatch)
    monkeypatch.setattr(orchestrator_module, "_load_embedder", lambda _settings: embedder)
    monkeypatch.setattr(orchestrator_module, "_collect_upstream", lambda *_a: ({"lov-a": doc}, ()))
    run_sync(settings)


def _sidecar_rows(settings: Settings, slug: str = "lov-a") -> dict[str, np.ndarray]:
    sidecar = read_embeddings(_embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", slug))
    return {
        section_id: row.astype(np.float32) * sidecar.scale for section_id, row in sidecar.sections
    }


# --- what the committed sidecar vouches for -------------------------------


def test_verified_committed_rows_are_keyed_by_their_input_text(tmp_path: Path) -> None:
    settings = _published(tmp_path)
    repo = settings.lovverk_repo_path

    prior = load_prior_vectors(repo, _record(settings), _target())

    assert prior is not None
    sidecar = read_embeddings(_embeddings_path(repo, "gjeldende-lover", "lov-a"))
    inputs = _inputs(_law((_SECTION_ONE, _SECTION_TWO)))
    assert set(prior.rows) == {item.text for item in inputs}
    for item, (_, row) in zip(inputs, sidecar.sections, strict=True):
        assert np.array_equal(prior.rows[item.text], row)
    assert prior.scale == sidecar.scale


def _edit_markdown(settings: Settings) -> None:
    path = settings.lovverk_repo_path / _record(settings).markdown_path
    path.write_text(
        path.read_text(encoding="utf-8").replace("hele riket", "riket"), encoding="utf-8"
    )


def _rewrite_sidecar(settings: Settings, **change: object) -> None:
    path = _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a")
    sidecar = read_embeddings(path)
    sections = change.get("sections", sidecar.sections)
    esi = change.get("esi", sidecar.embedding_space_id)
    write_embeddings(path, sections, sidecar.scale, sidecar.dim, embedding_space_id=esi)  # type: ignore[arg-type]


def _swap_section_ids(settings: Settings) -> None:
    path = _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a")
    rows = read_embeddings(path).sections
    ids = [section_id for section_id, _ in rows][::-1]
    _rewrite_sidecar(settings, sections=[(ids[n], row) for n, (_, row) in enumerate(rows)])


def _unlink_sidecar(settings: Settings) -> None:
    _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a").unlink()


def _corrupt_sidecar(settings: Settings) -> None:
    _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a").write_bytes(b"LSPE")


_REFUSALS = {
    "markdown no longer derives the embedded inputs": _edit_markdown,
    "sidecar missing": _unlink_sidecar,
    "sidecar corrupt": _corrupt_sidecar,
    "version-1 sidecar cannot prove its space": lambda s: _rewrite_sidecar(s, esi=None),
    "sidecar header names another space": lambda s: _rewrite_sidecar(s, esi="0" * 32),
    "rows do not line up with the inputs": _swap_section_ids,
}


@pytest.mark.parametrize("damage", list(_REFUSALS.values()), ids=list(_REFUSALS))
def test_unprovable_committed_rows_are_never_reused(tmp_path: Path, damage: object) -> None:
    settings = _published(tmp_path)
    damage(settings)  # type: ignore[operator]

    assert load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target()) is None


_RECORD_REFUSALS = {
    "sidecar built from older content": {"embedding_hash": "f" * 64},
    "record in another space": {"embedding_space_id": "0" * 32},
    "record without input identity": {"embedding_input_hash": None},
    "tombstone": {"status": "removed", "removed_reason": "repealed"},
}


@pytest.mark.parametrize("update", list(_RECORD_REFUSALS.values()), ids=list(_RECORD_REFUSALS))
def test_a_record_that_does_not_vouch_for_its_sidecar_is_never_reused(
    tmp_path: Path, update: dict[str, object]
) -> None:
    settings = _published(tmp_path)
    record = _record(settings).model_copy(update=update)

    assert load_prior_vectors(settings.lovverk_repo_path, record, _target()) is None


def test_no_target_or_no_record_means_no_reuse(tmp_path: Path) -> None:
    settings = _published(tmp_path)
    repo = settings.lovverk_repo_path

    assert load_prior_vectors(repo, _record(settings), None) is None
    assert load_prior_vectors(repo, None, _target()) is None
    other_dim = EmbeddingTarget(space_id=_target().space_id, dim=_target().dim + 1)
    assert load_prior_vectors(repo, _record(settings), other_dim) is None
    assert embedding_target(None) is None


# --- what is paid for -----------------------------------------------------


def test_only_inputs_with_new_text_are_paid_and_counted() -> None:
    old, new = (
        _inputs(_law((_SECTION_ONE, _SECTION_TWO))),
        _inputs(_law((_SECTION_ONE, _SECTION_TWO_NEW))),
    )
    prior = PriorVectors(rows={item.text: np.zeros(4, dtype=np.int8) for item in old}, scale=1.0)

    paid = paid_inputs(new, prior)

    assert [item.section_id for item in paid] == [new[1].section_id]
    assert paid_tokens(new, prior) == count_input_tokens(new[1].text)
    assert paid_inputs(new, None) == new
    assert paid_tokens(new, None) == sum(count_input_tokens(item.text) for item in new)


def test_carried_rows_come_back_bit_identical_when_the_scale_holds() -> None:
    inputs = _inputs(_law((_SECTION_ONE, _SECTION_TWO_NEW)))
    carried = np.array([127, -3, 5, 0], dtype=np.int8)
    prior = PriorVectors(rows={inputs[0].text: carried}, scale=0.01)
    embedder = _TextEmbedder()

    rows, scale = ReusingEmbedder(embedder, prior).quantized_rows(inputs)

    assert embedder.paid == [inputs[1].text]
    assert np.array_equal(rows[0], carried)
    assert scale == pytest.approx(0.01)


def test_dropping_the_row_that_held_the_maximum_withdraws_every_carried_row() -> None:
    """Without the old maximum a rebuild's scale is unknowable from int8 rows (#936)."""
    old = _inputs(_law((_SECTION_ONE, _SECTION_TWO)))
    loud, quiet = np.array([127, -5, 7, 0], dtype=np.int8), np.array([3, -5, 7, 0], dtype=np.int8)
    prior = PriorVectors(rows={old[0].text: quiet, old[1].text: loud}, scale=0.004)

    assert exact_prior(prior, _inputs(_law((_SECTION_ONE,)))) is None
    assert exact_prior(prior, old) is prior
    assert exact_prior(PriorVectors(rows={old[0].text: quiet}, scale=0.004), old[:1]) is None
    assert exact_prior(None, old) is None


def _louder_than_the_prior() -> tuple[list, PriorVectors]:
    inputs = _inputs(_law((_SECTION_ONE, _SECTION_TWO_NEW)))
    carried = np.array([127, -64, 5, 0], dtype=np.int8)
    return inputs, PriorVectors(rows={inputs[0].text: carried}, scale=0.001)


def test_a_paid_row_beyond_the_prior_scale_re_pays_the_document_from_floats() -> None:
    """Re-rounding the carried int8 rows at a new scale would round them twice (#936)."""
    inputs, prior = _louder_than_the_prior()
    embedder = _TextEmbedder()
    granted = replace(prior, headroom=RepayHeadroom(None))

    rows, scale = ReusingEmbedder(embedder, granted).quantized_rows(inputs)

    assert embedder.paid == [inputs[1].text, inputs[0].text]
    expected, expected_scale = quantize_int8(np.vstack([_vector(i.text, 4) for i in inputs]))
    assert np.array_equal(rows, expected)
    assert scale == expected_scale


def test_a_re_pay_without_headroom_is_refused_before_it_is_sent() -> None:
    inputs, prior = _louder_than_the_prior()
    embedder = _TextEmbedder()
    tight = replace(prior, headroom=RepayHeadroom(count_input_tokens(inputs[0].text) - 1))

    with pytest.raises(MassReembedError, match="re-paid"):
        ReusingEmbedder(embedder, tight).quantized_rows(inputs)
    with pytest.raises(MassReembedError, match="0 tokens left"):
        ReusingEmbedder(_TextEmbedder(), prior).quantized_rows(inputs)

    assert embedder.paid == [inputs[1].text]
    headroom = RepayHeadroom(count_input_tokens(inputs[0].text))
    ReusingEmbedder(_TextEmbedder(), replace(prior, headroom=headroom)).quantized_rows(inputs)
    assert headroom.tokens == 0


def test_a_fully_carried_document_makes_no_provider_call() -> None:
    inputs = _inputs(_law((_SECTION_ONE,)))
    prior = PriorVectors(rows={inputs[0].text: np.array([1, 2, 3, 127], dtype=np.int8)}, scale=0.5)
    embedder = _TextEmbedder()

    rows, _scale = ReusingEmbedder(embedder, prior).quantized_rows(inputs)

    assert embedder.paid == []
    assert np.array_equal(rows[0], prior.rows[inputs[0].text])


def test_a_malformed_provider_answer_writes_nothing() -> None:
    inputs = _inputs(_law((_SECTION_ONE, _SECTION_TWO_NEW)))
    prior = PriorVectors(rows={inputs[0].text: np.ones(4, dtype=np.int8)}, scale=1.0)

    with pytest.raises(EmbeddingResponseError, match="1 inputs"):
        ReusingEmbedder(_BrokenEmbedder(), prior).quantized_rows(inputs)


# --- the change path end to end -------------------------------------------


def test_a_one_note_amendment_pays_only_for_the_changed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    path = _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a")
    before = read_embeddings(path)
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    new_inputs = _inputs(changed)
    embedder = _TextEmbedder()

    _sync(settings, monkeypatch, changed, embedder)

    assert embedder.paid == [new_inputs[1].text]
    after = _sidecar_rows(settings)
    assert list(after) == [item.section_id for item in new_inputs]
    carried = read_embeddings(path)
    assert carried.scale == before.scale
    assert np.array_equal(carried.sections[0][1], before.sections[0][1])
    paid = after[new_inputs[1].section_id]
    assert float(paid @ _vector(new_inputs[1].text, 4) / np.linalg.norm(paid)) > 0.999
    record = _record(settings)
    assert record.embedding_input_hash == hash_embedding_inputs(new_inputs)
    assert record.embedding_hash == record.xml_hash == changed.xml_hash
    assert _state(settings.lovverk_repo_path)[1] == ""


def test_a_removed_section_drops_its_row_and_costs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    shortened = _law((_SECTION_ONE,))
    embedder = _TextEmbedder()

    _sync(settings, monkeypatch, shortened, embedder)

    assert embedder.paid == []
    assert list(_sidecar_rows(settings)) == [item.section_id for item in _inputs(shortened)]


def test_a_renamed_law_carries_every_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _published(tmp_path)
    before = read_embeddings(
        _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a")
    )
    embedder = _TextEmbedder()

    _sync(
        settings, monkeypatch, replace(_law((_SECTION_ONE, _SECTION_TWO)), slug="lov-b"), embedder
    )

    assert embedder.paid == []
    after = read_embeddings(
        _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-b")
    )
    assert [section_id for section_id, _ in after.sections] == [s for s, _ in before.sections]
    assert all(
        np.array_equal(new, old)
        for (_, new), (_, old) in zip(after.sections, before.sections, strict=True)
    )
    assert after.scale == pytest.approx(before.scale)
    assert not _embeddings_path(settings.lovverk_repo_path, "gjeldende-lover", "lov-a").exists()


def test_an_unprovable_sidecar_is_paid_and_counted_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    _corrupt_sidecar(settings)
    _git(settings.lovverk_repo_path, "commit", "-q", "-am", "corrupt sidecar")
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    full = sum(count_input_tokens(item.text) for item in _inputs(changed))
    settings = settings.model_copy(update={"reembed_guard_max_tokens": full - 1})
    embedder = _TextEmbedder()

    with pytest.raises(MassReembedError, match=f"token workload {full:,} > {full - 1:,}"):
        _sync(settings, monkeypatch, changed, embedder)
    assert embedder.paid == []


@pytest.mark.parametrize("budget_offset", [-1, 0])
def test_the_guard_measures_the_paid_delta_and_still_fails_closed_above_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget_offset: int
) -> None:
    settings = _published(tmp_path)
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    delta = count_input_tokens(_inputs(changed)[1].text)
    settings = settings.model_copy(update={"reembed_guard_max_tokens": delta + budget_offset})
    before = _state(settings.lovverk_repo_path)
    embedder = _TextEmbedder()

    if budget_offset == -1:
        with pytest.raises(MassReembedError, match=f"token workload {delta:,} > {delta - 1:,}"):
            _sync(settings, monkeypatch, changed, embedder)
        assert embedder.paid == []
        assert _state(settings.lovverk_repo_path) == before
    else:
        _sync(settings, monkeypatch, changed, embedder)
        assert sum(count_input_tokens(text) for text in embedder.paid) == delta


def test_a_crashed_run_publishes_nothing_and_its_retry_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _published(tmp_path)
    repo = settings.lovverk_repo_path
    head = _state(repo)[0].strip()
    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))

    with pytest.raises(EmbeddingResponseError):
        _sync(settings, monkeypatch, changed, _BrokenEmbedder())
    assert _state(repo)[0].strip() == head
    sidecar = _embeddings_path(repo, "gjeldende-lover", "lov-a")
    results = []
    for _attempt in range(2):
        subprocess.run(["git", "reset", "-q", "--hard", head], cwd=repo, check=True)
        subprocess.run(["git", "clean", "-qfd"], cwd=repo, check=True)
        embedder = _TextEmbedder()
        _sync(settings, monkeypatch, changed, embedder)
        results.append((sidecar.read_bytes(), embedder.paid))
    assert results[0] == results[1]


def test_the_change_path_never_stores_a_cache_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a full re-embed writes a document entry, so none can hold assembled rows (#936)."""
    settings = _published(tmp_path)
    monkeypatch.setenv("LOVSPOR_EMBEDDING_CACHE_DIR", str(tmp_path / "cache"))
    prior = load_prior_vectors(settings.lovverk_repo_path, _record(settings), _target())
    assert prior is not None
    inputs = _inputs(_law((_SECTION_ONE, _SECTION_TWO_NEW)))
    digest = hash_embedding_inputs(inputs)
    provider = _TextEmbedder()
    cached = with_embedding_cache(provider, settings)
    assert isinstance(cached, CachingEmbedder)

    rows, scale = document_vectors(ReusingEmbedder(cached, prior), inputs, digest)

    assert provider.paid == [inputs[1].text]
    assert list((tmp_path / "cache").rglob("*.lsec")) == []
    full = cached.document_vectors(inputs, digest)
    assert np.array_equal(full.vectors, rows)
    restarted_provider = _BrokenEmbedder()
    restarted = with_embedding_cache(restarted_provider, settings)
    assert isinstance(restarted, CachingEmbedder)
    served, served_scale = document_vectors(ReusingEmbedder(restarted, prior), inputs, digest)
    assert restarted_provider.paid == []
    assert restarted.progress.documents_cached == 1
    assert np.array_equal(served, full.vectors)
    assert served_scale == full.scale
    assert np.float32(served_scale) == np.float32(scale)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_delta_response_preserves_the_existing_sidecar(
    tmp_path: Path, value: float
) -> None:
    """EmbeddingResponseError promises no half-valid sidecar is published."""
    settings = _published(tmp_path)
    repo = settings.lovverk_repo_path
    prior = load_prior_vectors(repo, _record(settings), _target())
    assert prior is not None
    path = _embeddings_path(repo, "gjeldende-lover", "lov-a")
    before = path.read_bytes()

    class NonfiniteEmbedder(_TextEmbedder):
        def encode(self, texts: list[str]) -> np.ndarray:
            rows = super().encode(texts)
            rows[0, 0] = value
            return rows

    changed = _law((_SECTION_ONE, _SECTION_TWO_NEW))
    rendered = orchestrator_module.render_full_document(
        changed.xml_bytes, orchestrator_module._frontmatter_context(changed, _OBSERVED)
    )
    with pytest.raises(EmbeddingResponseError, match="non-finite"):
        orchestrator_module._write_embeddings_for_doc(
            repo, "gjeldende-lover", "lov-a", rendered, ReusingEmbedder(NonfiniteEmbedder(), prior)
        )
    assert path.read_bytes() == before


def test_future_sidecar_version_stops_change_path_before_spending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delta loader explicitly propagates unsupported versions before any spend."""
    settings = _published(tmp_path)
    repo = settings.lovverk_repo_path
    path = _embeddings_path(repo, "gjeldende-lover", "lov-a")
    payload = bytearray(path.read_bytes())
    payload[4] = 3
    path.write_bytes(payload)
    _git(repo, "commit", "-q", "-am", "future sidecar version")
    before = _state(repo)
    provider = _TextEmbedder()

    with pytest.raises(UnsupportedSidecarVersionError):
        _sync(settings, monkeypatch, _law((_SECTION_ONE, _SECTION_TWO_NEW)), provider)

    assert provider.paid == []
    assert _state(repo) == before
    assert path.read_bytes() == payload
