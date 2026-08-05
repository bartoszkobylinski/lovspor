"""Binary read/write for per-document embedding files.

File layout — one file per document, named
``<dataset>/embeddings/<slug>.bin`` inside the lovverk corpus repo.
The complete byte-level format is documented in ``docs/embeddings.md``;
this module is the single canonical implementation of both sides.

Common header prefix (16 bytes, little-endian, both versions):

| Offset | Size | Field         | Notes                                  |
|-------:|-----:|---------------|----------------------------------------|
|      0 |    4 | magic         | ``b"LSPE"`` (LovSpor Embeddings)       |
|      4 |    1 | version       | ``1`` or ``2``                         |
|      5 |    1 | reserved      | ``0`` (future flags)                   |
|      6 |    2 | section count | uint16, must equal records that follow |
|      8 |    4 | dim           | uint32, embedding dimension            |
|     12 |    4 | scale         | float32, dequantization scale          |

Version 2 (ADR-0005 Stage 2) appends exactly one field to the header:

| Offset | Size | Field      | Notes                                       |
|-------:|-----:|------------|---------------------------------------------|
|     16 |   16 | ESI digest | raw bytes of the Embedding Space Identity   |

The digest is the 128-bit ``embedding_space_id`` (the manifest stores it
as 32 lowercase hex characters; the header stores the same value as raw
bytes). It makes the sidecar carry its own space identity: a version-2
file read without any manifest still proves which space its vectors
belong to, and a consumer that reached it *through* the manifest can
detect a substituted file by comparing the two. Version 1 carries no
identity and a detached v1 read remains Unknown/legacy (ADR-0005 §2).

Each section record then (both versions):

- ``1`` byte: section_id length in UTF-8 bytes (uint8, 1-255)
- ``N`` bytes: section_id (UTF-8)
- ``dim`` bytes: int8 quantized vector

The format is intentionally simple so a third-party tool can verify
the corpus end-to-end (per the ``lovverk`` README's ``How updates
work`` section). No compression — the embeddings themselves are the
compression. Per-doc sharding lets ``git`` diff per-section changes
without rewriting a monolithic blob.

Version discipline (ADR-0005 §3, binding): the writer emits version 1
until the one coordinated corpus-wide cutover has landed; a version-2
file must never be published opportunistically. The reader reads both.
A version this reader does not know raises
:class:`~lovspor.errors.UnsupportedSidecarVersionError` — deliberately
NOT a ``ValueError`` — so the search path's corrupt-file skip cannot
silently shrink the corpus when it meets a future format (the
silent-partial-recall failure ADR-0005 §3 names).
"""

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from lovspor.errors import UnsupportedSidecarVersionError

_MAGIC = b"LSPE"
_VERSION_1 = 1
_VERSION_2 = 2
_SUPPORTED_VERSIONS = (_VERSION_1, _VERSION_2)
_HEADER_FMT = "<4sBBHIf"
_HEADER_SIZE = 16
_ESI_DIGEST_SIZE = 16
_ESI_HEX_LEN = 32
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
    """Parsed contents of one ``<slug>.bin`` file.

    ``version`` is the format version the file was stored in.
    ``embedding_space_id`` is the sidecar-carried ESI (32 lowercase hex
    chars) for version-2 files, ``None`` for version 1 — a v1 file
    cannot prove its space and must never be assumed into one.
    """

    dim: int
    scale: float
    sections: list[tuple[str, np.ndarray]]
    version: int = 1
    embedding_space_id: str | None = None


def _esi_digest_bytes(embedding_space_id: str) -> bytes:
    """Raw 16-byte form of a manifest-style hex ESI, strictly validated.

    A malformed identity must fail here, at write time, rather than
    produce a header digest that silently differs from what the
    manifest records.
    """
    if len(embedding_space_id) != _ESI_HEX_LEN:
        raise ValueError(
            f"embedding_space_id must be {_ESI_HEX_LEN} hex chars, "
            f"got {len(embedding_space_id)}: {embedding_space_id!r}",
        )
    try:
        return bytes.fromhex(embedding_space_id)
    except ValueError as exc:
        raise ValueError(
            f"embedding_space_id is not valid hex: {embedding_space_id!r}",
        ) from exc


