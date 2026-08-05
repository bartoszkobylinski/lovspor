import struct
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.store import EMBEDDING_DIM, EmbeddingFile, read_embeddings, write_embeddings
from lovspor.errors import UnsupportedSidecarVersionError

ESI = "738c919fa57385d94c558d93c4b0e588"

HEADER_FMT = "<4sBBHIf"
MAX_SECTIONS = 65535


def _vector(values: list[int]) -> np.ndarray:
    return np.array(values, dtype=np.int8)


def _header(*, magic: bytes = b"LSPE", version: int = 1, count: int = 1, dim: int = 3) -> bytes:
    return struct.pack(HEADER_FMT, magic, version, 0, count, dim, 0.5)


def test_write_and_read_embeddings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "embeddings" / "arbeidsmiljoloven.bin"
    sections = [
        ("15-3", _vector([1, -2, 3])),
        ("15-4", _vector([4, 5, -6])),
    ]

    write_embeddings(path, sections, scale=0.25, dim=3)
    result = read_embeddings(path)

    assert result.dim == 3
    assert result.scale == pytest.approx(0.25)
    assert [section_id for section_id, _vector_int8 in result.sections] == ["15-3", "15-4"]
    np.testing.assert_array_equal(result.sections[0][1], sections[0][1])
    np.testing.assert_array_equal(result.sections[1][1], sections[1][1])


def test_native_embedding_dimension_matches_text_embedding_3_large() -> None:
    """Sprint 9 PR-B pivoted from nb-sbert-v2-large (1024) to OpenAI
    text-embedding-3-large (3072) based on the empirical benchmark
    in benchmarks/embedding_comparison/results-2026-04-30.md."""
    assert EMBEDDING_DIM == 3072


def test_write_embeddings_is_byte_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first.bin"
    second = tmp_path / "nested" / "second.bin"
    sections = [("1", _vector([1, 2, 3]))]

    write_embeddings(first, sections, scale=0.125, dim=3)
    write_embeddings(second, sections, scale=0.125, dim=3)

    assert first.read_bytes() == second.read_bytes()


def test_write_embeddings_accepts_max_length_section_id(tmp_path: Path) -> None:
    path = tmp_path / "max-id.bin"
    section_id = "x" * 255

    write_embeddings(path, [(section_id, _vector([1]))], scale=1.0, dim=1)

    assert read_embeddings(path).sections[0][0] == section_id


def test_write_embeddings_accepts_max_section_count(tmp_path: Path) -> None:
    path = tmp_path / "max-sections.bin"
    sections = [(str(i), _vector([0])) for i in range(MAX_SECTIONS)]

    write_embeddings(path, sections, scale=1.0, dim=1)

    result = read_embeddings(path)
    assert len(result.sections) == MAX_SECTIONS
    assert result.sections[0][0] == "0"
    assert result.sections[-1][0] == str(MAX_SECTIONS - 1)


def test_write_embeddings_rejects_more_than_max_sections(tmp_path: Path) -> None:
    sections = [(str(i), _vector([0])) for i in range(MAX_SECTIONS + 1)]

    with pytest.raises(ValueError) as exc_info:
        write_embeddings(tmp_path / "too-many.bin", sections, scale=1.0, dim=1)
    assert str(exc_info.value) == "too many sections (65536); max is 65535"


def test_write_embeddings_creates_nested_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "c.bin"

    write_embeddings(path, [("1", _vector([1]))], scale=1.0, dim=1)

    assert path.exists()


def test_write_embeddings_leaves_no_temp_file(tmp_path: Path) -> None:
    """The write goes via a temp file + atomic rename; on success nothing
    but the final .bin should remain in the directory."""
    path = tmp_path / "atomic.bin"

    write_embeddings(path, [("1", _vector([1]))], scale=1.0, dim=1)

    assert [p.name for p in tmp_path.iterdir()] == ["atomic.bin"]


def test_write_embeddings_header_reserved_byte_is_zero(tmp_path: Path) -> None:
    path = tmp_path / "header.bin"

    write_embeddings(path, [], scale=0.75, dim=3)

    magic, version, reserved, count, dim, scale = struct.unpack(
        HEADER_FMT,
        path.read_bytes()[:16],
    )
    assert (magic, version, reserved, count, dim) == (b"LSPE", 1, 0, 0, 3)
    assert scale == pytest.approx(0.75)


