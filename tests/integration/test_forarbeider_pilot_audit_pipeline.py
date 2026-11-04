"""The PR4 pilot audit on one synthetic Prop. L and one Prop. LS, supported commands only (#738).

``synthetic-fixtures``, the PR3A review, the dossier, the owner's authorization
candidate and ``pilot-audit``: no real document is read here, so the real
pilot's status stays REAL_INPUT_NOT_PROVIDED. The audit's report is the very
preflight report, a revocation turns one document to DENY without touching the
other, and the engine checkout and the default corpus location, seeded with a
law corpus the MCP reader answers from, are byte-identical afterwards.
"""

import json
from pathlib import Path

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
from tests.unit.test_forarbeider_reader_isolation import _law_answers
from tests.unit.test_mcp import _record, _seed_corpus


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _audit(scope: list[str], authorization: str, checks: list[str]) -> tuple[int, str]:
    options = ["--at", "2100-04-10T12:00:00Z", "--authorization", authorization, *checks]
    result = RUNNER.invoke(app, [*scope, "pilot-audit", *options])
    return result.exit_code, result.output


def _verdicts(output: str) -> list[str]:
    return [line.split()[2] for line in output.splitlines() if line.startswith("chapter ")]


def test_the_pilot_audit_runs_one_l_and_one_ls_and_changes_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review_commands, "_interactive", lambda: True)
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    default_corpus = tmp_path / "cache" / "lovverk"
    _seed_corpus(default_corpus, {"nl-1": _record(slug="skatteloven", title="Skatteloven")})
    corpus_before, answers_before = _tree(default_corpus), _law_answers(default_corpus)
    engine_before = _git("status", "--porcelain", "--untracked-files=all", cwd=ROOT)
    group = ["ops", "forarbeider-release", "--workspace", str(tmp_path / "workspace")]
    group += ["--ledger", str(tmp_path / "ledger")]
    fixtures = tmp_path / "fixtures"
    _run([*group, "synthetic-fixtures", "--out", str(fixtures)])

    audited = {}
    for kind in KINDS:
        scope, report, checks = _release(tmp_path, fixtures, kind)
        stored = json.loads((tmp_path / "workspace" / "reports" / f"{report}.json").read_text())
        authorization = stored["authorization_sha256"]
        code, output = _audit(scope, authorization, checks)
        assert code == 0, output
        assert _digest(output, "report") == report
        assert _verdicts(output) == ["PASS", "PASS"]
        assert f"PILOT_AUDIT {KINDS[kind]} {kind} 2099-2100" in output
        assert "publication_authorized=false release_clearance=false" in output
        audited[kind] = (scope, authorization, checks)

    scope, authorization, checks = audited["prop-l"]
    revoke = [*group, "revoke", "--authorization", authorization]
    _run([*revoke, "--decided", "owner-b@2100-04-05T12:00:00Z"])
    code, output = _audit(scope, authorization, checks)
    assert code == 2
    assert _verdicts(output) == ["DENY", "DENY"]
    code, output = _audit(*audited["prop-ls"])
    assert (code, _verdicts(output)) == (0, ["PASS", "PASS"])

    assert _tree(default_corpus) == corpus_before
    assert _law_answers(default_corpus) == answers_before
    assert not (default_corpus / "forarbeider").exists()
    assert _git("status", "--porcelain", "--untracked-files=all", cwd=ROOT) == engine_before
    assert all(path.stat().st_mode & 0o077 == 0 for path in (tmp_path / "workspace").rglob("*"))
