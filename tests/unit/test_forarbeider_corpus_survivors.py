"""Observable serialization, refusal and filesystem contracts for survivor review."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lovspor.forarbeider.corpus_render import _identity_fields, render_document
from lovspor.forarbeider.corpus_transaction import JOURNAL, Journal, Operation, _stage
from lovspor.forarbeider.corpus_tree import (
    MARKER,
    create_sandbox,
    read_dataset,
    require_sandbox,
)
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from tests.unit.forarbeider_corpus_support import beside_fifo, metadata_of
from tests.unit.forarbeider_promotion_support import planned, simulate
from tests.unit.forarbeider_release_support import Desk, at_a_terminal  # noqa: F401
from tests.unit.test_forarbeider_corpus_render import _rendering


def test_identity_fields_identify_an_unsafe_title(tmp_path: Path) -> None:
    metadata = metadata_of(Desk(tmp_path)).model_copy(update={"title": "Title\u2028injection"})
    with pytest.raises(ForarbeiderRefusedError) as caught:
        _identity_fields(metadata)
    assert caught.value.reason is RefusalReason.RENDER_UNSAFE
    assert caught.value.detail == "title"


def test_new_text_files_use_utf8_in_an_ascii_process(tmp_path: Path) -> None:
    code = """
import locale
import sys
from pathlib import Path
from lovspor.forarbeider.corpus_tree import _write_new
locale.setlocale(locale.LC_CTYPE, 'C')
assert locale.getencoding().lower() in {'ascii', 'us-ascii', 'ansi_x3.4-1968'}
_write_new(Path(sys.argv[1]), '\\u00e5\\u00a7\\n')
"""
    target = tmp_path / "text"
    result = subprocess.run(
        [sys.executable, "-c", code, str(target)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert target.read_bytes() == "å§\n".encode()


def test_staged_journal_round_trips_operations(tmp_path: Path) -> None:
    box = create_sandbox(tmp_path / "box", ())
    journal = Journal(
        schema_version=1,
        record_type="forarbeider_corpus_journal",
        target_manifest_sha256="a" * 64,
        operations=(
            Operation(path="forarbeider/INDEX.md", sha256="b" * 64),
            Operation(path="forarbeider/manifest.json", sha256="a" * 64),
        ),
    )
    _stage(box, {}, journal)
    raw = (box / JOURNAL).read_bytes()
    assert Journal.model_validate_json(raw) == journal
    assert (
        raw
        == (json.dumps(journal.model_dump(mode="json"), sort_keys=True, indent=2) + "\n").encode()
    )


@pytest.mark.parametrize("name", [MARKER, ".git/config"])
@pytest.mark.parametrize("kind", ["missing", "fifo", "symlink"])
def test_unreadable_sandbox_identity_is_refused(tmp_path: Path, name: str, kind: str) -> None:
    box = create_sandbox(tmp_path / "box", ())
    path = box / name
    original = path.read_bytes()
    path.unlink()
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "symlink":
        outside = tmp_path / "outside"
        outside.write_bytes(original)
        path.symlink_to(outside)
    blocked, refusals = beside_fifo(lambda: require_sandbox(box, ()), path)
    assert not blocked, "the sandbox check blocked on a FIFO"
    assert [(error.reason, error.detail) for error in refusals] == [
        (RefusalReason.CORPUS_NOT_SANDBOX, "sandbox")
    ]


@pytest.mark.parametrize("name", ["lover", "forarbeider"])
def test_symlinked_subtrees_are_refused(tmp_path: Path, name: str) -> None:
    box = create_sandbox(tmp_path / "box", ())
    outside = tmp_path / "outside"
    if name == "lover":
        (box / name).rename(outside)
        (box / name).mkdir()
    else:
        outside.mkdir()
        (box / name).mkdir()
    (box / name / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        (read_dataset(box) if name == "forarbeider" else require_sandbox(box, ()))
    assert caught.value.reason is RefusalReason.UNSAFE_INPUT_PATH


def test_rendered_unicode_and_chapter_spacing_are_stable(tmp_path: Path) -> None:
    rendering = _rendering(Desk(tmp_path))
    first = render_document(rendering)
    assert render_document(rendering) == first
    assert 'rights_basis: "åndsverkloven § 14"' in first
    assert "\n## Tilråding\n" in first
    assert "- første punkt\n- andre punkt\n  - a) underpunkt" in first


@pytest.mark.usefixtures("at_a_terminal")
def test_simulation_rederives_a_valid_release_plan(tmp_path: Path) -> None:
    desk = Desk(tmp_path / "desk")
    box = create_sandbox(tmp_path / "box", ())
    sha = planned(desk, box)
    result = simulate(desk, sha, box)
    assert result.exit_code == 0, result.output
    assert len(read_dataset(box).manifest.documents) == 1


def test_staged_unicode_paths_use_utf8_in_an_ascii_process(tmp_path: Path) -> None:
    code = """
import locale
import sys
from pathlib import Path
from lovspor.forarbeider.corpus_transaction import JOURNAL, Journal, Operation, _stage
locale.setlocale(locale.LC_CTYPE, 'C')
assert locale.getencoding().lower() in {'ascii', 'us-ascii', 'ansi_x3.4-1968'}
box = Path(sys.argv[1])
(box / '.git').mkdir()
journal = Journal(
    schema_version=1, record_type='forarbeider_corpus_journal',
    target_manifest_sha256='a' * 64,
    operations=(
        Operation(path='forarbeider/2099-2100/prop-l/9\\u0661.md', sha256='b' * 64),
        Operation(path='forarbeider/manifest.json', sha256='a' * 64),
    ),
)
_stage(box, {}, journal)
assert Journal.model_validate_json((box / JOURNAL).read_bytes()) == journal
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0", "LC_ALL": "C"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    raw = (tmp_path / JOURNAL).read_bytes()
    assert "9\u0661.md".encode() in raw
