"""Changed-fragment embedding on the change path (#919), byte-exact (#936).

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
slug swap overwriting a sidecar) cannot change what is reused.

The output is the full re-embed's, byte for byte (#936). LSPE holds one int8
scale per file, ``float32(max|v| / 127)``, and int8 rows keep no float vector,
so a carried row can be reused only while the document's scale is provably
unchanged; it is never rounded a second time. Then the carried row *is*
``round(v / scale)``, and each paid row is quantized at that scale by the same
rule (:func:`~lovspor.embeddings.quantize.quantize_at_scale`). The scale is
provably unchanged when both hold:

1. every committed row reaching ±127 (the row holding the old maximum is one)
   is carried, so the new maximum is at least the old one
   (:func:`exact_prior`, checked by the guard before any spend; a document
   that fails it is paid and counted in full);
2. no paid row has a component whose scale would exceed the stored one.

Condition 2 is only known once the paid rows are back. A document that fails
it re-pays its carried inputs and is quantized from the floats of all its
inputs, as a rebuild is. That re-pay is spent from what the guard left under
its token ceiling (:class:`RepayHeadroom`) and refused before it is sent if it
does not fit, so the ceiling holds for the whole run.

What the proof assumes, because bytes alone cannot show it:

* **Provenance.** The committed sidecar is what the rebuild quantizer wrote
  over the floats of its own inputs. Int8 rows cannot prove their header
  scale was ``float32(max|v| / 127)``: a sidecar from a writer that kept a
  stale scale can still hold a ±127 row. So the set of sidecar writers is
  pinned (``tests/unit/test_delta_embed_provenance.py``): the rebuild
  quantizer, this module (byte-identical to it), and the LSPE cutover, which
  copies vectors and scale bit for bit. What bytes can refute is refused: no
  ±127 row, or a scale that is not finite and positive.
* **Vector purity.** A vector is a function of its text alone, independent of
  the request it was batched in: a re-pay batches differently from a rebuild.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import numpy as np

from lovspor.embeddings.cache import CachingEmbedder, quantized_document_vectors
from lovspor.embeddings.inputs import EmbeddingInput, build_embedding_inputs, hash_embedding_inputs
from lovspor.embeddings.model import EmbeddingModel, count_input_tokens
from lovspor.embeddings.provider import UNKNOWN_SPACE_ID, space_id_of
from lovspor.embeddings.quantize import quantize_at_scale, quantize_int8
from lovspor.embeddings.store import EmbeddingFile, read_embeddings
from lovspor.errors import EmbeddingResponseError, MassReembedError
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.document_io import embeddings_path
from lovspor.sync.reembed_guard import OVERRIDE_HINT

_INT8_MAX = 127.0


@dataclass
class RepayHeadroom:
    """Tokens the run may still spend re-paying carried inputs (#936).

    The guard grants what its ceiling leaves after the counted workload;
    ``None`` is the operator's override. It starts at zero, so a re-pay
    nothing granted is refused rather than waved through.
    """

    tokens: int | None = 0

    def spend(self, tokens: int) -> None:
        """Take ``tokens`` from the headroom, or refuse before anything is sent."""
        if self.tokens is None:
            return
        if tokens > self.tokens:
            raise MassReembedError(
                f"keeping a delta-embedded document byte-identical to a full re-embed needs "
                f"its carried inputs re-paid ({tokens:,} tokens), more than the {self.tokens:,} "
                "tokens left under the re-embed guard's token ceiling. That call was not sent "
                f"and nothing was committed. {OVERRIDE_HINT}",
            )
        self.tokens -= tokens


@dataclass(frozen=True)
class EmbeddingTarget:
    """The space and dimension the configured adapter writes, and the run's re-pay headroom."""

    space_id: str
    dim: int
    headroom: RepayHeadroom = field(default_factory=RepayHeadroom, compare=False)


@dataclass(frozen=True)
class PriorVectors:
    """Verified int8 rows of a document's committed sidecar, keyed by input text."""

    rows: Mapping[str, np.ndarray]
    scale: float
    headroom: RepayHeadroom = field(default_factory=RepayHeadroom, compare=False)


def embedding_target(embedder: EmbeddingModel | None) -> EmbeddingTarget | None:
    """The adapter's declared space, or ``None`` when it declares none (no reuse)."""
    space_id = space_id_of(embedder)
    if embedder is None or space_id is None or space_id == UNKNOWN_SPACE_ID:
        return None
    return EmbeddingTarget(space_id=space_id, dim=embedder.get_dimension())


def grant_headroom(target: EmbeddingTarget | None, tokens: int | None) -> None:
    """Hand the run's re-pays what the guard's ceiling leaves (``None``: overridden)."""
    if target is not None:
        target.headroom.tokens = tokens


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
    return PriorVectors(rows=rows, scale=sidecar.scale, headroom=target.headroom)


def prior_for(
    repo: Path,
    record: ManifestRecord | None,
    target: EmbeddingTarget | None,
    inputs: list[EmbeddingInput],
) -> PriorVectors | None:
    """The committed rows the change path may carry into ``inputs``, decided before spend."""
    return exact_prior(load_prior_vectors(repo, record, target), inputs)


def exact_prior(prior: PriorVectors | None, inputs: list[EmbeddingInput]) -> PriorVectors | None:
    """``prior`` when ``inputs`` carry every row that may hold its maximum (condition 1).

    A scale that is not finite and positive was never written by the rebuild
    quantizer, so nothing is carried from it.
    """
    if prior is None or not (math.isfinite(prior.scale) and prior.scale > 0):
        return None
    loudest = {text for text, row in prior.rows.items() if _reaches_max(row)}
    return prior if loudest and loudest <= {item.text for item in inputs} else None


def paid_inputs(inputs: list[EmbeddingInput], prior: PriorVectors | None) -> list[EmbeddingInput]:
    """The inputs the provider is sent: every input whose text has no carried row."""
    known = prior.rows if prior is not None else {}
    return [item for item in inputs if item.text not in known]


def paid_tokens(inputs: list[EmbeddingInput], prior: PriorVectors | None) -> int:
    """The guard's workload for one document: tokens of its paid inputs only."""
    return _tokens([item.text for item in paid_inputs(inputs, prior)])


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
        """Int8 rows and scale for ``inputs``, byte-identical to a full re-embed of them."""
        paid = [item.text for item in paid_inputs(inputs, self._prior)]
        fresh = dict(zip(paid, self._encode(paid), strict=True))
        if not self._keeps_scale(inputs, fresh):
            return self._repaid(inputs, fresh)
        scale = self._prior.scale
        quantized = {text: quantize_at_scale(row, scale) for text, row in fresh.items()}
        rows = [quantized.get(item.text, self._prior.rows.get(item.text)) for item in inputs]
        return np.vstack(cast(list[np.ndarray], rows)), scale

    def _keeps_scale(self, inputs: list[EmbeddingInput], fresh: dict[str, np.ndarray]) -> bool:
        """Conditions 1 and 2: a full re-embed would derive the stored scale again."""
        if exact_prior(self._prior, inputs) is None:
            return False
        if not fresh:
            return True
        loudest = float(np.abs(np.vstack(list(fresh.values()))).max())
        return bool(np.float32(loudest / _INT8_MAX) <= np.float32(self._prior.scale))

    def _repaid(
        self, inputs: list[EmbeddingInput], fresh: dict[str, np.ndarray]
    ) -> tuple[np.ndarray, float]:
        """The whole document from float vectors: carried inputs are paid again, within headroom."""
        carried = [item.text for item in inputs if item.text not in fresh]
        self._prior.headroom.spend(_tokens(carried))
        vectors = {**fresh, **dict(zip(carried, self._encode(carried), strict=True))}
        return quantize_int8(np.vstack([vectors[item.text] for item in inputs]))

    def _encode(self, texts: list[str]) -> np.ndarray:
        """One paid call for the texts; none at all when there are none."""
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

    With the durable cache on, a document entry, which only a full re-embed
    writes, still costs nothing. The change path reads only this runner's
    local copy (no remote fetch, upload, adoption or conflict check) and never
    stores an entry: a rerun after a crash pays its delta again, and a cached
    rebuild is never served rows a delta assembled (#936).
    """
    if not isinstance(embedder, ReusingEmbedder):
        return quantized_document_vectors(embedder, inputs, input_hash)
    cache = embedder.inner if isinstance(embedder.inner, CachingEmbedder) else None
    stored = None if cache is None else cache.cached_document(inputs, input_hash)
    if stored is not None:
        return stored.vectors, cast(float, stored.scale)
    return embedder.quantized_rows(inputs)


def _reaches_max(row: np.ndarray) -> bool:
    return int(np.abs(row.astype(np.int16)).max(initial=0)) == int(_INT8_MAX)


def _tokens(texts: list[str]) -> int:
    return sum(count_input_tokens(text) for text in texts)


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
    try:
        inputs = build_embedding_inputs(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
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
