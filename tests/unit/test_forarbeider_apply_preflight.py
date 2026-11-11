"""``forarbeider-release batch preflight``, end to end (#738, B1a).

Every state is reached through supported commands: staging, the PR3A review,
dossier, authorization and preflight, a standing policy signed at a
terminal, a real fetch run made offline through ``pytest-httpx``, a stored
batch plan, and a scratch lovverk release clone made with real Git. The
preflight must write nothing anywhere: each case, a pass and every refusal,
compares the whole ``tmp_path`` tree (workspace, ledger, staging, fetch
output and the clone with its ``.git``) byte for byte and mtime for mtime.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import apply_preflight as preflight_module
from lovspor.forarbeider.apply_preflight import (
    ApplyPreflightRequest,
    _difference,
    apply_preflight,
    preflight_lines,
)
from lovspor.forarbeider.batch_models import BatchPlan
from lovspor.forarbeider.release_commands import ReleaseScope
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import option_help, said
from tests.unit.forarbeider_batch_support import (
    BRANCH,
    CHAPTER_MARKER,
    PLAN_AT,
    RUN_AT,
    BatchDesk,
)
from tests.unit.forarbeider_checkout_support import git, release_clone, snapshot, trust
from tests.unit.forarbeider_policy_support import PolicyDesk
from tests.unit.forarbeider_release_support import (
    OWNER,
    PRIVATE_MARKER,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

QUOTE = "<blockquote><p>Sitat.</p></blockquote>"
TABLE = "<table><tr><td>x</td></tr></table>"
NOW = datetime(2100, 4, 10, 13, 0, tzinfo=UTC)
DOC1, DOC2 = "regjeringen-9000101", "regjeringen-9000102"


class Bench:
    """A batch desk, a release clone on the batch branch, and the preflight's clock."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.batch = BatchDesk(root)
        self.clone = release_clone(root / "release")
        self.monkeypatch = monkeypatch
        trust(monkeypatch, self.clone)
        self.at(NOW)

    def at(self, moment: datetime) -> None:
        self.monkeypatch.setattr(preflight_module, "_now", lambda: moment)

    def mixed(self, httpx_mock: HTTPXMock) -> Path:
        """Doc 1: clean 2 and 3. Doc 2: clean 2, flagged 3 with an eligible report."""
        self.batch.add(1)
        self.batch.add(2, extras={3: QUOTE})
        review = self.batch.flagged_report(2, (3,))
        self.batch.ready(httpx_mock)
        return self.batch.request([self.batch.document(1), self.batch.document(2, review)])

    def planned(self, request: Path, at: str = PLAN_AT) -> str:
        result = self.batch.plan(request, at=at)
        assert result.exit_code in (0, 2), result.output
        return said_digest(result.output, "BATCH PLAN")

    def head(self) -> list[str]:
        workspace = ["--workspace", str(self.batch.workspace)]
        return ["ops", "forarbeider-release", *workspace, "--ledger", str(self.batch.ledger)]

    def preflight(self, sha: str, request: Path, **given: Any) -> Any:
        """Run the preflight and assert it changed nothing below ``tmp_path``."""
        proof = given.get("proof") or self.batch.run_file
        checkout = given.get("checkout", self.clone)
        head = given.get("head") or self.head()
        arguments = [sha, "--checkout", str(checkout), "--request", str(request)]
        before = snapshot(self.root)
        result = CliRunner().invoke(
            app, [*head, "batch", "preflight", *arguments, "--source-proof", str(proof)]
        )
        assert snapshot(self.root) == before
        assert CHAPTER_MARKER not in result.output
        assert PRIVATE_MARKER not in result.output
        assert str(self.root) not in result.output
        return result

    def scope(self) -> ReleaseScope:
        return ReleaseScope(workspace=self.batch.workspace, ledger=self.batch.ledger)

    def ask(self, sha: str, request: Path) -> ApplyPreflightRequest:
        proof = self.batch.run_file
        assert proof is not None
        return ApplyPreflightRequest(
            plan=sha, checkout=self.clone, request=request, source_proof=proof
        )


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Bench:
    return Bench(tmp_path, monkeypatch)


def _refused(result: Any, reason: str, detail: str, code: int = 1) -> None:
    assert result.exit_code == code, result.output
    assert said(f"refused: {reason}: {detail}", result.output), result.output
    assert "APPLY PREFLIGHT PASS" not in result.output


def _not_reproduced(result: Any, detail: str) -> None:
    _refused(result, "plan_not_reproduced", detail, code=2)


# --- the pass --------------------------------------------------------------------------------


