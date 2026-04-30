import struct
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from lovspor.embeddings.store import EMBEDDING_DIM, EmbeddingFile, read_embeddings, write_embeddings

HEADER_FMT = "<4sBBHIf"


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


def test_native_embedding_dimension_is_jina_base_no_dimension() -> None:
    assert EMBEDDING_DIM == 768


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


@pytest.mark.parametrize(
    ("sections", "match"),
    [
        ([("", _vector([1, 2, 3]))], "section_id length 0 out of range"),
        ([("x" * 256, _vector([1, 2, 3]))], "section_id length 256 out of range"),
        ([("1", np.array([1, 2, 3], dtype=np.int16))], "vector dtype must be int8"),
        ([("1", _vector([1, 2]))], "vector shape must be \\(3,\\)"),
    ],
)
def test_write_embeddings_rejects_invalid_sections(
    tmp_path: Path,
    sections: list[tuple[str, np.ndarray]],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        write_embeddings(tmp_path / "bad.bin", sections, scale=1.0, dim=3)


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"short", "file too short"),
        (_header(magic=b"NOPE") + b"\x011abc", "bad magic"),
        (_header(version=2) + b"\x011abc", "unsupported version 2"),
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

    with pytest.raises(ValueError, match=match):
        read_embeddings(path)


def test_embedding_file_is_immutable() -> None:
    result = EmbeddingFile(dim=3, scale=1.0, sections=[("1", _vector([1, 2, 3]))])

    with pytest.raises(FrozenInstanceError):
        result.dim = 4
