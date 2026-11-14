"""The reviewed owner-exception list, pointed at a test's own entries (#911, #921).

The gate reads ``owner_exceptions.REASSIGNMENTS_PATH``; a test migration lists
its own changes there, bound to its own base and approved by digest the way
the owner signs lovverk's (the approved path exists only in fixtures: the
packaged list stays pending until the owner signs it).
"""

from datetime import date
from pathlib import Path

import pytest

from lovspor.identity import owner_exceptions
from lovspor.identity.owner_exceptions import (
    APPROVED,
    PENDING,
    Approval,
    ReassignmentList,
    list_digest,
    list_json,
)
from lovspor.identity.published import migration_base
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
PLAN_SUMS = "a" * 64


def fixture_exception(first_commit: str, served_commit: str) -> dict[str, object]:
    """``x`` kept by NEW (taken at ``served_commit``) over OLD (first at ``first_commit``)."""
    return {
        "directory": "forskrifter",
        "slug": "x",
        "first_owner_doc_id": OLD,
        "served_owner_doc_id": NEW,
        "evidence": {
            "first_owner_first_commit": first_commit,
            "served_owner_first_commit": served_commit,
        },
        "reason": "fixture: the threshold keeps NEW",
    }


def pending_list(
    base: str,
    reassignments: list[dict[str, str]],
    exceptions: list[dict[str, object]] | None = None,
    threshold: str | None = None,
) -> ReassignmentList:
    """A pending list bound to ``base`` (and ``threshold``, the base by default)."""
    corpus = {
        "reviewed_base": base,
        "u1_threshold": threshold or base,
        "plan_sums_sha256": PLAN_SUMS,
    }
    return ReassignmentList.model_validate(
        {
            "schema_version": 2,
            "corpus": corpus,
            "reassignments": reassignments,
            "threshold_exceptions": exceptions or [],
            "approval": {"status": PENDING},
        }
    )


def signed(listed: ReassignmentList) -> ReassignmentList:
    """``listed`` approved by its own digest (fixtures only)."""
    approval = Approval(
        status=APPROVED,
        digest=list_digest(listed),
        approved_by="fixture owner",
        approved_on=date(2026, 11, 14),
    )
    return listed.model_copy(update={"approval": approval})


def install(monkeypatch: pytest.MonkeyPatch, path: Path, listed: ReassignmentList) -> Path:
    """Write ``listed`` to ``path`` and point the gate at it."""
    path.write_text(list_json(listed), encoding="utf-8")
    monkeypatch.setattr(owner_exceptions, "REASSIGNMENTS_PATH", path)
    return path


def list_reassignments(
    monkeypatch: pytest.MonkeyPatch,
    repo: Path,
    reassignments: list[dict[str, str]],
    exceptions: list[dict[str, object]] | None = None,
) -> Path:
    """Point the gate at an approved list holding exactly these entries, bound to
    ``repo``'s migration base."""
    base = migration_base(repo)
    assert base is not None
    listed = signed(pending_list(base, reassignments, exceptions))
    return install(monkeypatch, repo.parent / "owner_reassignments.json", listed)


def allow_fixture_u1(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """List the fixture migration's U1 rename, approved, as the owner lists lovverk's."""
    list_reassignments(monkeypatch, repo, [FIXTURE_U1])
