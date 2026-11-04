"""The PR3B-2 synthetic promotion, end to end through supported commands only (#738).

From the bundled synthetic fixtures through PR3A approval, the PR3B-1 dossier,
authorization and preflight, to the PR3B-2 plan, a sandbox, the simulated
write and a withdrawal. The engine checkout, the default corpus location and
every path outside the sandbox's own dataset are byte-identical afterwards,
and the sandbox has no commit, no index and no remote.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import release_commands, review_commands
from lovspor.forarbeider.corpus_models import parse_manifest
from lovspor.forarbeider.corpus_tree import BASELINE, read_dataset
from tests.unit.forarbeider_release_support import private

ROOT = Path(__file__).parents[2]
RUNNER = CliRunner()
GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")
KINDS = {"prop-l": "regjeringen-9000001", "prop-ls": "regjeringen-9000003"}


def _git(*arguments: str, cwd: Path) -> str:
    assert GIT is not None
    done = subprocess.run([GIT, *arguments], cwd=cwd, capture_output=True, text=True, check=False)
    return done.stdout


def _run(arguments: list[str], text: str | None = None) -> str:
    result = RUNNER.invoke(app, arguments, input=text)
    assert result.exit_code == 0, result.output
    return result.output


def _digest(output: str, word: str) -> str:
    match = re.search(rf"{word} ([0-9a-f]{{64}})", output)
    assert match is not None, output
    return match[1]


def _approve(ledger: Path, observation: Path) -> str:
    group = ["ops", "forarbeider-review", "--ledger", str(ledger)]
    shown = _run([*group, "show", "--observation", str(observation)])
    pin = re.search(r"--expect (\S+)", shown)
    text = re.search(r"text_sha256 ([0-9a-f]{64})", shown)
    assert pin is not None and text is not None
    chapter_id = shown.splitlines()[0]
    actor = [*group, "--reviewer", "reviewer-a", "--decided-at", "2100-04-02T12:00:00Z"]
    kinds = ("privacy_basis", "source_completeness", "source_rights_policy", "third_party_checks")
    attest = [f"--attest={kind}=adr:0018" for kind in kinds]
    _run(
        [*actor, "approve", "--observation", str(observation), "--expect", pin[1], *attest],
        f"approve {chapter_id} {text[1][:12]}\n",
    )
    line = next(row for row in _run([*group, "verify"]).splitlines() if chapter_id in row)
    key, _, _, revision, _, _, _, head = line.split()
    return f"{key}@{revision}:{head}"


def _evidence(root: Path) -> list[str]:
    options = []
    for name in (
        "publication_purpose",
        "privacy_processing_basis",
        "third_party_screening",
        "source_rights",
        "source_completeness",
    ):
        path = private(root / "evidence" / f"{name}.txt", f"synthetic {name}".encode())
        options += ["--evidence", f"{name}@2,3={path}"]
    return options


def _release(root: Path, fixtures: Path, kind: str) -> tuple[list[str], str, list[str]]:
    """Approve, dossier, authorize and preflight one proposition: scope, report, checkpoints."""
    ledger = root / "ledger"
    observations = [fixtures / f"{kind}-ch{n}.json" for n in (2, 3)]
    checkpoints = [_approve(ledger, observation) for observation in observations]
    release = ["ops", "forarbeider-release", "--workspace", str(root / "workspace")]
    scope = [*release, "--ledger", str(ledger), "chapters"]
    scope += ["--metadata", str(fixtures / f"{kind}-metadata.json")]
    for number, observation in zip((2, 3), observations, strict=True):
        scope += [
            "--observation",
            str(observation),
            "--page",
            str(fixtures / f"{kind}-ch{number}.html"),
        ]
    reviewed = ["--reviewed", "reviewer-a@2100-04-03T12:00:00Z"]
    dossier = _digest(_run([*scope, "dossier", *_evidence(root / kind), *reviewed]), "dossier")
    grant = ["--dossier", dossier, "--decided", "owner-b@2100-04-04T12:00:00Z"]
    grant += ["--valid", "2100-04-04T12:00:00Z/2100-05-01T00:00:00Z"]
    confirm = f"authorize {KINDS[kind]} {dossier[:12]}\n"
    authorization = _digest(_run([*scope, "authorize", *grant], confirm), "authorization")
    checks = [part for value in checkpoints for part in ("--checkpoint", value)]
    at = ["--at", "2100-04-10T12:00:00Z", "--authorization", authorization]
    report = _digest(_run([*scope, "preflight", *at, *checks]), "report")
    return scope, report, checks


def _outside_dataset(sandbox: Path) -> dict[str, bytes]:
    return {
        path.relative_to(sandbox).as_posix(): path.read_bytes()
        for path in sorted(sandbox.rglob("*"))
        if path.is_file() and "forarbeider" not in path.relative_to(sandbox).parts[:1]
    }


def test_a_synthetic_promotion_and_withdrawal_touch_only_the_sandbox_dataset(
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
    outside = _outside_dataset(sandbox)

    for kind, document in KINDS.items():
        scope, report, checks = _release(tmp_path, fixtures, kind)
        planned = _run([*scope, "plan", "--report", report, "--corpus", str(sandbox), *checks])
        assert "publication_authorized=false" in planned
        applied = _run(
            [*scope, "simulate", "--plan", _digest(planned, "plan"), "--sandbox", str(sandbox)]
        )
        assert applied.startswith(f"APPLIED {document}")

    documents = read_dataset(sandbox).manifest.documents
    assert [item.document.id for item in documents] == list(KINDS.values())
    assert all(item.coverage == "partial_curated_excerpt" for item in documents)
    for item in documents:
        markdown = (sandbox / item.path).read_text()
        assert "NOT enacted law" in markdown and "source_license: null" in markdown
        assert "NLOD" not in markdown

    withdrawn = _run(
        [
            *group,
            "sandbox-withdraw",
            "--sandbox",
            str(sandbox),
            "--document",
            KINDS["prop-l"],
            "--decided",
            "owner-b@2100-04-12T12:00:00Z",
        ],
        f"withdraw {KINDS['prop-l']}\n",
    )
    assert "a revert does not erase them" in withdrawn

    manifest = parse_manifest((sandbox / "forarbeider" / "manifest.json").read_bytes())
    assert [item.release_status for item in manifest.documents] == ["withdrawn", "released"]
    assert not (sandbox / documents[0].path).exists()
    assert (sandbox / documents[1].path).exists()
    assert _outside_dataset(sandbox) == outside
    assert {name: (sandbox / name).read_text() for name in BASELINE} == BASELINE
    assert _git("rev-parse", "--verify", "-q", "HEAD", cwd=sandbox) == ""
    assert _git("remote", cwd=sandbox) == ""
    assert not (sandbox / ".git" / "index").exists()
    assert _git("status", "--porcelain", "--untracked-files=all", cwd=ROOT) == engine_before
    assert not (tmp_path / "cache").exists()
    stored = json.loads(next((tmp_path / "workspace" / "withdrawals").iterdir()).read_text())
    assert stored["history_erased"] is False
