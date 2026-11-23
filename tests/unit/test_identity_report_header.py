"""The header of ``report.md`` for the identity-migration dry run (#871).

``report.md`` renders the data of ``report.json``: the header names the base, the
U1 threshold and the outcome, then tabulates every count and every planned
file's digest, and closes with the digest of the changed-path list.
"""

import hashlib
from collections.abc import Mapping
from pathlib import Path

from lovspor.identity.plan import plan_migration
from lovspor.identity.report import render_markdown, report_data
from tests.unit.test_identity_migration import _base_repo


def _rows(table: Mapping[str, object]) -> str:
    return "".join(f"| `{name}` | `{value}` |\n" for name, value in table.items())


def test_the_header_renders_the_base_threshold_counts_and_digests(tmp_path: Path) -> None:
    repo, shas = _base_repo(tmp_path)
    plan = plan_migration(repo, "HEAD", shas["r4"])
    data = report_data(plan)
    counts = data["counts"]
    sums = data["planned_files_sha256"]
    assert isinstance(counts, dict)
    assert isinstance(sums, dict)
    changed = data["changed_paths"]
    assert isinstance(changed, list)
    assert len(changed) > 1
    listing = hashlib.sha256("".join(f"{p}\n" for p in changed).encode()).hexdigest()

    markdown = render_markdown(plan)

    assert markdown.split("## Identity schema")[0] == (
        "# Identity migration dry run (#871)\n"
        "\n"
        f"Base commit: `{data['base_commit']}`. Mode: dry-run; nothing was written to the "
        "corpus.\n"
        f"U1 threshold: `{plan.threshold.ref}` = `{shas['r4']}` "
        f"(problem: `{plan.threshold.problem}`).\n"
        "Nothing to do: `False`. Blocked: `False`.\n"
        "\n"
        "## Counts\n"
        "\n"
        "| count | value |\n"
        "|---|---|\n"
        f"{_rows(counts)}"
        "\n"
        "## Checksums\n"
        "\n"
        "| file | sha256 |\n"
        "|---|---|\n"
        f"{_rows(sums)}"
        "\n"
        f"SHA-256 over the sorted changed-path list: `{listing}`.\n"
        "\n"
    )
    assert plan.threshold.ref == shas["r4"]
