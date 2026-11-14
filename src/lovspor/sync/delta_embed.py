"""Changed-fragment embedding on the change path (#919).

A changed law usually differs from its committed version in a handful of
embedding inputs. On 2026-11-14, 48 laws changed one amendment note, and only
about 5% of the input tokens differed, yet the change path paid for every
input of every changed document: 2.8M tokens against the 1M guard. A vector
is a function of its input text alone, so the vector of an input whose text
the committed sidecar already embedded, in the same space, is carried
instead of paid for.

Reuse is allowed only after the committed rows are proven to belong to those
texts (:func:`load_prior_vectors`):

* the prior record's sidecar was written for its current content
  (``embedding_hash == xml_hash``), in the target space (record ESI);
* the committed Markdown re-derives exactly the inputs that sidecar was built
  from (the ADR-0006 ``embedding_input_hash``), so row *i* is the vector of
  input *i*'s text;
* the sidecar parses, is LSPE version 2 with the target ESI in its header,
  has the target dimension, and has one row per input with the same section
  ids in the same order.

Any failed check means no reuse for that document: it is paid for and counted
in full, exactly as before. The guard takes the snapshot from the clean
committed tree before the first write, so a later write in the same run (a
slug swap overwriting a sidecar) cannot change what is reused, and the
guard's count is exactly what the writer sends.

The LSPE format holds one int8 scale per file. While every paid row fits the
prior scale, the document keeps it, so carried int8 rows come back
bit-identical and repeated amendments never accumulate rounding. Only a paid
row with a larger component than the prior scale can hold rescales the whole
document once, as a full re-embed would: at most one quantization step per
carried component (cosine above 0.999, see ``embeddings.quantize``).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast

import numpy as np

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.model import EmbeddingModel, count_input_tokens
from lovspor.embeddings.provider import UNKNOWN_SPACE_ID, space_id_of
from lovspor.embeddings.quantize import dequantize_int8, quantize_int8
from lovspor.embeddings.store import EmbeddingFile, read_embeddings
from lovspor.errors import EmbeddingResponseError
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.document_io import embeddings_path

_INT8_MAX = 127.0
_FIT_TOLERANCE = 1e-6


@dataclass(frozen=True)
class EmbeddingTarget:
    """The space and dimension the configured adapter writes."""

    space_id: str
    dim: int


@dataclass(frozen=True)
class PriorVectors:
    """Verified int8 rows of a document's committed sidecar, keyed by input text."""

    rows: Mapping[str, np.ndarray]
    scale: float


def embedding_target(embedder: EmbeddingModel | None) -> EmbeddingTarget | None:
    """The adapter's declared space, or ``None`` when it declares none (no reuse)."""
    space_id = space_id_of(embedder)
    if embedder is None or space_id is None or space_id == UNKNOWN_SPACE_ID:
        return None
    return EmbeddingTarget(space_id=space_id, dim=embedder.get_dimension())


def load_prior_vectors(
    repo: Path, record: ManifestRecord | None, target: EmbeddingTarget | None
) -> PriorVectors | None:
    """The committed rows ``record`` vouches for, or ``None`` if any check fails."""
    if target is None or record is None or not _record_vouches(record, target):
        return None
    inputs = _committed_inputs(repo, record)
    sidecar = _committed_sidecar(repo, record, target) if inputs else None
    if inputs is None or sidecar is None:
        return None
    if [section_id for section_id, _ in sidecar.sections] != [i.section_id for i in inputs]:
        return None
    rows: dict[str, np.ndarray] = {}
    for item, (_, row) in zip(inputs, sidecar.sections, strict=True):
        rows.setdefault(item.text, row)
    return PriorVectors(rows=rows, scale=sidecar.scale)


def paid_inputs(inputs: list[EmbeddingInput], prior: PriorVectors | None) -> list[EmbeddingInput]:
    """The inputs the provider is sent: every input whose text has no carried row."""
    known = prior.rows if prior is not None else {}
    return [item for item in inputs if item.text not in known]


def paid_tokens(inputs: list[EmbeddingInput], prior: PriorVectors | None) -> int:
    """The guard's workload for one document: tokens of its paid inputs only."""
    return sum(count_input_tokens(item.text) for item in paid_inputs(inputs, prior))


