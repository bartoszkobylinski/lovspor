"""``forarbeider-release batch apply``, end to end (#738, contract Amendment 1, B1b).

Synthetic propositions, an offline fetch run, a stored plan and a scratch
release clone with real Git. Each case asserts what reached the clone, the
workspace and the output: only ``forarbeider/`` of the working tree changes,
only included chapters are rendered, nothing is staged or committed, and no
chapter text, private note, handle or path is printed.
"""

import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider.apply_preflight import ApplyPreflightRequest, apply_preflight
from lovspor.forarbeider.batch_apply import releases_of
from lovspor.forarbeider.corpus_models import MANIFEST_PATH, parse_any_manifest
from lovspor.forarbeider.corpus_transaction import JOURNAL, LOCK, STAGING
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.release_lock import release_lock
from lovspor.forarbeider.review_models import ReviewStatus
from lovspor.regjeringen_fetch.models import FetchRun, evidence_file_name
from tests.unit.cli_output import said
from tests.unit.forarbeider_apply_support import (
    CONFIRMED,
    DOC1,
    DOC2,
    NOW,
    PATH1,
    PATH2,
    QUOTE,
    TABLE,
    ApplyBench,
    outside_dataset,
    without_records,
)
from tests.unit.forarbeider_batch_support import BRANCH, CHAPTER_MARKER, PLAN_AT, RUN_AT
from tests.unit.forarbeider_checkout_support import commit_all, git
from tests.unit.forarbeider_policy_support import PolicyDesk
from tests.unit.forarbeider_release_support import at_a_terminal  # noqa: F401

pytestmark = pytest.mark.usefixtures("at_a_terminal")
WRITTEN = sorted([PATH1, PATH2, "forarbeider/INDEX.md", MANIFEST_PATH])


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("LOVVERK_CORPUS_PATH", raising=False)


