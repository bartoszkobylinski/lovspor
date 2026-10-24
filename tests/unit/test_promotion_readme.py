"""Every ``lovspor promote`` write keeps the corpus README's status block current (#661).

Reached the way the operator reaches it: the register through ``observatory
register-source``, the approval through ``promote approve``, the document
through ``promote local``, the withdrawal through ``promote withdraw``, and
the commit by running exactly the lines the command printed.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from lovspor.corpus_readme import STATUS_END, STATUS_START
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.commands import staged_paths
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import html_page

SLUG = "forskrift-om-renovasjon-og-slam-eksempel-kommune"
PROSE = "# lovverk\n\nHand-written intro.\n\n## Local regulations\n\nHand-written prose.\n"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    corpus = make_corpus(tmp_path)
    (corpus / "README.md").write_text(PROSE, encoding="utf-8")
    git(corpus, "add", "README.md")
    git(corpus, "commit", "-q", "-m", "docs: hand-written README")
    return corpus


def _promote(root: Path, corpus: Path, tmp_path: Path) -> str:
    sha256 = store(root, html_page())
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return result.stdout


def _run_printed_commit(stdout: str) -> None:
    for line in stdout.splitlines():
        if line.startswith("  git -C "):
            subprocess.run(shlex.split(line), check=True, capture_output=True)


def _readme(corpus: Path) -> str:
    return (corpus / "README.md").read_text(encoding="utf-8")


def test_promote_local_writes_the_block_and_stages_the_readme(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    stdout = _promote(root, corpus, tmp_path)

    where = shlex.quote(str(corpus.resolve()))
    assert "wrote lokale-forskrifter/manifest.json\nwrote README.md\n" in stdout
    assert f"  git -C {where} add -- lokale-forskrifter README.md\n" in stdout
    readme = _readme(corpus)
    assert readme.startswith("# lovverk\n\nHand-written intro.\n\n" + STATUS_START + "\n")
    assert readme.endswith(STATUS_END + "\n\n## Local regulations\n\nHand-written prose.\n")
    assert "| `forskrifter/` | 1 | Lovdata public data, NLOD 2.0 | 2026-04-29 |" in readme
    assert "| `lokale-forskrifter/` | 1 | åndsverkloven § 14 |" in readme
    assert "holds 1 local regulation from 1 authority" in readme
    assert f"kommune {AUTHORITY} (1)." in readme


def test_the_printed_commit_leaves_a_clean_checkout_with_the_readme_in_it(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    _run_printed_commit(_promote(root, corpus, tmp_path))

    assert git(corpus, "status", "--porcelain") == ""
    changed = git(corpus, "show", "--name-only", "--format=", "HEAD").split()
    assert "README.md" in changed
    assert not any(path == "manifest.json" or path.startswith("lover/") for path in changed)


def test_a_withdrawal_brings_the_block_back_to_no_current_regulation(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    _run_printed_commit(_promote(root, corpus, tmp_path))
    decision = tmp_path / "withdrawal.json"
    body = {
        "decision": "withdraw",
        "removed_reason": "withdrawn_misclassified",
        "decided_by": REVIEWER,
        "reviewer_role": REVIEWER_ROLE,
        "reason": "Klassifisert feil: siden er en høring, ikke en vedtatt forskrift.",
    }
    decision.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")

    result = invoke(
        "promote", "withdraw", "--authority", AUTHORITY, "--slug", SLUG,
        "--corpus", str(corpus), "--decision", str(decision),
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "wrote README.md\n" in result.stdout
    assert "`lokale-forskrifter/` holds no current local regulation." in _readme(corpus)


def test_a_readme_with_broken_markers_is_kept_and_reported(
    root: Path, corpus: Path, tmp_path: Path
) -> None:
    broken = f"# lovverk\n\n{STATUS_START}\nno end marker\n"
    (corpus / "README.md").write_text(broken, encoding="utf-8")
    git(corpus, "commit", "-q", "-am", "docs: break the markers")
    sha256 = store(root, html_page())
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0

    result = promote("local", sha256, corpus)

    assert result.exit_code == 0, result.output
    assert _readme(corpus) == broken
    assert "wrote README.md" not in result.stdout
    assert result.stderr == (
        "Warning: README.md corpus-status markers are broken; it was left unchanged.\n"
    )


def test_staged_paths_name_the_readme_only_when_the_checkout_has_one(tmp_path: Path) -> None:
    assert staged_paths(tmp_path) == "lokale-forskrifter"
    (tmp_path / "README.md").write_text("# lovverk\n", encoding="utf-8")

    assert staged_paths(tmp_path) == "lokale-forskrifter README.md"
