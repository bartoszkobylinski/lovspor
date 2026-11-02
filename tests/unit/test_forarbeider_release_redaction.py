"""A refusal names its reason and the input's role, never a private path or name (#765).

Every staged input, the ledger and the workspace is made missing, malformed,
oversized, symlinked or reachable by others in turn. The CLI must still say
``refused: <reason>: <role>`` and print no path, file name or private marker.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider import release_commands, release_store
from tests.unit.cli_output import said, unwrapped
from tests.unit.forarbeider_release_support import (
    PRIVATE_MARKER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")
RED = pytest.mark.xfail(strict=True, reason="final review on PR #765")

Breaker = Callable[[Path], None]


def _missing(path: Path) -> None:
    path.unlink()


def _malformed(path: Path) -> None:
    path.write_bytes(f'{{"marker": "{PRIVATE_MARKER}"'.encode())


def _symlinked(path: Path) -> None:
    copy = path.with_name(f"copy-{path.name}")
    copy.write_bytes(path.read_bytes())
    copy.chmod(0o600)
    path.unlink()
    path.symlink_to(copy)


def _shared(path: Path) -> None:
    path.chmod(0o644)


def _private_names(desk: Desk) -> list[str]:
    files = [desk.metadata, *desk.observations.values(), *desk.pages.values()]
    names = [str(desk.root), *(path.name for path in files)]
    return [*names, *(path.name for path in desk.evidence.values())]


def _assert_redacted(desk: Desk, result: Any, phrase: str) -> None:
    assert result.exit_code == 1, result.output
    assert said(phrase, result.output), result.output
    printed = unwrapped(result.output)
    for private in [PRIVATE_MARKER, *_private_names(desk)]:
        assert unwrapped(private) not in printed, private
    assert "/" not in result.output.split("refused:", 1)[1]


def _ready(tmp_path: Path) -> tuple[Desk, str, list[str]]:
    """An authorized desk and its checkpoints, read before anything is broken."""
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    return desk, authorization, desk.all_checkpoints()


_STAGED_CASES = [
    ("metadata", _missing, "input_unreadable"),
    ("metadata", _malformed, "schema_invalid"),
    ("metadata", _symlinked, "unsafe_input_path"),
    ("metadata", _shared, "observation_unsafe"),
    ("observation", _missing, "input_unreadable"),
    ("observation", _malformed, "schema_invalid"),
    ("observation", _symlinked, "unsafe_input_path"),
    ("observation", _shared, "observation_unsafe"),
    ("page", _missing, "input_unreadable"),
    ("page", _symlinked, "unsafe_input_path"),
    ("page", _shared, "observation_unsafe"),
]


def _staged_path(desk: Desk, role: str) -> Path:
    if role == "metadata":
        return desk.metadata
    return desk.observations[3] if role == "observation" else desk.pages[3]


@RED
@pytest.mark.parametrize(("role", "breaker", "reason"), _STAGED_CASES)
def test_a_broken_staged_input_is_refused_by_role_at_preflight(
    tmp_path: Path, role: str, breaker: Breaker, reason: str
) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    breaker(_staged_path(desk, role))

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: {reason}: {role}")
    assert not (desk.workspace / "reports").exists()


@RED
@pytest.mark.parametrize(("role", "breaker", "reason"), _STAGED_CASES[:8])
def test_a_broken_staged_input_is_refused_by_role_at_dossier(
    tmp_path: Path, role: str, breaker: Breaker, reason: str
) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    breaker(_staged_path(desk, role))

    _assert_redacted(desk, desk.dossier(), f"refused: {reason}: {role}")
    assert not (desk.workspace / "dossiers").exists()


@RED
@pytest.mark.parametrize("role", ["metadata", "observation", "page"])
def test_an_oversized_staged_input_is_refused_by_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    if role == "page":
        monkeypatch.setattr(release_commands, "MAX_HTML_BYTES", 8)
    else:
        limit = 8 if role == "metadata" else desk.metadata.stat().st_size
        monkeypatch.setattr(release_store, "MAX_STAGED_BYTES", limit)

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: input_too_large: {role}")


def _shared_directory(path: Path) -> None:
    path.chmod(0o755)


@RED
@pytest.mark.parametrize("role", ["ledger", "workspace"])
def test_a_shared_ledger_or_workspace_is_refused_by_role(tmp_path: Path, role: str) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    _shared_directory(desk.ledger if role == "ledger" else desk.workspace)

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: review_ledger_unsafe: {role}")


@RED
@pytest.mark.parametrize("role", ["ledger", "workspace"])
def test_a_ledger_or_workspace_inside_a_checkout_is_refused_by_role(
    tmp_path: Path, role: str
) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    setattr(desk, role, checkout / role)

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: unsafe_output_path: {role}")


@RED
def test_an_unsafe_ledger_chapter_is_refused_by_role(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = desk.dossier()
    chain = next(path for path in desk.ledger.iterdir() if path.is_dir())
    chain.chmod(0o755)

    result = desk.authorize(said_digest(dossier.output, "dossier"))

    _assert_redacted(desk, result, "refused: review_ledger_unsafe: ledger")


@RED
def test_a_corrupt_ledger_record_is_refused_by_role_at_authorization(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier().output, "dossier")
    record = next(desk.ledger.rglob("*.json"))
    record.write_bytes(record.read_bytes() + b" ")

    result = desk.authorize(dossier)

    _assert_redacted(desk, result, "refused: review_ledger_corrupt: ledger")


@RED
@pytest.mark.parametrize("breaker", [_shared, _symlinked], ids=["shared", "symlinked"])
def test_an_unsafe_stored_record_names_its_digest_not_its_path(
    tmp_path: Path, breaker: Breaker
) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    breaker(desk.workspace / "authorizations" / f"{authorization}.json")

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: observation_unsafe: {authorization}")


@RED
def test_an_oversized_stored_record_names_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    monkeypatch.setattr(release_store, "MAX_RECORD_BYTES", 8)

    result = desk.preflight(authorization, checkpoints)

    _assert_redacted(desk, result, f"refused: input_too_large: {authorization}")


@RED
def test_a_report_that_cannot_be_written_names_its_folder(tmp_path: Path) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    reports = desk.workspace / "reports"
    reports.mkdir(mode=0o700)
    reports.chmod(0o500)
    try:
        result = desk.preflight(authorization, checkpoints)
    finally:
        reports.chmod(0o700)

    _assert_redacted(desk, result, "refused: output_write_failed: reports")


@RED
def test_a_path_given_as_a_timestamp_is_not_echoed(tmp_path: Path) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)

    result = desk.preflight(authorization, checkpoints, at=str(desk.metadata))

    _assert_redacted(desk, result, "refused: invalid_timestamp: at")


@RED
def test_a_path_given_as_a_decision_time_is_not_echoed(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    desk.dossier_at = str(desk.metadata)

    _assert_redacted(desk, desk.dossier(), "refused: invalid_timestamp: actor")


@RED
def test_a_path_given_as_a_validity_bound_is_not_echoed(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier().output, "dossier")

    result = desk.authorize(dossier, valid=f"{desk.metadata}/2100-05-01T00:00:00Z")

    _assert_redacted(desk, result, "refused: invalid_timestamp: valid")


def test_a_malformed_page_is_denied_without_a_path_in_the_report(tmp_path: Path) -> None:
    desk, authorization, checkpoints = _ready(tmp_path)
    desk.pages[3].write_bytes(f"<html>{PRIVATE_MARKER}</html>".encode())

    result = desk.preflight(authorization, checkpoints)

    assert result.exit_code != 1, result.output
    reports = list((desk.workspace / "reports").iterdir())
    for text in [result.output, *(path.read_text() for path in reports)]:
        for private in [PRIVATE_MARKER, *_private_names(desk)]:
            assert private not in text, private
