"""``lovspor ops identity-apply``: the supported way to apply the #871 plan (#910).

Every case is a real temporary git repository and a plan directory written by
``lovspor ops identity-plan``: what is under test is that the command writes
exactly the plan the owner reviewed, at exactly its base, and nothing else.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.errors import IdentityMigrationError
from lovspor.identity import apply_commands
from lovspor.identity.apply import apply_plan, verify_applied
from lovspor.identity.plan import MigrationPlan, plan_migration
from lovspor.identity.report import SUMS_FILENAME
from tests.unit.identity_git_fixtures import commit_all, doc_text, git, write_doc
from tests.unit.test_identity_migration import NEW, _base_repo

SIDECAR = b"\x00\x01embedding of x\xff"


def _tagged(tmp_path: Path) -> Path:
    """The #871 base repository, its U1 threshold tagged ``u1`` (x's reuse falls after it)."""
    corpus, shas = _base_repo(tmp_path)
    git(corpus, "tag", "u1", shas["r3"])
    return corpus


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The #871 base repository, with an embedding sidecar for the renamed document."""
    corpus = _tagged(tmp_path)
    (corpus / "forskrifter/embeddings").mkdir()
    (corpus / "forskrifter/embeddings/x.bin").write_bytes(SIDECAR)
    commit_all(corpus, "embed(forskrift): x")
    return corpus


def _invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["ops", *args])
    return result.exit_code, result.output


def _plan(repo: Path, out: Path) -> Path:
    code, output = _invoke(
        "identity-plan",
        *("--repo", str(repo), "--base", "HEAD", "--u1-threshold", "u1", "--out", str(out)),
    )
    assert code == 0, output
    return out


def _pin(plan_dir: Path) -> str:
    """The operator's pin: the sha256 of the reviewed plan's ``SHA256SUMS``."""
    return hashlib.sha256((plan_dir / SUMS_FILENAME).read_bytes()).hexdigest()


def _apply(repo: Path, plan_dir: Path, *extra: str, pin: str | None = None) -> tuple[int, str]:
    if pin is None:
        pin = _pin(plan_dir) if (plan_dir / SUMS_FILENAME).is_file() else "0" * 64
    args = ("--repo", str(repo), "--plan", str(plan_dir), "--expect-sha256", pin, *extra)
    return _invoke("identity-apply", *args)


def _status(repo: Path) -> list[str]:
    """Modified, deleted and untracked paths, read with ``git ls-files``, not the code tested."""
    out = git(
        repo, "-c", "core.quotePath=false", "ls-files", "-m", "-d", "-o", "--exclude-standard"
    )
    return sorted(set(out.splitlines()))