def write_embeddings(
    path: Path,
    sections: list[tuple[str, np.ndarray]],
    scale: float,
    dim: int = EMBEDDING_DIM,
    *,
    embedding_space_id: str | None = None,
) -> None:
    """Write per-section embeddings to a binary file.

    ``sections`` is a list of ``(section_id, int8_vector)`` pairs.
    Each ``int8_vector`` must be a 1-D ``int8`` numpy array of
    length ``dim``. ``scale`` is the dequantization factor produced
    by :func:`quantize_int8`.

    ``embedding_space_id`` selects the format version: ``None`` writes
    version 1 (byte-identical to what this function has always
    produced); a 32-hex-char ESI writes version 2 with that identity
    embedded in the header. The version is implied by the identity's
    presence because that is the only difference between the formats —
    there is no way to write a v2 file without an identity or a v1
    file with one. Callers obey ADR-0005 §3: version 2 is passed only
    by the coordinated cutover migration and, after the cutover has
    landed, by the regular writer.

    Parent directory is created if missing. Same input -> byte-
    identical file (deterministic) so the ``lovverk`` git history
    stays clean.
    """
    if len(sections) > _MAX_SECTIONS:
        raise ValueError(
            f"too many sections ({len(sections)}); max is {_MAX_SECTIONS}",
        )
    path.parent.mkdir(parents=True, exist_ok=True)

    if embedding_space_id is None:
        header = struct.pack(_HEADER_FMT, _MAGIC, _VERSION_1, 0, len(sections), dim, scale)
    else:
        header = struct.pack(
            _HEADER_FMT, _MAGIC, _VERSION_2, 0, len(sections), dim, scale
        ) + _esi_digest_bytes(embedding_space_id)
    parts: list[bytes] = [header]
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
    # Write to a sibling temp file then atomically rename, so a crash
    # mid-write can never leave a truncated .bin that later reads as
    # corrupt (or, worse, that a staleness check treats as present).
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(b"".join(parts))
    tmp.replace(path)


def read_embeddings(path: Path) -> EmbeddingFile:
    """Load and validate an embeddings file (format version 1 or 2).

    Raises ``ValueError`` for a corrupt file: too short, wrong magic,
    truncated body or header, malformed UTF-8 in a section_id.

    Raises :class:`UnsupportedSidecarVersionError` — which is NOT a
    ``ValueError`` — for a well-formed file in a version this reader
    does not implement. The distinction is binding (ADR-0005 §3):
    corrupt files are a per-file repairable condition and may be
    skipped by callers; an unsupported version means the *reader* is
    behind the corpus, and treating that as per-file corruption would
    silently shrink the searched corpus — the exact failure the
    version bump discipline exists to prevent.
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
    if version not in _SUPPORTED_VERSIONS:
        raise UnsupportedSidecarVersionError(
            f"{path}: sidecar format version {version} is newer than this "
            f"engine (reads {_SUPPORTED_VERSIONS}); update lovspor instead "
            f"of treating the file as corrupt",
        )

    cursor = _HEADER_SIZE
    embedding_space_id: str | None = None
    if version == _VERSION_2:
        if len(data) < _HEADER_SIZE + _ESI_DIGEST_SIZE:
            raise ValueError(
                f"{path}: version-2 header truncated "
                f"({len(data)} bytes, need {_HEADER_SIZE + _ESI_DIGEST_SIZE})",
            )
        embedding_space_id = data[cursor : cursor + _ESI_DIGEST_SIZE].hex()
        cursor += _ESI_DIGEST_SIZE

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
    return EmbeddingFile(
        dim=dim,
        scale=scale,
        sections=sections,
        version=version,
        embedding_space_id=embedding_space_id,
    )