class ReusingEmbedder:
    """The configured adapter, plus one document's verified committed rows."""

    def __init__(self, inner: EmbeddingModel, prior: PriorVectors) -> None:
        self._inner = inner
        self._prior = prior

    @property
    def inner(self) -> EmbeddingModel:
        return self._inner

    def encode(self, texts: list[str]) -> np.ndarray:
        return self._inner.encode(texts)

    def get_dimension(self) -> int:
        return self._inner.get_dimension()

    @property
    def space_id(self) -> str:
        return space_id_of(self._inner) or UNKNOWN_SPACE_ID

    @property
    def descriptor(self) -> str | None:
        descriptor = getattr(self._inner, "descriptor", None)
        return descriptor if isinstance(descriptor, str) else None

    def quantized_rows(self, inputs: list[EmbeddingInput]) -> tuple[np.ndarray, float]:
        """Int8 rows and scale for ``inputs``: paid rows for new text, carried rows otherwise."""
        paid = [item.text for item in paid_inputs(inputs, self._prior)]
        fresh = dict(zip(paid, self._encode(paid), strict=True))
        rows = [
            fresh[item.text]
            if item.text in fresh
            else dequantize_int8(self._prior.rows[item.text], self._prior.scale)
            for item in inputs
        ]
        return _quantize_keeping(np.vstack(rows), self._prior.scale)

    def _encode(self, texts: list[str]) -> np.ndarray:
        """One paid call for the new texts; none at all when every text is carried."""
        if not texts:
            return np.empty((0, self.get_dimension()), dtype=np.float32)
        vectors = np.asarray(self._inner.encode(texts), dtype=np.float32)
        if vectors.shape != (len(texts), self.get_dimension()) or not np.isfinite(vectors).all():
            raise EmbeddingResponseError(
                f"provider returned shape {vectors.shape} for {len(texts)} inputs at dimension "
                f"{self.get_dimension()}, or non-finite values; no sidecar was written",
            )
        return vectors


def reusing(embedder: EmbeddingModel, prior: PriorVectors | None) -> EmbeddingModel:
    """``embedder`` carrying ``prior``'s rows, or ``embedder`` itself when there are none."""
    return embedder if prior is None else ReusingEmbedder(embedder, prior)


def document_vectors(
    embedder: EmbeddingModel, inputs: list[EmbeddingInput], input_hash: str
) -> tuple[np.ndarray, float]:
    """Int8 rows and scale for a non-empty document, paying only for what is not carried.

    With the durable cache on, a cached document entry still costs nothing, and
    a miss stores the carried-plus-paid rows under the document's input hash, so
    a rerun after a crash does not pay the delta again.
    """
    if not isinstance(embedder, ReusingEmbedder):
        return quantized_document_vectors(embedder, inputs, input_hash)
    if isinstance(embedder.inner, CachingEmbedder):
        stored = embedder.inner.document_vectors(
            inputs, input_hash, partial(embedder.quantized_rows, inputs)
        )
        return stored.vectors, cast(float, stored.scale)
    return embedder.quantized_rows(inputs)


def _quantize_keeping(matrix: np.ndarray, scale: float) -> tuple[np.ndarray, float]:
    """Int8 rows at the prior ``scale`` while every component fits it; else a fresh scale.

    The tolerance absorbs the float32 product of a carried ``127 * scale``, which
    rounds back to 127; anything beyond it would clip, so it rescales instead.
    """
    fits = float(np.abs(matrix).max()) <= _INT8_MAX * scale * (1 + _FIT_TOLERANCE)
    if not (np.isfinite(scale) and scale > 0 and fits):
        return quantize_int8(matrix)
    quantized = np.clip(np.round(matrix / scale), -_INT8_MAX, _INT8_MAX).astype(np.int8)
    return quantized, scale


def _record_vouches(record: ManifestRecord, target: EmbeddingTarget) -> bool:
    """The record says its sidecar embeds its current content in the target space."""
    return (
        record.status == "current"
        and record.slug is not None
        and record.embedding_hash == record.xml_hash
        and record.embedding_space_id == target.space_id
        and record.embedding_input_hash is not None
    )


def _committed_inputs(repo: Path, record: ManifestRecord) -> list[EmbeddingInput] | None:
    """The inputs the committed Markdown derives, if they are the ones the sidecar embeds."""
    path = repo / record.markdown_path
    if not path.is_file():
        return None
    inputs = build_embedding_inputs(path.read_text(encoding="utf-8"))
    return inputs if hash_embedding_inputs(inputs) == record.embedding_input_hash else None


def _committed_sidecar(
    repo: Path, record: ManifestRecord, target: EmbeddingTarget
) -> EmbeddingFile | None:
    """The committed sidecar, if it parses and its header proves the target space.

    A corrupt or missing file is not reusable and is paid for in full. An
    unsupported format version propagates: the engine is behind the corpus,
    and the run stops before any spend rather than overwrite what it cannot read.
    """
    path = embeddings_path(repo, record.source_dataset, str(record.slug))
    try:
        sidecar = read_embeddings(path)
    except (ValueError, OSError):
        return None
    if sidecar.embedding_space_id != target.space_id or sidecar.dim != target.dim:
        return None
    return sidecar
