"""The reviewed owner reassignment list, pointed at a test's own entries (#911).

The gate reads ``owner_continuity.REASSIGNMENTS_PATH``; a test migration lists
its own changes there, as the owner lists lovverk's in the packaged file.
"""

import json
from pathlib import Path

import pytest

from lovspor.identity import owner_continuity
from tests.unit.test_identity_migration import NEW, OLD

#: The fixture migration's one owner change against its base: the U1 rename of ``x``.
FIXTURE_U1 = {
    "directory": "forskrifter",
    "slug": "x",
    "from_doc_id": NEW,
    "to_doc_id": OLD,
    "kind": "u1-rename",
    "reason": "fixture: x's later holder is renamed",
}


def list_reassignments(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    reassignments: list[dict[str, str]],
    threshold_exceptions: list[dict[str, str]] | None = None,
) -> None:
    """Point the gate at a reviewed list holding exactly these entries."""
    path = tmp_path / "owner_reassignments.json"
    data = {
        "schema_version": 1,
        "reassignments": reassignments,
        "threshold_exceptions": threshold_exceptions or [],
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(owner_continuity, "REASSIGNMENTS_PATH", path)


def allow_fixture_u1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """List the fixture migration's U1 rename, as the owner lists lovverk's."""
    list_reassignments(monkeypatch, tmp_path, [FIXTURE_U1])
