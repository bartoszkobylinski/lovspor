"""An approval that is not a JSON object is refused like any other bad approval."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk.commands import read_approval
from lovspor.crosswalk.errors import CrosswalkError


@pytest.mark.parametrize(
    "content", [b"", b'{"R1":', b"\xff", b"null", b"[]", b'"approved"'], ids=repr
)
def test_a_document_level_error_names_the_document(tmp_path: Path, content: bytes) -> None:
    approval = tmp_path / "approval.json"
    approval.write_bytes(content)
    with pytest.raises(CrosswalkError) as caught:
        read_approval(approval)
    assert str(caught.value) == "approval refused: invalid document"


def test_field_errors_still_name_their_fields(tmp_path: Path) -> None:
    approval = tmp_path / "approval.json"
    approval.write_bytes(b'{"R1": "approved", "R2": "approved"}')
    with pytest.raises(CrosswalkError) as caught:
        read_approval(approval)
    assert str(caught.value) == "approval refused: invalid approved_by, approved_on, input_sha256"


def test_release_command_refuses_a_non_object_approval_cleanly(tmp_path: Path) -> None:
    source, approval = tmp_path / "input.jsonl", tmp_path / "approval.json"
    source.write_bytes(b"")
    approval.write_bytes(b"[]")
    args = ["--input", str(source), "--approval", str(approval)]
    args += ["--cache", str(tmp_path / "cache"), "--output", str(tmp_path / "out.jsonl")]
    result = CliRunner().invoke(app, ["ops", "crosswalk", "release-candidate", *args])
    assert result.exit_code == 1
    assert "approval refused: invalid document" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == ["approval.json", "input.jsonl"]