def test_an_unchanged_world_passes_and_prints_the_binding(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    result = bench.preflight(sha, request)

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].startswith(f"APPLY PREFLIGHT PASS plan {sha} binding ")
    head = git(bench.clone, "rev-parse", "HEAD").strip()
    assert lines[1] == f"target {BRANCH} head {head} base forarbeider/manifest.json absent"
    assert lines[2] == "included 4 (standing_policy 3, owner_review 1) excluded 0 documents 2"
    assert lines[3].startswith(f"document {DOC1} forarbeider/2099-2100/prop-l/9000101.md")
    assert lines[3].endswith("chapters 2,3")
    assert lines[-1] == (
        "writes_corpus=false applies=false owner_confirmation=required_not_given "
        f"evaluated_at {NOW.isoformat()}"
    )


def test_the_command_prints_what_apply_preflight_returns_and_the_binding_is_stable(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    first = apply_preflight(bench.scope(), bench.ask(sha, request), NOW)
    bench.at(NOW + timedelta(hours=1))
    later = apply_preflight(bench.scope(), bench.ask(sha, request), NOW + timedelta(hours=1))
    result = bench.preflight(sha, request)

    assert result.output.rstrip("\n") == preflight_lines(later)
    assert first.binding_sha256 == later.binding_sha256
    assert first.binding == later.binding
    assert first.evaluated_at != later.evaluated_at
    assert first.binding.plan_sha256 == sha
    assert first.binding.target_branch == BRANCH
    assert first.binding.remote == "github.com/bartoszkobylinski/lovverk"


def test_the_binding_lists_only_included_chapters(bench: Bench, httpx_mock: HTTPXMock) -> None:
    bench.batch.add(1, extras={3: TABLE})
    bench.batch.add(2, extras={3: QUOTE})
    bench.batch.ready(httpx_mock)
    documents = [bench.batch.document(1), bench.batch.document(2)]
    request = bench.batch.request(documents)
    sha = bench.planned(request)

    verdict = apply_preflight(bench.scope(), bench.ask(sha, request), NOW)

    assert [item.split("@")[0] for item in verdict.binding.included] == [
        f"{DOC1}-ch2",
        f"{DOC2}-ch2",
    ]
    assert all(len(item.split("@")[1]) == 64 for item in verdict.binding.included)
    assert verdict.binding.counts.excluded == 2
    assert said("chapters 2", bench.preflight(sha, request).output)


def test_a_plan_made_at_an_offset_moment_reproduces(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request, at="2100-04-10T14:00:00+02:00")

    assert bench.preflight(sha, request).exit_code == 0


def test_the_preflight_states_its_options_and_that_it_writes_nothing() -> None:
    path = ["ops", "forarbeider-release", "batch", "preflight"]
    assert "release clone" in str(option_help(app, path, "--checkout"))
    assert "request" in str(option_help(app, path, "--request"))
    assert "fetch run" in str(option_help(app, path, "--source-proof"))
    result = CliRunner().invoke(
        app, [*path[:2], "--workspace", "/w", "--ledger", "/l", *path[2:], "--help"]
    )
    assert said("Read-only", result.output)


# --- the plan no longer reproduces: exit 2 ---------------------------------------------------


def test_a_restaged_page_is_not_reproduced(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    page = bench.batch.desks[1].pages[2]
    page.write_bytes(page.read_bytes() + b"\n")

    _not_reproduced(bench.preflight(sha, request), "inputs")


def test_a_new_review_decision_is_not_reproduced(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    bench.batch.desks[1].decide(2, ReviewStatus.HELD, at="2100-04-10T12:30:00Z")

    _not_reproduced(bench.preflight(sha, request), "inputs")


def test_another_checkpoint_is_not_reproduced(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    payload = json.loads(request.read_text())
    payload["documents"][1]["review"]["checkpoints"] = []
    changed = bench.batch.request(payload["documents"])

    _not_reproduced(bench.preflight(sha, changed), "inputs")


def test_an_authorization_revoked_since_the_plan_is_not_reproduced(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    report = json.loads(request.read_text())["documents"][1]["review"]["report"]
    stored = json.loads((bench.batch.workspace / "reports" / f"{report}.json").read_text())
    revoke = ["revoke", "--authorization", stored["authorization_sha256"]]
    revoke += ["--decided", f"{OWNER}@2100-04-10T12:30:00Z"]
    assert CliRunner().invoke(app, [*bench.head(), *revoke]).exit_code == 0

    result = bench.preflight(sha, request)

    _not_reproduced(result, f"{DOC2}-ch3: authorization_revoked")


def test_an_authorization_expired_since_the_plan_is_not_reproduced(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    bench.batch.add(1)
    bench.batch.add(2, extras={3: QUOTE})
    review = bench.batch.flagged_report(2, (3,))
    fetched = datetime(2100, 4, 30, 12, 0, tzinfo=UTC)
    bench.batch.sign_policy(bench.batch.fetch(httpx_mock, bench.batch.pages(), at=fetched))
    request = bench.batch.request([bench.batch.document(1), bench.batch.document(2, review)])
    sha = bench.planned(request, at="2100-04-30T13:00:00Z")
    bench.at(datetime(2100, 5, 1, 1, 0, tzinfo=UTC))

    _not_reproduced(bench.preflight(sha, request), f"{DOC2}-ch3: authorization_expired")


def test_a_policy_revoked_since_the_plan_is_not_reproduced(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    policy = PolicyDesk(bench.root / "revoker")
    policy.workspace, policy.ledger = bench.batch.workspace, bench.batch.ledger
    assert policy.revoke(bench.batch.policy_sha, "2100-04-10T12:30:00Z").exit_code == 0

    _not_reproduced(bench.preflight(sha, request), f"{DOC1}-ch2: policy_revoked")


def test_a_source_proof_older_than_a_day_is_not_reproduced(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    bench.at(RUN_AT + timedelta(hours=24, minutes=1))

    _not_reproduced(bench.preflight(sha, request), f"{DOC1}-ch2: source_proof_stale")


def test_another_source_proof_is_not_reproduced(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    first = bench.batch.run_file
    later = bench.batch.fetch(httpx_mock, bench.batch.pages(), at=RUN_AT + timedelta(hours=1))
    assert later != first

    _not_reproduced(bench.preflight(sha, request, proof=later), "source_proof")


# --- refusals: exit 1 ------------------------------------------------------------------------


def test_an_unknown_malformed_or_edited_plan_is_refused(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    _refused(bench.preflight("0" * 64, request), "release_record_missing", "0" * 64)
    _refused(bench.preflight("not-a-digest", request), "release_invalid", "plan")
    stored = bench.batch.workspace / "batch_plans" / f"{sha}.json"
    stored.write_text(stored.read_text().replace(BRANCH, "forarbeider/edited"))
    _refused(bench.preflight(sha, request), "release_record_corrupt", sha)


def test_a_plan_that_includes_nothing_is_refused(bench: Bench, httpx_mock: HTTPXMock) -> None:
    bench.batch.add(1)
    bench.batch.ready(httpx_mock)
    request = bench.batch.request([bench.batch.document(1)])
    sha = bench.planned(request, at="2100-04-11T12:00:00Z")

    _refused(bench.preflight(sha, request), "batch_nothing_included", sha)


def test_a_request_for_another_branch_is_refused(bench: Bench, httpx_mock: HTTPXMock) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    other = bench.batch.request(json.loads(request.read_text())["documents"], "forarbeider/other")

    _refused(bench.preflight(sha, other), "target_branch_mismatch", "request")


def test_a_checkout_the_guard_refuses_is_refused_before_any_evaluation(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    git(bench.clone, "switch", "--quiet", "main")
    _refused(bench.preflight(sha, request), "checkout_branch_mismatch", "branch")
    git(bench.clone, "switch", "--quiet", BRANCH)
    (bench.clone / "stray.txt").write_text("x\n")
    _refused(bench.preflight(sha, request), "checkout_dirty", "untracked")


def test_a_missing_workspace_is_refused_and_never_created(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    missing = bench.root / "no-workspace"
    head = ["ops", "forarbeider-release", "--workspace", str(missing)]
    head += ["--ledger", str(bench.batch.ledger)]

    _refused(bench.preflight(sha, request, head=head), "release_record_missing", "workspace")
    assert not missing.exists()


def test_the_difference_names_inputs_then_the_first_changed_chapter_then_the_plan(
    bench: Bench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    plan = BatchPlan.model_validate(bench.batch.stored(sha))
    first = plan.documents[0]
    edited = first.chapters[0].model_copy(update={"text_sha256": "9" * 64})
    stored = plan.model_copy(
        update={
            "documents": (
                first.model_copy(update={"chapters": (edited, *first.chapters[1:])}),
                *plan.documents[1:],
            )
        }
    )
    excluded = edited.model_copy(update={"reasons": ("policy_revoked", "review_veto")})
    replayed = plan.model_copy(
        update={
            "documents": (
                first.model_copy(update={"chapters": (excluded, *first.chapters[1:])}),
                *plan.documents[1:],
            )
        }
    )

    assert _difference(plan.model_copy(update={"inputs_sha256": "0" * 64}), plan) == "inputs"
    assert _difference(plan, stored) == f"{DOC1}-ch2: included"
    assert _difference(replayed, plan) == f"{DOC1}-ch2: policy_revoked,review_veto"
    assert _difference(plan, plan) == "plan"