@pytest.fixture
def bench(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ApplyBench:
    return ApplyBench(tmp_path, monkeypatch)


def _refused(result: object, reason: str, detail: str, code: int = 1) -> None:
    output = getattr(result, "output", "")
    assert getattr(result, "exit_code", None) == code, output
    assert said(f"refused: {reason}: {detail}", output), output
    assert "BATCH APPLIED" not in output


def _untouched(bench: ApplyBench) -> None:
    """Nothing changed in the clone: status clean, no forarbeider change pending."""
    assert bench.status() == []
    for leftover in (LOCK, JOURNAL, STAGING):
        assert not (bench.clone / leftover).exists(), leftover


# --- the pass --------------------------------------------------------------------------------


def test_a_confirmed_batch_writes_only_the_dataset_and_commits_nothing(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    head = git(bench.clone, "rev-parse", "HEAD").strip()
    before = outside_dataset(bench.root, bench.clone)

    result = bench.apply(sha, request)

    assert result.exit_code == 0, result.output
    assert said(f"BATCH APPLIED plan {sha}", result.output)
    assert said("not committed: no git add, commit or push ran", result.output)
    assert said("identity_authenticated=false", result.output)
    assert bench.status() == WRITTEN
    assert git(bench.clone, "rev-parse", "HEAD").strip() == head
    assert git(bench.clone, "diff", "--cached", "--name-only") == ""
    after = outside_dataset(bench.root, bench.clone)
    assert without_records(after, bench.batch.workspace) == before
    for leftover in (LOCK, JOURNAL, STAGING):
        assert not (bench.clone / leftover).exists()


def test_the_phrase_names_both_digests_the_counts_and_the_branch(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    result = bench.apply(sha, request)

    (phrase,) = bench.prompts
    binding = phrase.split()[3]
    assert phrase == f"apply {sha[:12]} binding {binding} documents 2 chapters 4 to {BRANCH}"
    assert said(f"APPLY PREFLIGHT PASS plan {sha} binding {binding}", result.output)
    (stored,) = bench.records("batch_confirmations")
    confirmation = json.loads(stored.read_text())
    assert confirmation["phrase"] == phrase
    assert confirmation["binding_sha256"].startswith(binding)
    assert confirmation["documents"] == [DOC1, DOC2]
    assert confirmation["chapters"] == 4
    assert confirmation["target_branch"] == BRANCH
    assert confirmation["identity_authenticated"] is False
    assert confirmation["decided_at"] == "2100-04-10T12:30:00Z"
    assert (confirmation["git_add"], confirmation["commits"], confirmation["pushes"]) == (
        False,
        False,
        False,
    )
    assert stored.name == f"{confirmation['result_manifest_sha256']}.json"


def test_the_manifest_records_each_clearance_and_every_label(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    bench.apply(bench.planned(request), request)

    manifest = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())

    assert manifest.schema_version == 2
    first, second = manifest.documents
    assert [item.clearance.cleared_by for item in first.chapters] == ["standing_policy"] * 2
    assert [item.clearance.cleared_by for item in second.chapters] == [
        "standing_policy",
        "owner_review",
    ]
    for member in manifest.documents:
        assert (member.normative, member.source_license) == (False, None)
        assert member.rights_basis == "åndsverkloven § 14"
        assert member.times.owner_decided_at.isoformat() == "2100-04-10T12:30:00+00:00"
    text = (bench.clone / PATH1).read_text()
    assert "NOT enacted law" in text
    assert "normative: false" in text


def test_only_included_chapters_are_written(bench: ApplyBench, httpx_mock: HTTPXMock) -> None:
    bench.batch.add(1, extras={3: TABLE})
    bench.batch.add(2, extras={3: QUOTE})
    bench.batch.ready(httpx_mock)
    request = bench.batch.request([bench.batch.document(1), bench.batch.document(2)])
    sha = bench.planned(request)

    result = bench.apply(sha, request)

    assert result.exit_code == 0, result.output
    manifest = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())
    for member, desk in zip(
        manifest.documents, (bench.batch.desks[1], bench.batch.desks[2]), strict=True
    ):
        assert [item.chapter_number for item in member.chapters] == [2]
        assert member.coverage == "partial_curated_excerpt"
        rendered = (bench.clone / member.path).read_text()
        assert desk.results[3].source_page_url not in rendered
        assert "Sitat" not in rendered


def test_a_second_run_after_the_write_or_after_the_owners_commit_is_unchanged(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    assert bench.apply(sha, request).exit_code == 0
    written = {path: (bench.clone / path).read_bytes() for path in WRITTEN}

    again = bench.apply(sha, request)
    commit_all(bench.clone, "owner: release batch")
    committed = bench.apply(sha, request)

    for result in (again, committed):
        assert result.exit_code == 0, result.output
        assert said(f"UNCHANGED plan {sha}", result.output)
    assert len(bench.prompts) == 1, "a retry asks no phrase and writes nothing"
    assert {path: (bench.clone / path).read_bytes() for path in WRITTEN} == written
    assert len(bench.records("batch_confirmations")) == len(bench.records("batch_releases")) == 1


# --- the owner's confirmation ----------------------------------------------------------------


def test_a_wrong_phrase_writes_nothing_and_stores_no_confirmation(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    bench.typed = lambda shown: shown.replace("chapters 4", "chapters 5")

    _refused(bench.apply(sha, request), "review_not_interactive", "batch_confirmation")
    _untouched(bench)
    assert bench.records("batch_confirmations") == []


def test_no_terminal_no_apply(
    bench: ApplyBench, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lovspor.forarbeider import release_commands  # noqa: PLC0415

    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    monkeypatch.setattr(release_commands, "_interactive", lambda: False)

    _refused(bench.apply(sha, request), "review_not_interactive", "batch_confirmation")
    _untouched(bench)


def test_a_moment_after_the_clock_or_before_the_policy_is_refused(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    _refused(
        bench.apply(sha, request, decided="2100-04-10T13:00:01Z"), "invalid_timestamp", "decided"
    )
    _refused(
        bench.apply(sha, request, decided="2100-04-03T00:00:00Z"), "release_invalid", "decided"
    )
    _untouched(bench)
    assert bench.prompts == []


def test_a_head_moved_while_the_owner_typed_is_a_confirmation_mismatch(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    def commit_then_type(shown: str) -> str:
        git(bench.clone, "commit", "--quiet", "--allow-empty", "-m", "moved under the owner")
        return shown

    bench.typed = commit_then_type

    _refused(bench.apply(sha, request), "confirmation_mismatch", "binding", code=2)
    _untouched(bench)


def test_a_policy_revoked_while_the_owner_typed_is_not_reproduced(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    policy = PolicyDesk(bench.root / "revoker")
    policy.workspace, policy.ledger = bench.batch.workspace, bench.batch.ledger

    def revoke_then_type(shown: str) -> str:
        assert policy.revoke(bench.batch.policy_sha, "2100-04-10T12:40:00Z").exit_code == 0
        return shown

    bench.typed = revoke_then_type

    _refused(bench.apply(sha, request), "plan_not_reproduced", f"{DOC1}-ch2: policy_revoked", 2)
    _untouched(bench)


# --- the world changed since the plan: nothing is asked, nothing written ---------------------


@pytest.mark.parametrize("change", ["page", "veto", "stale_proof", "weeks_later"])
def test_a_changed_input_is_refused_before_the_phrase(
    bench: ApplyBench, httpx_mock: HTTPXMock, change: str
) -> None:
    from datetime import timedelta  # noqa: PLC0415

    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    if change == "page":
        page = bench.batch.desks[1].pages[2]
        page.write_bytes(page.read_bytes() + b"\n")
        expected = "inputs"
    elif change == "veto":
        bench.batch.desks[1].decide(2, ReviewStatus.HELD, at="2100-04-10T12:10:00Z")
        expected = "inputs"
    elif change == "stale_proof":
        bench.at(RUN_AT + timedelta(hours=24, minutes=1))
        expected = f"{DOC1}-ch2: source_proof_stale"
    else:
        from datetime import UTC, datetime  # noqa: PLC0415

        bench.at(datetime(2100, 5, 1, 1, 0, tzinfo=UTC))
        expected = f"{DOC1}-ch2: source_proof_stale"

    _refused(bench.apply(sha, request), "plan_not_reproduced", expected, code=2)
    _untouched(bench)
    assert bench.prompts == []


def test_a_wrong_branch_an_unknown_plan_or_another_request_is_refused(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    git(bench.clone, "switch", "--quiet", "main")
    _refused(bench.apply(sha, request), "checkout_branch_mismatch", "branch")
    git(bench.clone, "switch", "--quiet", BRANCH)
    other_branch = bench.apply(sha, request, branch="forarbeider/other")
    _refused(other_branch, "target_branch_mismatch", "branch")
    _refused(bench.apply("0" * 64, request), "release_record_missing", "0" * 64)
    other = bench.batch.request(json.loads(request.read_text())["documents"], "forarbeider/x")
    _refused(bench.apply(sha, other), "target_branch_mismatch", "request")
    _untouched(bench)
    assert bench.prompts == []


def test_a_plan_whose_document_is_already_released_is_a_conflicting_release(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    bench.batch.add(1)
    bench.batch.ready(httpx_mock)
    first = bench.batch.request([bench.batch.document(1)])
    assert bench.apply(bench.planned(first), first).exit_code == 0
    commit_all(bench.clone, "owner: first batch")
    again = bench.planned(first, at="2100-04-10T12:20:00Z")

    _refused(bench.apply(again, first), "release_conflict", DOC1)
    _untouched(bench)


# --- the dataset as found --------------------------------------------------------------------


def test_a_symlink_hard_link_or_stray_file_in_the_dataset_is_refused(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    import os  # noqa: PLC0415

    bench.batch.add(1)
    bench.batch.add(2)
    bench.batch.ready(httpx_mock)
    first = bench.batch.request([bench.batch.document(1)])
    assert bench.apply(bench.planned(first), first).exit_code == 0
    commit_all(bench.clone, "owner: first batch")
    second = bench.batch.request([bench.batch.document(2)])
    sha = bench.planned(second, at="2100-04-10T12:20:00Z")
    member = bench.clone / PATH1
    original = member.read_bytes()

    member.unlink()
    member.symlink_to(bench.root / "elsewhere.md")
    commit_all(bench.clone, "symlink")
    _refused(bench.apply(sha, second), "unsafe_input_path", "forarbeider")
    git(bench.clone, "reset", "--quiet", "--hard", "HEAD~1")
    os.link(member, bench.root / "outside-link.md")
    _refused(bench.apply(sha, second), "unsafe_input_path", "forarbeider")
    (bench.root / "outside-link.md").unlink()
    assert member.read_bytes() == original
    assert bench.prompts == [bench.prompts[0]]


# --- concurrency and the lock ----------------------------------------------------------------


def test_a_second_apply_is_refused_while_one_holds_the_lock(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)

    with release_lock(bench.clone):
        _refused(bench.apply(sha, request), "corpus_locked", "checkout")
        _refused(bench.recover(), "corpus_locked", "checkout")
        assert (
            bench.invoke(
                [
                    "preflight",
                    sha,
                    "--checkout",
                    str(bench.clone),
                    "--request",
                    str(request),
                    "--source-proof",
                    str(bench.batch.run_file),
                ]
            ).exit_code
            == 1
        )

    assert bench.status() == []
    assert bench.prompts == []


def test_the_preflight_refuses_a_clone_while_an_apply_holds_it(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    seen: list[str] = []

    def preflight_then_type(shown: str) -> str:
        result = bench.invoke(
            [
                "preflight",
                sha,
                "--checkout",
                str(bench.clone),
                "--request",
                str(request),
                "--source-proof",
                str(bench.batch.run_file),
            ]
        )
        seen.append(result.output)
        return shown

    bench.typed = preflight_then_type

    assert bench.apply(sha, request).exit_code == 0
    assert said("refused: checkout_pending_change: lock", seen[0])


def test_the_plan_moment_is_not_the_confirmation_moment(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request, at=PLAN_AT)

    assert bench.apply(sha, request, decided=CONFIRMED).exit_code == 0
    stored = json.loads(bench.records("batch_releases")[0].read_text())
    assert stored["written_to"] == "working_tree_only"
    assert stored["publication"] == "owner_merge_of_lovverk_pr"
    assert CHAPTER_MARKER not in json.dumps(stored)


# --- boundaries ------------------------------------------------------------------------------


def test_twenty_five_documents_apply_as_one_batch(bench: ApplyBench, httpx_mock: HTTPXMock) -> None:
    from lovspor.forarbeider.batch_models import MAX_DOCUMENTS  # noqa: PLC0415

    for number in range(1, MAX_DOCUMENTS + 1):
        bench.batch.add(number)
    bench.batch.ready(httpx_mock)
    documents = [bench.batch.document(number) for number in range(1, MAX_DOCUMENTS + 1)]
    request = bench.batch.request(documents)
    sha = bench.planned(request)

    result = bench.apply(sha, request)

    assert result.exit_code == 0, result.output
    assert bench.prompts[0].endswith(f"documents {MAX_DOCUMENTS} chapters 50 to {BRANCH}")
    manifest = parse_any_manifest((bench.clone / MANIFEST_PATH).read_bytes())
    assert len(manifest.documents) == MAX_DOCUMENTS
    assert len(bench.status()) == MAX_DOCUMENTS + 2


def test_a_malformed_base_manifest_is_refused_before_the_phrase(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    (bench.clone / "forarbeider").mkdir()
    (bench.clone / MANIFEST_PATH).write_text('{"schema_version": 2, "documents": [}')
    (bench.clone / "forarbeider" / "INDEX.md").write_text("# Forarbeider\n")
    commit_all(bench.clone, "malformed")

    _refused(bench.apply(sha, request), "manifest_invalid", "manifest")
    assert bench.prompts == []
    assert bench.status() == []


def test_an_included_chapter_the_fresh_evaluation_has_no_result_for_is_refused(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    """B1b regression: a fresh evaluation that lost a chapter never renders the stale plan's."""
    request = bench.mixed(httpx_mock)
    sha = bench.planned(request)
    proof = bench.batch.run_file
    assert proof is not None
    ask = ApplyPreflightRequest(plan=sha, checkout=bench.clone, request=request, source_proof=proof)
    verdict = apply_preflight(bench.scope(), ask, NOW)
    first = verdict.batch.documents[0]
    lost = first._replace(inputs=first.inputs._replace(observed=first.inputs.observed[1:]))
    batch = verdict.batch._replace(documents=(lost, *verdict.batch.documents[1:]))

    with pytest.raises(ForarbeiderRefusedError) as caught:
        releases_of(verdict._replace(batch=batch))
    assert (caught.value.reason.value, caught.value.detail) == (
        "plan_not_reproduced",
        f"{DOC1}-ch2: outcome_missing",
    )
    with pytest.raises(ForarbeiderRefusedError) as caught:
        releases_of(verdict._replace(batch=batch._replace(documents=verdict.batch.documents[1:])))
    assert caught.value.detail == f"{DOC1}: outcome_missing"


def test_a_policy_window_that_ended_since_the_plan_is_not_reproduced(
    bench: ApplyBench, httpx_mock: HTTPXMock
) -> None:
    bench.batch.add(1)
    run_file = bench.batch.fetch(httpx_mock, bench.batch.pages())
    run = FetchRun.model_validate_json(run_file.read_bytes())
    policy = PolicyDesk(bench.root / "short-policy")
    policy.workspace, policy.ledger = bench.batch.workspace, bench.batch.ledger
    policy.evidence["robots_txt"] = run_file.parent / str(evidence_file_name(run.robots))
    assert run.terms is not None
    policy.evidence["terms_page"] = run_file.parent / str(evidence_file_name(run.terms))
    bench.batch.policy_sha = policy.signed(valid="2100-04-04T12:00:00Z/2100-04-10T12:45:00Z")
    request = bench.batch.request([bench.batch.document(1)])
    sha = bench.planned(request)

    result = bench.apply(sha, request)

    _refused(result, "plan_not_reproduced", f"{DOC1}-ch2: policy_expired", code=2)
    _untouched(bench)
    assert bench.prompts == []
