"""Binary read/write for per-document embedding files.

File layout — one file per document, named
``<dataset>/embeddings/<slug>.bin`` inside the lovverk corpus repo.
The complete byte-level format is documented in ``docs/embeddings.md``;
this module is the single canonical implementation of both sides.

Header (16 bytes, little-endian):

| Offset | Size | Field         | Notes                                  |
|-------:|-----:|---------------|----------------------------------------|
|      0 |    4 | magic         | ``b"LSPE"`` (LovSpor Embeddings)       |
|      4 |    1 | version       | ``1``                                  |
|      5 |    1 | reserved      | ``0`` (future flags)                   |
|      6 |    2 | section count | uint16, must equal records that follow |
|      8 |    4 | dim           | uint32, embedding dimension            |
|     12 |    4 | scale         | float32, dequantization scale          |

Each section record then:

- ``1`` byte: section_id length in UTF-8 bytes (uint8, 1-255)
- ``N`` bytes: section_id (UTF-8)
- ``dim`` bytes: int8 quantized vector

The format is intentionally simple so a third-party tool can verify
the corpus end-to-end (per the ``lovverk`` README's ``How updates
work`` section). No compression — the embeddings themselves are the
compression. Per-doc sharding lets ``git`` diff per-section changes
without rewriting a monolithic blob.
"""

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

_MAGIC = b"LSPE"
_VERSION = 1
_HEADER_FMT = "<4sBBHIf"
_HEADER_SIZE = 16
_MAX_SECTIONS = 65535
_MAX_SECTION_ID_LEN = 255

EMBEDDING_DIM = 3072
"""Native dimensionality of OpenAI ``text-embedding-3-large``.

Bumped from 768 (the placeholder for the abandoned jina-v2-base-no
candidate) to 3072 in Sprint 9 PR-B after the empirical benchmark
(``benchmarks/embedding_comparison/results-2026-04-30.md``) showed
``text-embedding-3-large`` beating Norwegian-tuned alternatives by
+24% Recall@5.

Each file's header records ``dim`` so old and new files can coexist
during a migration. A future model change would bump this constant
again and fire a Sprint-9-style backfill against the existing
records.
"""


@dataclass(frozen=True)
class EmbeddingFile:
    """Parsed contents of one ``<slug>.bin`` file."""

    dim: int
    scale: float
    sections: list[tuple[str, np.ndarray]]


def write_embeddings(
    path: Path,
    sections: list[tuple[str, np.ndarray]],
    scale: float,
    dim: int = EMBEDDING_DIM,
) -> None:
    """Write per-section embeddings to a binary file.

    ``sections`` is a list of ``(section_id, int8_vector)`` pairs.
    Each ``int8_vector`` must be a 1-D ``int8`` numpy array of
    length ``dim``. ``scale`` is the dequantization factor produced
    by :func:`quantize_int8`.

    Parent directory is created if missing. Same input -> byte-
    identical file (deterministic) so the ``lovverk`` git history
    stays clean.
    """
    if len(sections) > _MAX_SECTIONS:
        raise ValueError(
            f"too many sections ({len(sections)}); max is {_MAX_SECTIONS}",
        )
    path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[bytes] = [
        struct.pack(_HEADER_FMT, _MAGIC, _VERSION, 0, len(sections), dim, scale),
    ]
    for section_id, vector in sections:
        encoded = section_id.encode("utf-8")
        if len(encoded) == 0 or len(encoded) > _MAX_SECTION_ID_LEN:
            raise ValueError(
                f"section_id length {len(encoded)} out of range "
                f"(1..{_MAX_SECTION_ID_LEN}): {section_id!r}",
            )
        if vector.dtype != np.int8:
            raise ValueError(
                f"vector dtype must be int8 for section {section_id!r}, got {vector.dtype}",
            )
        if vector.shape != (dim,):
            raise ValueError(
                f"vector shape must be ({dim},) for section {section_id!r}, got {vector.shape}",
            )
        parts.append(struct.pack("<B", len(encoded)))
        parts.append(encoded)
        parts.append(vector.tobytes())
    path.write_bytes(b"".join(parts))


def read_embeddings(path: Path) -> EmbeddingFile:
    """Load and validate an embeddings file.

    Raises ``ValueError`` for: file too short, wrong magic, unknown
    version, truncated body, malformed UTF-8 in a section_id.
    """
    data = path.read_bytes()
    if len(data) < _HEADER_SIZE:
        raise ValueError(
            f"{path}: file too short ({len(data)} bytes, need at least {_HEADER_SIZE})",
        )
    magic, version, _reserved, section_count, dim, scale = struct.unpack(
        _HEADER_FMT,
        data[:_HEADER_SIZE],
    )
    if magic != _MAGIC:
        raise ValueError(
            f"{path}: bad magic {magic!r} (expected {_MAGIC!r})",
        )
    if version != _VERSION:
        raise ValueError(
            f"{path}: unsupported version {version} (this engine reads {_VERSION})",
        )

    cursor = _HEADER_SIZE
    sections: list[tuple[str, np.ndarray]] = []
    for _ in range(section_count):
        if cursor >= len(data):
            raise ValueError(f"{path}: truncated body at section index {len(sections)}")
        id_len = data[cursor]
        cursor += 1
        if cursor + id_len > len(data):
            raise ValueError(f"{path}: truncated section_id at index {len(sections)}")
        try:
            section_id = data[cursor : cursor + id_len].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"{path}: invalid UTF-8 in section_id at index {len(sections)}: {exc}",
            ) from exc
        cursor += id_len
        if cursor + dim > len(data):
            raise ValueError(f"{path}: truncated vector at section_id {section_id!r}")
        vector = np.frombuffer(data[cursor : cursor + dim], dtype=np.int8).copy()
        cursor += dim
        sections.append((section_id, vector))
    if cursor != len(data):
        raise ValueError(
            f"{path}: trailing data ({len(data) - cursor} bytes) after {section_count} sections",
        )
    return EmbeddingFile(dim=dim, scale=scale, sections=sections)
