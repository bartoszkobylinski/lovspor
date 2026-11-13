"""`.gitleaksignore` may only silence exact, justified findings (#822).

gitleaks reads this file in every scan: the pre-commit staged scan and the Security
audit's tree and history scans. An entry is a full fingerprint
(``commit:file:rule:line``), so the same text in another commit, file or line is
still reported. A path, a rule, a bare commit or a glob would hide new findings.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IGNORE = ROOT / ".gitleaksignore"
FINGERPRINT = re.compile(r"[0-9a-f]{40}:[^:\s*?]+:[a-z0-9-]+:[1-9][0-9]*")
ISSUE = re.compile(r"#[1-9][0-9]*\b")


def test_ignore_file_is_carried_into_the_test_shadow_tree() -> None:
    """The fingerprint tests need their input during the clean baseline (#822)."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert ".gitleaksignore" in config["tool"]["mutmut"]["also_copy"]
    assert IGNORE.is_file()


def _entries() -> list[tuple[str, list[str]]]:
    """Each entry with the comment block directly above it."""
    entries: list[tuple[str, list[str]]] = []
    comments: list[str] = []
    for line in IGNORE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            comments.append(line)
        elif line.strip():
            entries.append((line, comments))
            comments = []
        else:
            comments = []
    return entries


def test_every_entry_is_one_exact_fingerprint() -> None:
    entries = _entries()
    assert entries
    for entry, _ in entries:
        assert FINGERPRINT.fullmatch(entry), entry


def test_every_entry_names_the_issue_that_verified_it() -> None:
    for entry, comments in _entries():
        assert any(ISSUE.search(comment) for comment in comments), entry


def test_the_822_false_positive_is_pinned_to_its_commit_and_line() -> None:
    expected = (
        "4d9b78911ce84f6e39d7b926b87255ba1d36d4b7:"
        "tests/unit/test_site_sources.py:generic-api-key:209"
    )
    assert [entry for entry, _ in _entries()] == [expected]