@pytest.mark.parametrize(
    ("sections", "match"),
    [
        (
            [("", _vector([1, 2, 3]))],
            r"section_id length 0 out of range \(1..255\): ''",
        ),
        (
            [("x" * 256, _vector([1, 2, 3]))],
            r"section_id length 256 out of range \(1..255\)",
        ),
        (
            [("1", np.array([1, 2, 3], dtype=np.int16))],
            "vector dtype must be int8 for section '1', got int16",
        ),
        (
            [("1", _vector([1, 2]))],
            r"vector shape must be \(3,\) for section '1', got \(2,\)",
        ),
    ],
)
def test_write_embeddings_rejects_invalid_sections(
    tmp_path: Path,
    sections: list[tuple[str, np.ndarray]],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        write_embeddings(tmp_path / "bad.bin", sections, scale=1.0, dim=3)


def test_write_embeddings_invalid_section_messages_are_exact(tmp_path: Path) -> None:
    path = tmp_path / "bad.bin"

    with pytest.raises(ValueError) as exc_info:
        write_embeddings(path, [("1", np.array([1, 2, 3], dtype=np.int16))], scale=1.0, dim=3)
    assert str(exc_info.value) == "vector dtype must be int8 for section '1', got int16"

    with pytest.raises(ValueError) as exc_info:
        write_embeddings(path, [("1", _vector([1, 2]))], scale=1.0, dim=3)
    assert str(exc_info.value) == "vector shape must be (3,) for section '1', got (2,)"


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"short", "file too short"),
        (_header(magic=b"NOPE") + b"\x011abc", "bad magic"),
        (_header(version=2) + b"\x011abc", "version-2 header truncated"),
        (_header(count=1, dim=3), "truncated body at section index 0"),
        (_header(count=1, dim=3) + b"\x05ab", "truncated section_id at index 0"),
        (_header(count=1, dim=3) + b"\x01\xffabc", "invalid UTF-8 in section_id"),
        (_header(count=1, dim=3) + b"\x011ab", "truncated vector at section_id '1'"),
        (_header(count=0, dim=3) + b"x", "trailing data"),
    ],
)
def test_read_embeddings_rejects_malformed_files(
    tmp_path: Path,
    content: bytes,
    match: str,
) -> None:
    path = tmp_path / "bad.bin"
    path.write_bytes(content)

    with pytest.raises(ValueError, match=match) as exc_info:
        read_embeddings(path)
    assert str(path) in str(exc_info.value)


def test_read_embeddings_malformed_messages_are_exact(tmp_path: Path) -> None:
    path = tmp_path / "bad.bin"

    path.write_bytes(b"short")
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: file too short (5 bytes, need at least 16)"

    path.write_bytes(_header(magic=b"NOPE") + b"\x011abc")
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: bad magic b'NOPE' (expected b'LSPE')"

    path.write_bytes(_header(version=2) + b"\x011abc")
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == (f"{path}: version-2 header truncated (21 bytes, need 32)")

    path.write_bytes(_header(count=1, dim=3))
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: truncated body at section index 0"

    path.write_bytes(_header(count=1, dim=3) + b"\x05ab")
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: truncated section_id at index 0"

    path.write_bytes(_header(count=1, dim=3) + b"\x011ab")
    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: truncated vector at section_id '1'"


def test_read_embeddings_allows_exact_section_id_boundary(tmp_path: Path) -> None:
    path = tmp_path / "exact-id.bin"
    section_id = "abc"
    path.write_bytes(_header(count=1, dim=0) + bytes([len(section_id)]) + section_id.encode())

    result = read_embeddings(path)

    assert result.sections[0][0] == section_id
    np.testing.assert_array_equal(result.sections[0][1], np.array([], dtype=np.int8))


def test_read_embeddings_reports_exact_trailing_byte_count(tmp_path: Path) -> None:
    path = tmp_path / "trailing.bin"
    path.write_bytes(_header(count=0, dim=3) + b"xyz")

    with pytest.raises(ValueError) as exc_info:
        read_embeddings(path)
    assert str(exc_info.value) == f"{path}: trailing data (3 bytes) after 0 sections"


def test_embedding_file_is_immutable() -> None:
    result = EmbeddingFile(dim=3, scale=1.0, sections=[("1", _vector([1, 2, 3]))])

    with pytest.raises(FrozenInstanceError):
        result.dim = 4


