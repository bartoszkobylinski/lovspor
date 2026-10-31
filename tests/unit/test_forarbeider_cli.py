"""``lovspor ops forarbeider-metadata``: the operator's one route to a record (#738)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider.models import read_metadata
from tests.unit.cli_output import said

FIXTURE = Path(__file__).parents[1] / "fixtures" / "forarbeider" / "prop-l.html"
URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1"
WHEN = "2100-04-01T12:00:00Z"


def _argv(html: Path, output: Path, url: str = URL, when: str = WHEN) -> list[str]:
    return [
        "ops",
        "forarbeider-metadata",
        "--html",
        str(html),
        "--source-url",
        url,
        "--retrieved-at",
        when,
        "--output",
        str(output),
    ]


def test_operator_creates_the_record_and_it_reads_back(tmp_path: Path) -> None:
    output = tmp_path / "staging" / "regjeringen-9000001.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output))
    assert result.exit_code == 0
    assert result.output == f"wrote regjeringen-9000001: 5 chapters to {output.resolve()}\n"
    record = read_metadata(output)
    assert record.source_page_url == URL
    assert record.retrieved_at.isoformat() == "2100-04-01T12:00:00+00:00"
    assert json.loads(output.read_text(encoding="utf-8"))["source_license"] is None


def test_module_entry_point_runs_it_in_a_real_process(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    code = "from lovspor.cli import app; app()"
    completed = subprocess.run(
        [sys.executable, "-c", code, *_argv(FIXTURE, output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert output.exists()


def test_same_inputs_give_identical_bytes(tmp_path: Path) -> None:
    first, second = tmp_path / "1.json", tmp_path / "2.json"
    assert CliRunner().invoke(app, _argv(FIXTURE, first)).exit_code == 0
    assert CliRunner().invoke(app, _argv(FIXTURE, second)).exit_code == 0
    assert first.read_bytes() == second.read_bytes()


def test_refusal_exits_one_says_why_and_writes_nothing(tmp_path: Path) -> None:
    output = tmp_path / "record.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output, url="https://example.org/x"))
    assert result.exit_code == 1
    assert said("refused: host_not_allowed: example.org", result.output)
    assert list(tmp_path.iterdir()) == []


def test_output_inside_a_checkout_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    output = tmp_path / "record.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output))
    assert result.exit_code == 1
    assert said("unsafe_output_path", result.output)
    assert not output.exists()


def test_engine_repository_is_refused() -> None:
    engine = Path(__file__).parents[2]
    output = engine / "data" / "cache" / "refused-forarbeider.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output))
    assert result.exit_code == 1
    assert not output.exists()


def test_corpus_default_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    output = tmp_path / "lovverk" / "forarbeider" / "a.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output))
    assert result.exit_code == 1
    assert said("unsafe_output_path", result.output)
    assert not output.exists()


@pytest.mark.parametrize("when", ["2100-04-01", "2100-04-01T12:00:00", "yesterday", ""])
def test_retrieval_time_must_be_explicit_and_aware(tmp_path: Path, when: str) -> None:
    output = tmp_path / "record.json"
    result = CliRunner().invoke(app, _argv(FIXTURE, output, when=when))
    assert result.exit_code == 1
    assert said("invalid_timestamp", result.output)
    assert not output.exists()


def test_missing_required_option_is_a_usage_error(tmp_path: Path) -> None:
    argv = _argv(FIXTURE, tmp_path / "a.json")[:-4]
    assert CliRunner().invoke(app, argv).exit_code == 2


@pytest.mark.parametrize("failure", ["timestamp", "identity", "navigation", "encoding"])
def test_refusal_preserves_existing_output(tmp_path: Path, failure: str) -> None:
    """Exit 1 writes nothing, including when the target already contains a record."""
    output = tmp_path / "record.json"
    previous = b'{"existing": "must survive"}\n'
    output.write_bytes(previous)
    payload = FIXTURE.read_bytes()
    when = WHEN
    if failure == "timestamp":
        when = "2100-04-01T12:00:00"
        reason = "invalid_timestamp"
    elif failure == "identity":
        payload = payload.replace(b'id9000001/"', b'id9000009/"')
        reason = "conflicting_identity"
    elif failure == "navigation":
        payload = payload.replace(b"data-longdoc-table-of-content", b"data-other")
        reason = "chapter_navigation_missing"
    else:
        payload = b"\xff"
        reason = "unreadable_page"
    saved = tmp_path / "saved.html"
    saved.write_bytes(payload)
    result = CliRunner().invoke(app, _argv(saved, output, when=when))
    assert result.exit_code == 1
    assert said(f"refused: {reason}", result.output)
    assert output.read_bytes() == previous
    assert saved.read_bytes() == payload
    assert {path.name for path in tmp_path.iterdir()} == {"record.json", "saved.html"}
