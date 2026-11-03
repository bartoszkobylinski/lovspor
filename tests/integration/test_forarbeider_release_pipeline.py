"""PR1 metadata, PR2 chapters, PR3A approval and the PR3B-1 preflight, end to end (#738).

Only supported commands, on synthetic Prop. L and Prop. LS pages: the state a
preview needs is reachable by an operator, and reaching it writes nothing to
any Git checkout or corpus.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import release_commands, review_commands
from tests.unit.forarbeider_release_support import RETRIEVED, page, private

ROOT = Path(__file__).parents[2]
RUNNER = CliRunner()


def _git_state() -> str:
    command = ["git", "status", "--porcelain", "--untracked-files=all"]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def _run(arguments: list[str], text: str | None = None) -> str:
    result = RUNNER.invoke(app, arguments, input=text)
    assert result.exit_code == 0, result.output
    return result.output


def _digest(output: str, word: str) -> str:
    match = re.search(rf"{word} ([0-9a-f]{{64}})", output)
    assert match is not None, output
    return match[1]


def _stage(root: Path, kind: str) -> tuple[Path, list[Path], list[Path]]:
    pages, observations = [], []
    for chapter in (2, 3):
        payload, url = page(kind, chapter)
        saved = private(root / "pages" / f"ch{chapter}.html", payload)
        observation = root / "staged" / f"ch{chapter}.json"
        command = ["ops", "forarbeider-chapter", "--html", str(saved), "--source-url", url]
        _run([*command, "--retrieved-at", RETRIEVED, "--output", str(observation)])
        pages.append(saved)
        observations.append(observation)
    metadata = root / "staged" / "metadata.json"
    _, url = page(kind, 2)
    command = ["ops", "forarbeider-metadata", "--html", str(pages[0]), "--source-url", url]
    _run([*command, "--retrieved-at", RETRIEVED, "--output", str(metadata)])
    metadata.chmod(0o600)
    return metadata, observations, pages


def _approve(ledger: Path, observation: Path) -> str:
    group = ["ops", "forarbeider-review", "--ledger", str(ledger)]
    shown = _run([*group, "show", "--observation", str(observation)])
    pin = re.search(r"--expect (\S+)", shown)
    assert pin is not None
    chapter_id = shown.splitlines()[0]
    text = re.search(r"text_sha256 ([0-9a-f]{64})", shown)
    assert text is not None
    actor = [*group, "--reviewer", "reviewer-a", "--decided-at", "2100-04-02T12:00:00Z"]
    attest = [
        f"--attest={kind}=adr:0018"
        for kind in (
            "privacy_basis",
            "source_completeness",
            "source_rights_policy",
            "third_party_checks",
        )
    ]
    _run(
        [*actor, "approve", "--observation", str(observation), "--expect", pin[1], *attest],
        f"approve {chapter_id} {text[1][:12]}\n",
    )
    verified = _run([*group, "verify"])
    line = next(row for row in verified.splitlines() if chapter_id in row)
    key, _, _, revision, _, _, _, head = line.split()
    return f"{key}@{revision}:{head}"


@pytest.mark.parametrize("kind", ["l", "ls"])
def test_supported_commands_reach_a_preview_that_writes_nothing_public(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    monkeypatch.setattr(review_commands, "_interactive", lambda: True)
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)
    before = _git_state()
    metadata, observations, pages = _stage(tmp_path, kind)
    ledger = tmp_path / "ledger"
    checkpoints = [_approve(ledger, observation) for observation in observations]
    evidence = []
    for name in (
        "publication_purpose",
        "privacy_processing_basis",
        "third_party_screening",
        "source_rights",
        "source_completeness",
    ):
        path = private(tmp_path / "evidence" / f"{name}.txt", f"synthetic {name}".encode())
        evidence += ["--evidence", f"{name}@2,3={path}"]
    release = ["ops", "forarbeider-release", "--workspace", str(tmp_path / "workspace")]
    scope = [*release, "--ledger", str(ledger), "chapters", "--metadata", str(metadata)]
    for observation, saved in zip(observations, pages, strict=True):
        scope += ["--observation", str(observation), "--page", str(saved)]
    dossier = _digest(
        _run([*scope, "dossier", *evidence, "--reviewed", "reviewer-a@2100-04-03T12:00:00Z"]),
        "dossier",
    )
    document = json.loads(observations[0].read_text())["id"].split("-ch")[0]
    grant = ["--dossier", dossier, "--decided", "owner-b@2100-04-04T12:00:00Z"]
    grant += ["--valid", "2100-04-04T12:00:00Z/2100-05-01T00:00:00Z"]
    authorized = _run([*scope, "authorize", *grant], f"authorize {document} {dossier[:12]}\n")
    authorization = _digest(authorized, "authorization")
    checks = [part for value in checkpoints for part in ("--checkpoint", value)]
    output = _run(
        [
            *scope,
            "preflight",
            "--at",
            "2100-04-10T12:00:00Z",
            "--authorization",
            authorization,
            *checks,
        ]
    )
    report = json.loads(
        (tmp_path / "workspace" / "reports" / f"{_digest(output, 'report')}.json").read_text()
    )
    assert report["outcome"] == "eligible_preview"
    assert report["publication_authorized"] is False
    assert report["planned_chapters"] == [2, 3]
    assert report["coverage"] == "partial_curated_excerpt"
    assert _git_state() == before
