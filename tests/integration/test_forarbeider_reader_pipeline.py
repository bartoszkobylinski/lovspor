"""The opt-in reader over the operator's own synthetic run (#738, ADR-0018 PR3B-3).

Only supported commands: ``synthetic-fixtures``, the PR3A review, dossier,
authorization, preflight, ``plan``, ``sandbox-create``, ``simulate``, the
reader and ``sandbox-withdraw``. Reading changes no byte of the sandbox, the
engine checkout or the default corpus location.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from lovspor.cli import app
from lovspor.forarbeider import release_commands, review_commands
from tests.integration.test_forarbeider_promotion_pipeline import (
    KINDS,
    ROOT,
    RUNNER,
    _digest,
    _git,
    _release,
    _run,
)


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _read(sandbox: Path, *arguments: str) -> Any:
    return RUNNER.invoke(app, ["ops", "forarbeider-read", "--sandbox", str(sandbox), *arguments])


def _named(document: str, kind: str) -> list[str]:
    return ["--document", document, "--session", "2099-2100", "--type", kind]


def test_the_reader_serves_released_synthetic_documents_and_drops_withdrawn_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review_commands, "_interactive", lambda: True)
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    engine_before = _git("status", "--porcelain", "--untracked-files=all", cwd=ROOT)
    group = ["ops", "forarbeider-release", "--workspace", str(tmp_path / "workspace")]
    group += ["--ledger", str(tmp_path / "ledger")]
    fixtures = tmp_path / "fixtures"
    _run([*group, "synthetic-fixtures", "--out", str(fixtures)])
    sandbox = tmp_path / "sandbox"
    _run([*group, "sandbox-create", "--path", str(sandbox)])
    for kind in KINDS:
        scope, report, checks = _release(tmp_path, fixtures, kind)
        planned = _run([*scope, "plan", "--report", report, "--corpus", str(sandbox), *checks])
        _run([*scope, "simulate", "--plan", _digest(planned, "plan"), "--sandbox", str(sandbox)])
    before = _tree(tmp_path)

    listing = json.loads(_read(sandbox, "list").stdout)
    assert [item["document"]["id"] for item in listing["documents"]] == list(KINDS.values())
    for kind, document in KINDS.items():
        shown = json.loads(_read(sandbox, "show", *_named(document, kind)).stdout)
        assert shown["labels"]["normative"] is False
        assert shown["partial_curated_excerpt"] is True
        chapter = _read(sandbox, "chapter", *_named(document, kind), "--chapter", "2")
        assert json.loads(chapter.stdout)["markdown"].startswith(
            "Source: <https://www.regjeringen.no/"
        )
        provenance = json.loads(_read(sandbox, "provenance", *_named(document, kind)).stdout)
        assert provenance["verification"] == "synthetic_rerender_byte_identical"
    assert _tree(tmp_path) == before

    withdraw = [*group, "sandbox-withdraw", "--sandbox", str(sandbox)]
    _run(
        [*withdraw, "--document", KINDS["prop-l"], "--decided", "owner-b@2100-04-12T12:00:00Z"],
        f"withdraw {KINDS['prop-l']}\n",
    )

    listing = json.loads(_read(sandbox, "list").stdout)
    assert [item["document"]["id"] for item in listing["documents"]] == [KINDS["prop-ls"]]
    refused = _read(sandbox, "chapter", *_named(KINDS["prop-l"], "prop-l"), "--chapter", "2")
    assert refused.exit_code == 1
    assert "refused: document_not_released: document" in refused.output
    assert _git("status", "--porcelain", "--untracked-files=all", cwd=ROOT) == engine_before
    assert not (tmp_path / "cache").exists()