def test_v2_round_trip_carries_the_esi(tmp_path: Path) -> None:
    path = tmp_path / "v2.bin"
    sections = [("15-3", _vector([1, -2, 3])), ("15-4", _vector([4, 5, -6]))]

    write_embeddings(path, sections, scale=0.25, dim=3, embedding_space_id=ESI)
    result = read_embeddings(path)

    assert result.version == 2
    assert result.embedding_space_id == ESI
    assert result.dim == 3
    assert result.scale == pytest.approx(0.25)
    np.testing.assert_array_equal(result.sections[0][1], sections[0][1])
    np.testing.assert_array_equal(result.sections[1][1], sections[1][1])


def test_v1_read_carries_no_identity(tmp_path: Path) -> None:
    """A v1 file cannot prove its space: the parsed identity must be None,
    never inferred from configuration or dimension (ADR-0005 §2)."""
    path = tmp_path / "v1.bin"
    write_embeddings(path, [("1", _vector([1, 2, 3]))], scale=1.0, dim=3)

    result = read_embeddings(path)

    assert result.version == 1
    assert result.embedding_space_id is None


def test_v2_bytes_are_the_v1_bytes_with_the_digest_spliced_in(tmp_path: Path) -> None:
    """Golden structural pin: v2 = v1 header with version byte 2 plus the raw
    16-byte digest, then the identical body. Nothing else may differ."""
    sections = [("15-3", _vector([1, -2, 3]))]
    v1_path, v2_path = tmp_path / "v1.bin", tmp_path / "v2.bin"
    write_embeddings(v1_path, sections, scale=0.25, dim=3)
    write_embeddings(v2_path, sections, scale=0.25, dim=3, embedding_space_id=ESI)

    v1, v2 = v1_path.read_bytes(), v2_path.read_bytes()

    assert v2[:4] == v1[:4] == b"LSPE"
    assert (v1[4], v2[4]) == (1, 2)
    assert v2[5:16] == v1[5:16], "shared header fields must be identical"
    assert v2[16:32] == bytes.fromhex(ESI)
    assert v2[32:] == v1[16:], "body must be byte-identical across versions"
    assert len(v2) == len(v1) + 16


def test_v2_write_is_byte_deterministic(tmp_path: Path) -> None:
    sections = [("2-1", _vector([7, 8, 9]))]
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    write_embeddings(a, sections, scale=0.5, dim=3, embedding_space_id=ESI)
    write_embeddings(b, sections, scale=0.5, dim=3, embedding_space_id=ESI)

    assert a.read_bytes() == b.read_bytes()


def test_a_future_version_raises_unsupported_not_valueerror(tmp_path: Path) -> None:
    """ADR-0005 §3: an unsupported version must be distinguishable from a
    corrupt file, or the search path's corrupt-skip silently shrinks the
    corpus. The error type IS the distinction — it must never be a
    ValueError."""
    path = tmp_path / "v3.bin"
    path.write_bytes(_header(version=3) + b"\x011abc")

    with pytest.raises(UnsupportedSidecarVersionError) as exc_info:
        read_embeddings(path)

    assert not isinstance(exc_info.value, ValueError)
    assert "version 3" in str(exc_info.value)
    assert "update lovspor" in str(exc_info.value)


def test_header_only_v2_round_trip(tmp_path: Path) -> None:
    """A sectionless doc's sidecar still carries the space identity in v2."""
    path = tmp_path / "empty.bin"
    write_embeddings(path, [], scale=1.0, dim=3, embedding_space_id=ESI)

    result = read_embeddings(path)

    assert result.version == 2
    assert result.embedding_space_id == ESI
    assert result.sections == []
    assert len(path.read_bytes()) == 32


@pytest.mark.parametrize(
    ("esi", "match"),
    [
        ("abc", "must be 32 hex chars"),
        ("g" * 32, "not valid hex"),
        ("738c919fa57385d94c558d93c4b0e5", "must be 32 hex chars"),
    ],
)
def test_write_rejects_a_malformed_esi(tmp_path: Path, esi: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        write_embeddings(
            tmp_path / "bad.bin",
            [("1", _vector([1, 2, 3]))],
            scale=1.0,
            dim=3,
            embedding_space_id=esi,
        )
