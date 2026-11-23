"""The closing sections of ``report.md`` for the identity-migration dry run (#871).

After the review tables the owner reads which ``doc_id``s the plan cannot answer
for, which orphan histories it does not restore, and whether the history repair
keeps its invariant, with each violation listed under it.
"""

from pathlib import Path

from lovspor.identity.plan import plan_migration
from lovspor.identity.repair_report import HISTORY_REPAIR_NOTE
from lovspor.identity.report import render_markdown
from tests.unit.identity_git_fixtures import commit_all
from tests.unit.test_identity_history_repair import (
    LATER,
    PATH,
    _build,
    _by_id_at_base,
    _event,
    _later_by_id,
)
from tests.unit.test_identity_migration import _base_repo, _history

_GHOST = "sf-19990101-0009"


def test_a_doc_id_no_manifest_held_is_listed_as_unrecoverable_and_not_restored(
    tmp_path: Path,
) -> None:
    repo, _ = _base_repo(tmp_path)
    _history(repo, "ghost", _GHOST)
    commit_all(repo, "a history file nobody recorded")

    markdown = render_markdown(plan_migration(repo, "HEAD"))

    assert "## Fate of every reused slug\n\n| directory | slug |" in markdown
    assert markdown[markdown.index("## Unrecoverable") :] == (
        "## Unrecoverable doc_ids\n"
        "\n"
        "| doc_id |\n"
        "|---|\n"
        f"| `{_GHOST}` |\n"
        "\n"
        "## Orphan-history doc_ids not restored\n"
        "\n"
        "| doc_id |\n"
        "|---|\n"
        f"| `{_GHOST}` |\n"
        "\n"
        "## History repair\n"
        "\n"
        f"{HISTORY_REPAIR_NOTE}.\n"
        "\n"
        "Invariant (each event in exactly one document's history): `True`.\n"
    )


def test_a_broken_history_invariant_is_reported_with_each_violation(tmp_path: Path) -> None:
    repo, shas = _build(tmp_path)
    wrong = {
        "doc_id": LATER,
        "events": [_event(shas["owner_remove"][:7], PATH)],
        "schema_version": 1,
    }
    _by_id_at_base(repo, _later_by_id(), wrong)
    plan = plan_migration(repo, "HEAD", shas["threshold"])
    violations = plan.history_repair.invariant_violations
    assert violations

    markdown = render_markdown(plan)

    assert markdown[markdown.index("## History repair\n") :] == (
        "## History repair\n"
        "\n"
        f"{HISTORY_REPAIR_NOTE}.\n"
        "\n"
        "Invariant (each event in exactly one document's history): `False`.\n"
        + "".join(f"- {violation}\n" for violation in violations)
    )
