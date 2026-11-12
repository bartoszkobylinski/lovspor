"""Synthetic lovverk repositories for the document-identity tests (#871).

Small real git repos built in ``tmp_path``: the identity defects live in how
git history is walked, so a mock would hide exactly what the tests check.
"""

import json
import subprocess
from pathlib import Path

_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2026-06-01T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-06-01T12:00:00+00:00",
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "HOME": "/nonexistent",
    "GIT_CONFIG_NOSYSTEM": "1",
}


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` with a fixed identity and clock; return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=_ENV,
    )
    return result.stdout.strip()


def init_repo(repo: Path) -> Path:
    """An empty repository on ``main`` with signing off."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


def doc_text(doc_id: str, slug: str, body: str) -> str:
    """A Markdown file in the renderer's front-matter shape."""
    return f'---\nid: "{doc_id}"\nslug: "{slug}"\ntype: "forskrift"\n---\n\n{body}\n'


def commit_all(repo: Path, subject: str) -> str:
    """Stage everything, commit with ``subject``, return the full sha."""
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "HEAD")


def write_doc(repo: Path, rel_path: str, text: str) -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def record(doc_id: str, slug: str, status: str = "current", directory: str = "forskrifter") -> dict:
    """A raw manifest record as a sync writes it."""
    dataset = "gjeldende-lover" if directory == "lover" else "gjeldende-sentrale-forskrifter"
    return {
        "doc_type": "lov" if directory == "lover" else "forskrift",
        "xml_hash": f"hash-{doc_id}",
        "markdown_path": f"{directory}/{slug}.md",
        "source_dataset": dataset,
        "last_seen": "2026-06-01T00:00:00Z",
        "status": status,
        "slug": slug,
        "title": f"Title of {doc_id}",
    }


def write_manifest_json(repo: Path, documents: dict[str, dict], generated_at: str) -> None:
    payload = {"version": 1, "generated_at": generated_at, "documents": documents}
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    (repo / "manifest.json").write_text(text, encoding="utf-8")


def tree_of(repo: Path, rev: str = "HEAD") -> str:
    return git(repo, "rev-parse", f"{rev}^{{tree}}")
