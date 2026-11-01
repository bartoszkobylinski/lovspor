"""Chapter determinism and all-or-nothing staging contracts of ADR-0018 PR2."""

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from unittest.mock import Mock

import pytest

from lovspor.forarbeider.chapter_models import result_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation
from tests.unit.test_forarbeider_chapter import URL, page, result, section


@pytest.mark.parametrize(("seed", "encoding"), [("1", "ascii"), ("98765", "latin-1")])
def test_chapter_bytes_are_stable_across_python_sessions(
    tmp_path: Path, seed: str, encoding: str
) -> None:
    """Hash randomization and process I/O encoding cannot alter UTF-8 staging bytes."""
    body = section("<p>Høring: © æøå §&nbsp;3, diagnose, test@example.org.</p>")
    source = tmp_path / "page.html"
    source.write_bytes(page(body))
    script = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import result_text

held = build_chapter_result(
    Path(sys.argv[1]).read_bytes(), sys.argv[2], datetime(2100, 4, 1, 12, tzinfo=UTC)
)
sys.stdout.buffer.write(result_text(held).encode("utf-8"))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script, str(source), URL],
        env={**os.environ, "PYTHONHASHSEED": seed, "PYTHONIOENCODING": encoding},
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == result_text(result(body)).encode("utf-8")


def test_partial_staging_write_is_refused_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write must leave neither a partial observation nor staging residue."""
    real_fdopen = os.fdopen

    @contextmanager
    def failing_stream(descriptor: int, mode: str) -> Iterator[BinaryIO]:
        with real_fdopen(descriptor, mode) as stream:

            def partial_write(payload: bytes) -> None:
                stream.write(payload[:3])
                stream.flush()
                raise OSError("simulated disk full")

            proxy = Mock(wraps=stream)
            proxy.write.side_effect = partial_write
            yield proxy

    monkeypatch.setattr(os, "fdopen", failing_stream)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        write_observation(tmp_path / "chapter.json", "complete observation")
    assert caught.value.reason is RefusalReason.OUTPUT_WRITE_FAILED
    assert list(tmp_path.iterdir()) == []
