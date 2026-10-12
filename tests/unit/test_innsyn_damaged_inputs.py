"""Every innsyn reader turns undecodable bytes into a refusal, never a traceback (#509).

Codex round 2 on PR #605 found invalid UTF-8 in ``requests.jsonl`` escaping as
a ``UnicodeDecodeError``. These are the sibling readers: each file an operator
can hand the commands, damaged with a byte that is not UTF-8.
"""

import shutil
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from tests.unit.innsyn_support import FIXTURES, fetched, innsyn

BAD = b"\xff\xfe not utf-8\n"
PAGE = "https://www.fjaler.kommune.no/"


def _ok(workspace: Path, *args: str) -> None:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output


def _refused(workspace: Path, *args: str) -> str:
    result = innsyn(workspace, *args)
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output
    return result.stderr


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _ok(target, "set-sender", "--name", "Test Requester", "--email", "kontakt@example.no")
    _ok(
        target,
        "set-recipient",
        "--id",
        "4646",
        "--address",
        "postmottak@fjaler.kommune.no",
        "--evidence-url",
        PAGE,
        "--read-at",
        "2026-10-03T10:00:00Z",
    )
    return target


@pytest.fixture
def lf_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "lf-ledger.jsonl"
    path.write_text("", encoding="utf-8")
    return path


def test_a_directory_with_invalid_utf8_is_refused(workspace: Path) -> None:
    with (workspace / "directory.jsonl").open("ab") as handle:
        handle.write(BAD)
    assert "directory.jsonl: not UTF-8" in _refused(workspace, "authorities")


def test_a_population_snapshot_with_invalid_utf8_is_refused(workspace: Path) -> None:
    (snapshot,) = (workspace / "population").glob("population-*.json")
    snapshot.write_bytes(snapshot.read_bytes()[:40] + BAD)
    assert "not a population snapshot" in _refused(workspace, "population")


def test_a_sender_with_invalid_utf8_is_refused(
    workspace: Path, tmp_path: Path, lf_ledger: Path
) -> None:
    (workspace / "sender.json").write_bytes(b'{"name": "' + BAD + b'"}')
    args = ("letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(lf_ledger))
    assert "not a sender record" in _refused(workspace, *args)


def test_a_held_file_with_invalid_utf8_is_refused(
    workspace: Path, tmp_path: Path, lf_ledger: Path
) -> None:
    held = tmp_path / "held.txt"
    held.write_bytes(BAD)
    args = ("letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(lf_ledger))
    assert "held.txt: not UTF-8" in _refused(workspace, *args, "--held", str(held))
    assert not (tmp_path / "w").exists()


def test_an_lf_ledger_with_invalid_utf8_is_refused(workspace: Path, tmp_path: Path) -> None:
    ledger = tmp_path / "lf-ledger.jsonl"
    ledger.write_bytes(BAD)
    args = ("letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(ledger))
    assert "lf-ledger.jsonl: not UTF-8" in _refused(workspace, *args)


def test_a_manifest_with_invalid_utf8_is_refused(
    workspace: Path, tmp_path: Path, lf_ledger: Path
) -> None:
    _ok(workspace, "letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(lf_ledger))
    manifest = tmp_path / "w" / "manifest.json"
    manifest.write_bytes(BAD)
    args = ("record-sent", "--manifest", str(manifest), "--id", "4646")
    assert "not a wave manifest" in _refused(workspace, *args, "--sent-at", "2026-10-13T08:00Z")
    assert not (workspace / "requests.jsonl").exists()


def test_a_letter_with_invalid_utf8_is_refused(
    workspace: Path, tmp_path: Path, lf_ledger: Path
) -> None:
    _ok(workspace, "letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(lf_ledger))
    (tmp_path / "w" / "4646.txt").write_bytes(BAD)
    manifest = str(tmp_path / "w" / "manifest.json")
    args = ("record-sent", "--manifest", manifest, "--id", "4646")
    _refused(workspace, *args, "--sent-at", "2026-10-13T08:00Z")
    assert not (workspace / "requests.jsonl").exists()


def test_a_pilot_register_with_invalid_utf8_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "pilot"
    shutil.copytree(FIXTURES / "pilot", copy)
    register = copy / "sent.csv"
    register.write_bytes(register.read_bytes() + BAD)
    stderr = _refused(tmp_path / "innsyn", "import-pilot", "--register", str(register))
    assert "sent.csv: not UTF-8" in stderr
    assert not (tmp_path / "innsyn" / "requests.jsonl").exists()


def test_a_pilot_letter_with_invalid_utf8_is_refused(tmp_path: Path) -> None:
    copy = tmp_path / "pilot"
    shutil.copytree(FIXTURES / "pilot", copy)
    (copy / "innsyn-4646.txt").write_bytes(BAD)
    _refused(tmp_path / "innsyn", "import-pilot", "--register", str(copy / "sent.csv"))
    assert not (tmp_path / "innsyn" / "requests.jsonl").exists()
