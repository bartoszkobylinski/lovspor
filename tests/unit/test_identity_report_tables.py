"""The review tables of ``report.md`` for the identity-migration dry run (#871).

The owner reviews the plan in ``report.md``: each list is a Markdown table (or
``(none)``) followed by a blank line, and a holder cell names the document, its
role and status, its manifest span and where it ends up.
"""

from pathlib import Path

from lovspor.identity.plan import plan_migration
from lovspor.identity.report import render_markdown
from tests.unit.identity_git_fixtures import (
    commit_all,
    doc_text,
    record,
    write_doc,
    write_manifest_json,
)
from tests.unit.test_identity_migration import KEEP, NEW, OLD, _base_repo, _history


def _section(markdown: str, title: str) -> str:
    start = markdown.index(f"## {title}")
    end = markdown.index("\n## ", start + 1)
    return markdown[start : end + 1]


def test_a_reused_slug_lists_each_holder_with_its_span_and_destination(tmp_path: Path) -> None:
    repo, shas = _base_repo(tmp_path)

    markdown = render_markdown(plan_migration(repo, "HEAD"))

    old = f"`{OLD}` (owner, removed, {shas['r1']}..{shas['r2']}), now at `x`"
    new = f"`{NEW}` (later, current, {shas['r4']}..{shas['r4']}) → x-2026"
    assert _section(markdown, "Fate of every reused slug") == (
        "## Fate of every reused slug\n"
        "\n"
        "| directory | slug | owner | reuse | holders |\n"
        "|---|---|---|---|---|\n"
        f"| `forskrifter` | `x` | `{OLD}` | post-threshold | {old}; {new} |\n"
        "\n"
    )
    assert _section(markdown, "Threshold exceptions") == (
        "## Threshold exceptions (owner set by the U1 threshold, not the first holder)\n"
        "\n"
        "(none)\n"
        "\n"
    )


def test_a_prior_holder_cell_carries_its_span_alone(tmp_path: Path) -> None:
    """A registry prior holder has no role or status: only its id and span."""
    repo, shas = _base_repo(tmp_path)

    markdown = render_markdown(plan_migration(repo, "HEAD", shas["r4"]))

    assert _section(markdown, "Threshold exceptions") == (
        "## Threshold exceptions (owner set by the U1 threshold, not the first holder)\n"
        "\n"
        "| directory | slug | owner | basis | prior_holders |\n"
        "|---|---|---|---|---|\n"
        f"| `forskrifter` | `x` | `{NEW}` | `threshold-exception` "
        f"| `{OLD}` ({shas['r1']}..{shas['r2']}) |\n"
        "\n"
    )
    fates = _section(markdown, "Fate of every reused slug")
    assert f"`{NEW}` (owner, current, {shas['r4']}..{shas['r4']}), now at `x` |\n" in fates


def test_every_kind_of_reuse_an_address_saw_is_listed(tmp_path: Path) -> None:
    """Held before its threshold owner and again after the threshold: both reuses show."""
    repo, shas = _base_repo(tmp_path)
    newer = "sf-20261101-0007"
    write_doc(repo, "forskrifter/x.md", doc_text(newer, "x", "newest body"))
    _history(repo, "x", newer)
    documents = {
        KEEP: record(KEEP, "c"),
        NEW: record(NEW, "x", "removed"),
        newer: record(newer, "x"),
    }
    write_manifest_json(repo, documents, "2026-11-01T00:00:00Z")
    commit_all(repo, "add(forskrift): x")

    markdown = render_markdown(plan_migration(repo, "HEAD", shas["r4"]))

    fates = _section(markdown, "Fate of every reused slug")
    assert f"| `forskrifter` | `x` | `{NEW}` | post-threshold, pre-threshold | " in fates