def test_apply_writes_exactly_the_planned_paths_and_never_commits(
    repo: Path, tmp_path: Path
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    head = git(repo, "rev-parse", "HEAD")

    code, output = _apply(repo, plan_dir)

    assert code == 0, output
    report = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
    assert _status(repo) == report["changed_paths"]
    assert git(repo, "rev-parse", "HEAD") == head
    assert 'slug: "x-2026"' in (repo / "forskrifter/x-2026.md").read_text(encoding="utf-8")
    assert (repo / "forskrifter/embeddings/x-2026.bin").read_bytes() == SIDECAR
    assert not (repo / "forskrifter/x.md").exists()
    assert not (repo / "forskrifter/embeddings/x.bin").exists()
    summary = json.loads(output.strip().splitlines()[-1])
    assert summary["verified_files"] == len(report["planned_files_sha256"]) + 1
    assert summary["changed_paths_sha256"] == report["changed_paths_sha256"]


def test_the_plan_carries_the_renamed_markdown_and_the_sidecar_digest(
    repo: Path, tmp_path: Path
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")

    report = json.loads((plan_dir / "report.json").read_text(encoding="utf-8"))
    planned = (plan_dir / "planned/forskrifter/x-2026.md").read_text(encoding="utf-8")
    assert planned.startswith(f'---\nid: "{NEW}"\nslug: "x-2026"\n')
    assert "planned/forskrifter/x-2026.md" in report["planned_files_sha256"]
    assert list(report["planned_sidecars_sha256"]) == ["forskrifter/embeddings/x-2026.bin"]
    assert report["moved_from_paths"] == ["forskrifter/embeddings/x.bin", "forskrifter/x.md"]
    assert report["move_problems"] == []


def test_a_head_other_than_the_planned_base_is_refused(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    write_doc(repo, "README.md", "later\n")
    commit_all(repo, "a commit after the plan")

    code, output = _apply(repo, plan_dir)

    assert code == 2
    assert "is not the planned base" in output
    assert _status(repo) == []


def test_a_dirty_checkout_is_refused(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    write_doc(repo, "stray.txt", "x\n")

    code, output = _apply(repo, plan_dir)

    assert code == 2
    assert "not clean" in output
    assert _status(repo) == ["stray.txt"]


def test_a_second_apply_is_refused_before_and_after_the_commit(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    assert _apply(repo, plan_dir)[0] == 0
    applied = _status(repo)

    assert _apply(repo, plan_dir)[0] == 2
    assert _status(repo) == applied
    commit_all(repo, "migration: repair document identity (#871)")
    assert _apply(repo, plan_dir)[0] == 2
    assert _status(repo) == []


def test_a_plan_file_that_does_not_match_its_checksums_is_refused(
    repo: Path, tmp_path: Path
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    registry = plan_dir / "planned/slugs.json"
    registry.write_text(registry.read_text(encoding="utf-8").replace(NEW, "sf-99999999-9999"))

    code, output = _apply(repo, plan_dir)

    assert code == 2
    assert "planned/slugs.json" in output
    assert _status(repo) == []


def test_a_forged_plan_with_matching_checksums_is_refused(repo: Path, tmp_path: Path) -> None:
    """A plan directory edited and re-summed is not the plan this base produces."""
    plan_dir = _plan(repo, tmp_path / "plan")
    registry = plan_dir / "planned/slugs.json"
    registry.write_text(registry.read_text(encoding="utf-8").replace(NEW, "sf-99999999-9999"))
    sums = (plan_dir / SUMS_FILENAME).read_text(encoding="utf-8").splitlines()
    rewritten = [
        line if not line.endswith("planned/slugs.json") else f"{_sha(registry)}  planned/slugs.json"
        for line in sums
    ]
    (plan_dir / SUMS_FILENAME).write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    code, output = _apply(repo, plan_dir)

    assert code == 2
    assert "not the plan" in output
    assert _status(repo) == []


def test_an_extra_file_in_the_plan_directory_is_refused(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    (plan_dir / "planned/extra.md").write_text("x\n", encoding="utf-8")

    assert _apply(repo, plan_dir)[0] == 2
    assert _status(repo) == []


def test_a_plan_directory_inside_the_checkout_is_refused(repo: Path, tmp_path: Path) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    inside = repo / "plan"
    shutil.copytree(plan_dir, inside)

    assert _apply(repo, inside)[0] == 2


def test_a_missing_sidecar_is_refused_loudly(repo: Path) -> None:
    plan = plan_migration(repo, "HEAD", "u1")
    (repo / "forskrifter/embeddings/x.bin").unlink()

    with pytest.raises(
        IdentityMigrationError, match="embeddings/x.bin: planned move source is missing"
    ):
        apply_plan(repo, plan)


@pytest.mark.parametrize(
    "front_matter",
    [
        '---\nid: "{doc}"\nslug: x\ntype: "forskrift"\n---\n',  # unquoted slug
        '---\nid: "{doc}"\nslug: "other"\n---\n',  # another slug
        '---\nid: "sf-00000000-0000"\nslug: "x"\n---\n',  # another document
        "no front matter\n",
    ],
    ids=["unquoted-slug", "other-slug", "other-id", "none"],
)
def test_an_unmatched_slug_rewrite_is_reported_and_refused(
    tmp_path: Path, front_matter: str
) -> None:
    corpus = _tagged(tmp_path)
    write_doc(corpus, "forskrifter/x.md", front_matter.format(doc=NEW) + "\nbody\n")
    commit_all(corpus, "update(forskrift): x")
    plan = plan_migration(corpus, "HEAD", "u1")

    assert len(plan.moves.problems) == 1
    assert "forskrifter/x.md" in plan.moves.problems[0]
    assert "forskrifter/x-2026.md" not in plan.moves.markdown
    with pytest.raises(IdentityMigrationError, match="no planned slug rewrite"):
        apply_plan(corpus, plan)


def test_a_plan_with_move_problems_is_refused_by_the_command(tmp_path: Path) -> None:
    corpus = _tagged(tmp_path)
    write_doc(corpus, "forskrifter/x.md", "no front matter\n")
    commit_all(corpus, "update(forskrift): x")
    plan_dir = _plan(corpus, tmp_path / "plan")

    code, output = _apply(corpus, plan_dir)

    assert code == 2
    assert "forskrifter/x.md" in output
    assert _status(corpus) == []


def test_verification_names_a_file_that_differs_from_the_plan(repo: Path) -> None:
    plan = plan_migration(repo, "HEAD", "u1")
    apply_plan(repo, plan)
    assert verify_applied(repo, plan) == []
    (repo / "forskrifter/embeddings/x-2026.bin").write_bytes(b"tampered")
    write_doc(repo, "forskrifter/x.md", doc_text(NEW, "x", "back"))

    problems = verify_applied(repo, plan)

    assert [p.split(":")[0] for p in problems] == [
        "forskrifter/embeddings/x-2026.bin",
        "forskrifter/x.md",
    ]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("damage", ["missing-file", "missing-sums", "duplicate-entry", "malformed"])
def test_incomplete_or_malformed_plan_is_refused_without_writes(
    repo: Path, tmp_path: Path, damage: str
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    sums = plan_dir / SUMS_FILENAME
    if damage == "missing-file":
        (plan_dir / "planned/slugs.json").unlink()
    elif damage == "missing-sums":
        sums.unlink()
    elif damage == "duplicate-entry":
        content = sums.read_text(encoding="utf-8")
        sums.write_text(content + content.splitlines()[0] + "\n", encoding="utf-8")
    else:
        sums.write_text("no separator\n", encoding="utf-8")
    before = {
        p.relative_to(repo): p.read_bytes()
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }

    code, output = _apply(repo, plan_dir)

    assert code == 2, output
    assert _status(repo) == []
    assert {p: (repo / p).read_bytes() for p in before} == before


@pytest.mark.parametrize("failure", ["partial-write", "wrong-bytes", "extra-path"])
def test_apply_reports_exit_three_when_written_tree_cannot_be_verified(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    plan_dir = _plan(repo, tmp_path / "plan")
    head = git(repo, "rev-parse", "HEAD")

    def damaged_apply(corpus: Path, plan: MigrationPlan) -> None:
        if failure == "partial-write":
            write_doc(corpus, "partial.txt", "interrupted\n")
            raise IdentityMigrationError("synthetic interruption after writing")
        apply_plan(corpus, plan)
        if failure == "wrong-bytes":
            (corpus / "forskrifter/embeddings/x-2026.bin").write_bytes(b"wrong")
        else:
            write_doc(corpus, "extra.txt", "unexpected\n")

    monkeypatch.setattr(apply_commands, "apply_plan", damaged_apply)

    code, output = _apply(repo, plan_dir)

    assert code == 3, output
    problems = json.loads(output.strip().splitlines()[-1])["problems"]
    expected = {
        "partial-write": "partially written: synthetic interruption after writing",
        "wrong-bytes": "forskrifter/embeddings/x-2026.bin: sha256",
        "extra-path": "git status differs from changed_paths: extra ['extra.txt'], absent []",
    }[failure]
    assert any(expected in problem for problem in problems)
    assert _status(repo)
    assert git(repo, "rev-parse", "HEAD") == head
