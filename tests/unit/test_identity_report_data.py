"""The fields of ``report.json`` the identity-migration dry run publishes (#871).

``report_data`` is read by name — by the owner's review, by ``identity-plan``'s
summary line and by any script over ``report.json`` — so each top-level field
and each review list is pinned under its published key.
"""

from pathlib import Path

import pytest

from lovspor.identity.plan import plan_migration
from lovspor.identity.repair_report import HISTORY_REPAIR_NOTE
from lovspor.identity.report import REPORT_SCHEMA_VERSION, report_data
from tests.unit.test_identity_migration import LOST, OLD, _base_repo


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _base_repo(tmp_path)[0]


def test_the_report_names_its_schema_issue_mode_and_outcome(repo: Path) -> None:
    data = report_data(plan_migration(repo, "HEAD"))

    assert {
        name: data[name]
        for name in (
            "schema_version",
            "issue",
            "mode",
            "nothing_to_do",
            "history_repair_note",
            "unrecoverable_doc_ids",
            "orphan_history_doc_ids_not_restored",
        )
    } == {
        "schema_version": REPORT_SCHEMA_VERSION,
        "issue": 871,
        "mode": "dry-run",
        "nothing_to_do": False,
        "history_repair_note": HISTORY_REPAIR_NOTE,
        "unrecoverable_doc_ids": [],
        "orphan_history_doc_ids_not_restored": [],
    }


def test_the_restored_tombstones_are_listed_for_review(repo: Path) -> None:
    data = report_data(plan_migration(repo, "HEAD"))

    restored = data["restored_tombstones"]
    assert isinstance(restored, list)
    assert [(row["doc_id"], row["last_status"]) for row in restored] == [
        (OLD, "removed"),
        (LOST, "current"),
    ]
