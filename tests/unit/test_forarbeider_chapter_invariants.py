"""Chapter determinism and all-or-nothing staging contracts of ADR-0018 PR2."""

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from unittest.mock import Mock

import pytest

from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import HoldReason, TextBlock, result_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.staging import write_observation
from tests.unit.test_forarbeider_chapter import URL, WHEN, page, result, section


@pytest.mark.parametrize("newline", [b"\r\n", b"\r"])
def test_chapter_line_endings_change_source_hash_but_not_text(newline: bytes) -> None:
    """operations.md: raw bytes are hashed separately from normalized HTML text."""
    payload = page(section("<p>Første\n linje\t og\n neste §&nbsp;3.</p>"))
    changed = payload.replace(b"\n", newline)
    original = build_chapter_result(payload, URL, WHEN)
    held = build_chapter_result(changed, URL, WHEN)

    assert held.blocks is not None
    assert held.blocks[-1].text == "Første linje og neste §\u00a03."
    assert held.blocks == original.blocks
    assert held.text_sha256 == original.text_sha256
    assert held.source_sha256 == hashlib.sha256(changed).hexdigest()
    assert held.source_sha256 != original.source_sha256


def test_unrecognized_body_keeps_all_review_candidates_without_leaking_text() -> None:
    """The allowlist withholds text, but must not stop the chapter's reason scan."""
    held = result(
        section(
            "<aside>Høring: © diagnose kontakt@example.org</aside>"
            "<p>Høring: © diagnose kontakt@example.org</p>"
        )
    )

    assert held.hold_reasons == (
        HoldReason.REVIEW_REQUIRED,
        HoldReason.DAMAGED_DOM,
        HoldReason.THIRD_PARTY_WORK,
        HoldReason.HEARING_PARTICIPANTS,
        HoldReason.PERSONAL_DATA,
        HoldReason.SENSITIVE_DATA,
    )
    assert held.blocks is None
    assert held.text_sha256 is None
    serialized = result_text(held)
    for source_text in ("Høring", "©", "diagnose", "kontakt@example.org"):
        assert source_text not in serialized


@pytest.mark.parametrize("spelling", ["&nbsp;", "&#160;", "&#xA0;"])
def test_entity_spelling_changes_only_the_source_hash(spelling: str) -> None:
    """The text hash covers decoded published characters, the source hash raw bytes."""
    literal = result(section("<p>§\u00a03 &amp; æ</p>"))
    escaped = result(section(f"<p>§{spelling}3 &#38; &#230;</p>"))

    assert escaped.blocks is not None
    assert escaped.blocks[-1] == TextBlock(kind="paragraph", text="§\u00a03 & æ")
    assert escaped.blocks == literal.blocks
    assert escaped.text_sha256 == literal.text_sha256
    assert escaped.source_sha256 != literal.source_sha256


def test_failed_staging_close_preserves_existing_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Buffered I/O can fail at close: never publish it or touch an earlier observation."""
    target = tmp_path / "chapter.json"
    assert write_observation(target, "earlier observation") is True
    before = target.stat()
    real_fdopen = os.fdopen

    @contextmanager
    def failing_close(descriptor: int, mode: str) -> Iterator[BinaryIO]:
        with real_fdopen(descriptor, mode) as stream:
            yield stream
        raise OSError("simulated buffered close failure")

    publish = Mock()
    monkeypatch.setattr(os, "fdopen", failing_close)
    monkeypatch.setattr(os, "link", publish)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        write_observation(target, "later observation")

    assert caught.value.reason is RefusalReason.OUTPUT_WRITE_FAILED
    publish.assert_not_called()
    assert target.read_bytes() == b"earlier observation"
    after = target.stat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (
        before.st_ino,
        before.st_mode,
        before.st_mtime_ns,
    )
    assert list(tmp_path.iterdir()) == [target]


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
