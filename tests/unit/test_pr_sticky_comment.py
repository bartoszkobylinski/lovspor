"""Contract tests for scripts/ci/pr_sticky_comment.sh.

The escalation contract is unchanged — every blocked round still reaches the
PR with its run and artifact. What changes is that a round is *appended* to
one comment per workflow instead of creating a new one, because GitHub mails
the author on comment creation and not on edit.

`gh` is stubbed, but the stub evaluates the script's `--jq` filter with the
real jq binary: the filter is the part that decides whether a round appends or
starts a second comment, so emulating it would test the stub instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "ci" / "pr_sticky_comment.sh"
_MARKER = "<!-- lovspor-sticky:{key} -->"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="stub needs jq")

_GH_STUB = '''#!/usr/bin/env python3
"""Minimal `gh` good enough for pr_sticky_comment.sh, backed by a JSON file."""
import json, os, pathlib, subprocess, sys

state = pathlib.Path(os.environ["STICKY_STUB_STATE"])
comments = json.loads((state / "comments.json").read_text())
argv = sys.argv[1:]
with (state / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps(argv) + "\\n")


def jq(program, payload):
    out = subprocess.run(
        ["jq", "-r", program], input=json.dumps(payload),
        text=True, capture_output=True, check=True,
    )
    return out.stdout


if argv[0] == "pr" and argv[1] == "comment":
    body = pathlib.Path(argv[argv.index("--body-file") + 1]).read_text()
    comments.append({"id": 900 + len(comments), "body": body})
    (state / "comments.json").write_text(json.dumps(comments))
elif "--method" in argv and argv[argv.index("--method") + 1] == "PATCH":
    field = argv[argv.index("-f") + 1]
    body = field.split("=", 1)[1]
    path = next(a for a in argv if "/issues/comments/" in a)
    target = int(path.rsplit("/", 1)[1])
    for comment in comments:
        if comment["id"] == target:
            comment["body"] = body
    (state / "comments.json").write_text(json.dumps(comments))
elif argv[0] == "api" and "/issues/comments/" in argv[1]:
    target = int(argv[1].rsplit("/", 1)[1])
    hit = next(c for c in comments if c["id"] == target)
    sys.stdout.write(jq(argv[argv.index("--jq") + 1], hit))
elif argv[0] == "api":
    sys.stdout.write(jq(argv[argv.index("--jq") + 1], comments))
else:
    sys.stderr.write("unstubbed gh: %s\\n" % argv)
    sys.exit(1)
'''


def _workspace(tmp_path: Path, comments: list[dict[str, object]]) -> Path:
    state = tmp_path / "state"
    (state / "bin").mkdir(parents=True)
    (state / "comments.json").write_text(json.dumps(comments))
    stub = state / "bin" / "gh"
    stub.write_text(_GH_STUB)
    stub.chmod(0o755)
    return state


def _escalate(
    state: Path, body: str, *, key: str = "pipeline", repo: str | None = "o/r"
) -> subprocess.CompletedProcess[str]:
    body_file = state / "body.md"
    body_file.write_text(body)
    env = {
        "PATH": f"{state / 'bin'}:/usr/bin:/bin",
        "STICKY_STUB_STATE": str(state),
    }
    if repo is not None:
        env["GH_REPO"] = repo
    return subprocess.run(
        [str(_SCRIPT), key, "230", str(body_file)],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )


def _comments(state: Path) -> list[dict[str, object]]:
    return json.loads((state / "comments.json").read_text())


def test_first_escalation_creates_one_comment_carrying_the_marker(tmp_path: Path) -> None:
    state = _workspace(tmp_path, [])

    result = _escalate(state, "codex-tests BLOCKED: round A")

    assert result.returncode == 0, result.stderr
    body = str(_comments(state)[0]["body"])
    assert body.startswith(_MARKER.format(key="pipeline"))
    assert "round A" in body


def test_second_escalation_appends_rather_than_creating_a_second_comment(
    tmp_path: Path,
) -> None:
    state = _workspace(tmp_path, [])
    _escalate(state, "codex-tests BLOCKED: round A")

    result = _escalate(state, "codex-tests BLOCKED: round B")

    assert result.returncode == 0, result.stderr
    assert len(_comments(state)) == 1, "a second comment would mail the author again"
    body = str(_comments(state)[0]["body"])
    assert "round A" in body and "round B" in body
    assert body.index("round A") < body.index("round B")


def test_append_preserves_markdown_unicode_and_string_like_values(tmp_path: Path) -> None:
    seeded = _MARKER.format(key="pipeline") + "\n\nfirst round"
    state = _workspace(tmp_path, [{"id": 42, "body": seeded}])
    new_round = "true\n\n- `utf-8`: æøå — 🚨\n- [run](https://example.test/run)"

    result = _escalate(state, new_round)

    assert result.returncode == 0, result.stderr
    assert _comments(state) == [{"id": 42, "body": f"{seeded}\n\n---\n\n{new_round}"}]


def test_duplicate_sticky_comments_update_only_the_first_match(tmp_path: Path) -> None:
    marker = _MARKER.format(key="pipeline")
    first = {"id": 41, "body": f"{marker}\n\nfirst copy"}
    duplicate = {"id": 42, "body": f"{marker}\n\nracing copy"}
    state = _workspace(tmp_path, [first, duplicate])

    result = _escalate(state, "new round")

    assert result.returncode == 0, result.stderr
    assert _comments(state) == [
        {"id": 41, "body": f"{marker}\n\nfirst copy\n\n---\n\nnew round"},
        duplicate,
    ]


def test_an_unrelated_comment_never_becomes_the_sticky_one(tmp_path: Path) -> None:
    state = _workspace(tmp_path, [{"id": 11, "body": "a human review comment"}])

    _escalate(state, "codex-tests BLOCKED: round A")

    assert _comments(state)[0] == {"id": 11, "body": "a human review comment"}
    assert len(_comments(state)) == 2


def test_the_two_workflows_keep_separate_comments(tmp_path: Path) -> None:
    state = _workspace(tmp_path, [])
    _escalate(state, "codex-tests BLOCKED", key="pipeline")

    _escalate(state, "Mutation remediation BLOCKED", key="mutation")

    bodies = [str(c["body"]) for c in _comments(state)]
    assert len(bodies) == 2, "a shared comment lets one workflow drop the other's round"
    assert any("codex-tests BLOCKED" in b for b in bodies)
    assert any("Mutation remediation BLOCKED" in b for b in bodies)


def test_oldest_rounds_are_trimmed_and_the_newest_always_survives(
    tmp_path: Path,
) -> None:
    rounds = [f"round {i} " + "x" * 2000 for i in range(30)]
    seeded = _MARKER.format(key="pipeline") + "\n\n" + "\n\n---\n\n".join(rounds)
    state = _workspace(tmp_path, [{"id": 42, "body": seeded}])

    result = _escalate(state, "round 30 NEWEST")

    assert result.returncode == 0, result.stderr
    body = str(_comments(state)[0]["body"])
    assert len(body) < 65536, "GitHub rejects a longer body with a 422"
    assert body.startswith(_MARKER.format(key="pipeline"))
    assert body.rstrip().endswith("round 30 NEWEST")
    assert "round 0 " not in body
    assert "Older rounds trimmed" in body


def test_a_body_under_the_limit_is_never_trimmed(tmp_path: Path) -> None:
    seeded = _MARKER.format(key="pipeline") + "\n\nround 0"
    state = _workspace(tmp_path, [{"id": 42, "body": seeded}])

    _escalate(state, "round 1")

    body = str(_comments(state)[0]["body"])
    assert "round 0" in body
    assert "Older rounds trimmed" not in body


def test_an_unreadable_body_file_fails_loudly(tmp_path: Path) -> None:
    state = _workspace(tmp_path, [])
    env = {
        "PATH": f"{state / 'bin'}:/usr/bin:/bin",
        "GH_REPO": "o/r",
        "STICKY_STUB_STATE": str(state),
    }

    result = subprocess.run(
        [str(_SCRIPT), "pipeline", "230", str(state / "absent.md")],
        check=False,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert "body file not readable" in result.stderr
    assert _comments(state) == []


def test_a_missing_repository_fails_instead_of_guessing_one(tmp_path: Path) -> None:
    state = _workspace(tmp_path, [])

    result = _escalate(state, "round A", repo=None)

    assert result.returncode != 0
    assert "GH_REPO or GITHUB_REPOSITORY" in result.stderr
    assert _comments(state) == []
