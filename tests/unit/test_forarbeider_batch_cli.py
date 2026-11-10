"""``forarbeider-release batch plan`` / ``batch show``, end to end (#738, A2b).

Every state is reached through supported commands: staging files, the PR3A
review, dossier, authorization and preflight, the standing policy signed at a
terminal, and a real fetch run made offline through ``pytest-httpx``. The
planner itself makes no request, writes only ``batch_plans/<sha>.json`` in
the private workspace, and prints codes, ids, digests and public source URLs:
never chapter text, evidence, handles or a private path.
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from lovspor.forarbeider import release_cli
from lovspor.forarbeider.batch_models import MAX_DOCUMENTS
from lovspor.forarbeider.release_models import digest
from lovspor.forarbeider.review_models import ReviewStatus
from lovspor.regjeringen_fetch.models import FetchRun, evidence_file_name
from tests.unit.cli_output import option_help, said
from tests.unit.forarbeider_batch_support import (
    CHAPTER_MARKER,
    BatchDesk,
    chapters_of,
)
from tests.unit.forarbeider_policy_support import PolicyDesk
from tests.unit.forarbeider_release_support import (
    PRIVATE_MARKER,
    REVIEWER,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

QUOTE = "<blockquote><p>Sitat.</p></blockquote>"
TABLE = "<table><tr><td>x</td></tr></table>"
PACKAGE = Path(release_cli.__file__).parent
BATCH_MODULES = ("batch_classify.py", "batch_models.py", "batch_clearance.py", "batch_commands.py")


@pytest.fixture
def batch(tmp_path: Path) -> BatchDesk:
    return BatchDesk(tmp_path)


def _mixed(batch: BatchDesk, httpx_mock: HTTPXMock) -> Path:
    """Doc 1: two clean chapters. Doc 2: clean 2, flagged 3 with an eligible report, ready."""
    batch.add(1)
    batch.add(2, extras={3: QUOTE})
    review = batch.flagged_report(2, (3,))
    batch.ready(httpx_mock)
    return batch.request([batch.document(1), batch.document(2, review)])


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _refused(result: Any, reason: str, detail: str) -> None:
    assert result.exit_code == 1, result.output
    assert said(f"refused: {reason}: {detail}", result.output)


def test_a_mixed_batch_clears_each_chapter_by_its_one_path(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    result = batch.plan(_mixed(batch, httpx_mock))

    assert result.exit_code == 0, result.output
    sha = said_digest(result.output, "BATCH PLAN")
    plan = batch.stored(sha)
    chapters = chapters_of(plan)
    assert {key: item["clearance"]["cleared_by"] for key, item in chapters.items()} == {
        "regjeringen-9000101-ch2": "standing_policy",
        "regjeringen-9000101-ch3": "standing_policy",
        "regjeringen-9000102-ch2": "standing_policy",
        "regjeringen-9000102-ch3": "owner_review",
    }
    assert plan["counts"] == {
        "documents": 2,
        "chapters": 4,
        "standing_policy": 3,
        "owner_review": 1,
        "excluded": 0,
    }
    assert plan["publication_authorized"] is False
    assert plan["applies"] is False
    assert plan["owner_confirmation"] == "required_not_given"
    assert plan["source_proof_sha256"] == digest(batch.run_file.read_bytes())  # type: ignore[union-attr]
    assert said("publication_authorized=false applies=false", result.output)
    assert said("owner_confirmation=required_not_given", result.output)
    assert said("chapter 3 regjeringen-9000102-ch3 flagged included owner_review", result.output)
    assert said("sample regjeringen-9000101-ch2", result.output) or said(
        "sample regjeringen-9000101-ch3", result.output
    )


def test_show_prints_the_stored_plan_and_refuses_an_edited_one(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    planned = batch.plan(_mixed(batch, httpx_mock))
    sha = said_digest(planned.output, "BATCH PLAN")

    shown = batch.show(sha)
    path = batch.workspace / "batch_plans" / f"{sha}.json"
    path.write_text(path.read_text().replace('"applies": false', '"applies": true'))
    edited = batch.show(sha)

    assert shown.exit_code == 0, shown.output
    assert shown.output == planned.output
    _refused(edited, "release_record_corrupt", sha)
    _refused(batch.show("0" * 64), "release_record_missing", "0" * 64)
    _refused(batch.show("not-a-digest"), "release_invalid", "digest")


def test_the_same_request_gives_the_same_plan_and_stores_it_once(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    request = _mixed(batch, httpx_mock)
    entries = json.loads(request.read_text())["documents"]

    first = batch.plan(request)
    second = batch.plan(request)
    permuted = batch.plan(batch.request(entries[::-1]))

    sha = said_digest(first.output, "BATCH PLAN")
    assert said_digest(second.output, "BATCH PLAN") == sha
    assert said_digest(permuted.output, "BATCH PLAN") == sha
    assert first.output == second.output
    assert sorted(path.name for path in (batch.workspace / "batch_plans").iterdir()) == [
        f"{sha}.json"
    ]


def test_another_moment_is_another_plan(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    request = _mixed(batch, httpx_mock)

    first = said_digest(batch.plan(request).output, "BATCH PLAN")
    later = said_digest(batch.plan(request, at="2100-04-10T12:00:01Z").output, "BATCH PLAN")

    assert first != later
    assert batch.stored(first)["inputs_sha256"] != batch.stored(later)["inputs_sha256"]


def test_nothing_included_writes_the_plan_and_exits_two(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)

    result = batch.plan(batch.request([batch.document(1)]), at="2100-04-11T12:00:00Z")

    assert result.exit_code == 2, result.output
    plan = batch.stored(said_digest(result.output, "BATCH PLAN"))
    assert {tuple(item["reasons"]) for item in chapters_of(plan).values()} == {
        ("source_proof_stale",)
    }
    assert said("excluded source_proof_stale", result.output)


def test_a_page_the_proof_run_did_not_fetch_is_excluded_by_name(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    desk = batch.add(1)
    batch.ready(httpx_mock, skip=[desk.results[3].source_page_url])

    result = batch.plan(batch.request([batch.document(1)]))

    chapters = chapters_of(batch.stored(said_digest(result.output, "BATCH PLAN")))
    assert chapters["regjeringen-9000101-ch3"]["reasons"] == ["page_not_fetched"]
    assert chapters["regjeringen-9000101-ch2"]["included"] is True


def test_a_revoked_policy_clears_nothing_but_the_review_path_still_does(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    request = _mixed(batch, httpx_mock)
    policy = PolicyDesk(batch.root / "revoker")
    policy.workspace, policy.ledger = batch.workspace, batch.ledger
    assert policy.revoke(batch.policy_sha, "2100-04-09T12:00:00Z").exit_code == 0

    result = batch.plan(request)

    chapters = chapters_of(batch.stored(said_digest(result.output, "BATCH PLAN")))
    assert chapters["regjeringen-9000101-ch2"]["reasons"] == ["policy_revoked"]
    assert chapters["regjeringen-9000102-ch3"]["clearance"]["cleared_by"] == "owner_review"
    assert result.exit_code == 0


def test_withheld_and_flagged_chapters_without_a_report_are_excluded(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1, extras={2: TABLE, 3: QUOTE})
    batch.ready(httpx_mock)

    result = batch.plan(batch.request([batch.document(1)]))

    assert result.exit_code == 2, result.output
    chapters = chapters_of(batch.stored(said_digest(result.output, "BATCH PLAN")))
    assert chapters["regjeringen-9000101-ch2"]["reasons"] == ["withheld"]
    assert chapters["regjeringen-9000101-ch3"]["reasons"] == ["preflight_missing"]
    assert said("chapter 2 regjeringen-9000101-ch2 withheld excluded withheld", result.output)


def _deny_report(desk: Any) -> str:
    result = desk.preflight(None)
    assert result.exit_code == 2, result.output
    return said_digest(result.output, "report")


def test_a_chapter_excluded_for_several_reasons_prints_them_comma_joined(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    desk = batch.add(1, extras={3: QUOTE})
    desk.decide(3, ReviewStatus.APPROVED)
    report = _deny_report(desk)
    batch.ready(httpx_mock)

    result = batch.plan(batch.request([batch.document(1, (report, []))]))

    assert result.exit_code == 0, result.output
    chapters = chapters_of(batch.stored(said_digest(result.output, "BATCH PLAN")))
    reasons = chapters["regjeringen-9000101-ch3"]["reasons"]
    assert len(reasons) > 1
    assert said(
        f"chapter 3 regjeringen-9000101-ch3 flagged excluded {','.join(reasons)}", result.output
    )


def test_twenty_five_documents_plan_and_twenty_six_are_refused_unread(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    for number in range(1, MAX_DOCUMENTS + 2):
        batch.add(number)
    batch.ready(httpx_mock)
    documents = [batch.document(number) for number in range(1, MAX_DOCUMENTS + 2)]

    allowed = batch.plan(batch.request(documents[:MAX_DOCUMENTS]))
    before = _tree(batch.root)
    refused = batch.plan(batch.request(documents))

    assert allowed.exit_code == 0, allowed.output
    assert said(f"documents {MAX_DOCUMENTS} included 50", allowed.output)
    _refused(refused, "batch_too_large", "request")
    after = _tree(batch.root)
    assert {name for name in after if name not in before} == {"private/batch-2.json"}


def test_an_empty_batch_is_refused(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    batch.add(1)
    batch.ready(httpx_mock)

    _refused(batch.plan(batch.request([])), "batch_empty", "request")


def test_the_same_document_twice_is_refused(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    batch.add(1)
    batch.ready(httpx_mock)
    request = batch.request([batch.document(1), batch.document(1)])

    _refused(batch.plan(request), "document_duplicate", "regjeringen-9000101")
    assert not (batch.workspace / "batch_plans").exists()


def test_a_document_repeated_after_another_one_is_still_refused(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.add(2)
    batch.ready(httpx_mock)
    request = batch.request([batch.document(1), batch.document(2), batch.document(1)])

    _refused(batch.plan(request), "document_duplicate", "regjeringen-9000101")
    assert not (batch.workspace / "batch_plans").exists()


def test_a_malformed_policy_digest_is_refused_by_name_on_stderr(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)

    result = batch.plan(batch.request([batch.document(1)]), policy="not-a-digest")

    _refused(result, "release_invalid", "policy")
    assert said("refused: release_invalid: policy", result.stderr)


def test_a_malformed_at_moment_is_refused_by_name_on_stderr(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)

    result = batch.plan(batch.request([batch.document(1)]), at="not-a-time")

    _refused(result, "invalid_timestamp", "at")
    assert said("refused: invalid_timestamp: at", result.stderr)


def test_the_same_chapter_twice_is_refused(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    batch.add(1)
    batch.ready(httpx_mock)
    entry = batch.document(1)
    entry["observations"] = entry["observations"] + entry["observations"][:1]

    _refused(batch.plan(batch.request([entry])), "chapter_duplicate", "observation")


def test_a_chapter_of_another_document_is_refused(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    batch.add(1)
    batch.add(2)
    batch.ready(httpx_mock)
    mixed = batch.document(1)
    mixed["observations"] = [*mixed["observations"], batch.document(2)["observations"][0]]

    _refused(batch.plan(batch.request([mixed])), "chapter_foreign", "regjeringen-9000102-ch2")


@pytest.mark.parametrize(
    "branch", ["main", "master", "HEAD", "refs/heads/x", "forarbeider/", "forarbeider/A b"]
)
def test_a_target_branch_outside_the_grammar_is_refused(
    batch: BatchDesk, httpx_mock: HTTPXMock, branch: str
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)

    _refused(
        batch.plan(batch.request([batch.document(1)], branch=branch)),
        "target_branch_invalid",
        "target_branch",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("extractor_version", 2), ("normalizer_version", 2), ("id", "regjeringen-9000101-ch9")],
)
def test_a_result_of_another_code_version_or_identity_is_refused(
    batch: BatchDesk, httpx_mock: HTTPXMock, field: str, value: Any
) -> None:
    desk = batch.add(1)
    batch.ready(httpx_mock)
    path = desk.observations[2]
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))

    _refused(batch.plan(batch.request([batch.document(1)])), "schema_invalid", "observation")


def test_a_malformed_request_is_refused_without_its_path(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)
    request = batch.request([batch.document(1)], schema_version=2)

    result = batch.plan(request)

    _refused(result, "schema_invalid", "request")
    assert str(batch.root) not in result.output


def test_an_unknown_policy_or_report_is_refused(batch: BatchDesk, httpx_mock: HTTPXMock) -> None:
    batch.add(1, extras={3: QUOTE})
    report, _ = batch.flagged_report(1, (3,))
    batch.ready(httpx_mock)

    no_policy = batch.plan(batch.request([batch.document(1)]), policy="9" * 64)
    no_report = batch.plan(batch.request([batch.document(1, ("8" * 64, []))]))
    bad_checkpoint = batch.plan(batch.request([batch.document(1, (report, ["x@1:y"]))]))

    _refused(no_policy, "release_record_missing", "9" * 64)
    _refused(no_report, "release_record_missing", "8" * 64)
    _refused(bad_checkpoint, "release_invalid", "checkpoint")


def test_a_missing_or_edited_source_proof_is_refused(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    batch.add(1)
    batch.ready(httpx_mock)
    request = batch.request([batch.document(1)])
    assert batch.run_file is not None

    missing = batch.plan(request, run=batch.run_file.parent / "absent.json")
    run = FetchRun.model_validate_json(batch.run_file.read_bytes())
    stored = batch.run_file.parent / str(evidence_file_name(run.robots))
    stored.write_bytes(b"User-agent: *\nDisallow:\n")
    edited = batch.plan(request)

    _refused(missing, "input_unreadable", "source_proof")
    assert edited.exit_code == 1
    assert said("refused: release_record_corrupt: source_proof", edited.output)


def test_planning_writes_only_its_plan_and_never_the_corpus(
    batch: BatchDesk, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cache = tmp_path / "xdg"
    corpus = cache / "lovverk"
    (corpus / ".git").mkdir(parents=True)
    (corpus / "manifest.json").write_text('{"documents": []}\n')
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    request = _mixed(batch, httpx_mock)
    before = _tree(tmp_path)

    result = batch.plan(request)

    after = _tree(tmp_path)
    sha = said_digest(result.output, "BATCH PLAN")
    assert {name for name in after if before.get(name) != after[name]} == {
        f"workspace/batch_plans/{sha}.json"
    }
    assert set(before) <= set(after)
    assert (batch.workspace / "batch_plans" / f"{sha}.json").stat().st_mode & 0o077 == 0


def test_the_batch_modules_hold_no_process_network_or_corpus_write() -> None:
    forbidden = {"subprocess", "httpx", "socket", "requests", "lovspor.regjeringen_fetch.fetch"}
    for name in BATCH_MODULES:
        tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not imported & forbidden, name
        assert not {"corpus_transaction", "corpus_plan", "write_leaf"} & {
            item.rsplit(".", 1)[-1] for item in imported
        }, name


def test_no_chapter_text_evidence_handle_or_private_path_is_printed_or_stored(
    batch: BatchDesk, httpx_mock: HTTPXMock
) -> None:
    result = batch.plan(_mixed(batch, httpx_mock))
    sha = said_digest(result.output, "BATCH PLAN")
    shown = batch.show(sha)
    stored = (batch.workspace / "batch_plans" / f"{sha}.json").read_text()

    for text in (result.output, shown.output, stored):
        for secret in (CHAPTER_MARKER, PRIVATE_MARKER, REVIEWER, str(batch.root), "/private/"):
            assert secret not in text


def test_the_commands_state_their_options_and_that_they_authorize_nothing() -> None:
    app = release_cli.release_app

    assert "sha256" in (option_help(app, ["batch", "plan"], "--policy") or "")
    assert "fetch run" in (option_help(app, ["batch", "plan"], "--source-proof") or "")
    assert "absolute" in (option_help(app, ["batch", "plan"], "--request") or "")
    assert "ISO 8601" in (option_help(app, ["batch", "plan"], "--at") or "")
    assert "authorize nothing" in (release_cli.batch_app.info.help or "")
