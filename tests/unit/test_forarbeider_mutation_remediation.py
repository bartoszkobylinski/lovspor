"""Observable release record, refusal and preflight regression contracts."""

import io
import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.forarbeider import release_commands as commands
from lovspor.forarbeider.chapter import build_chapter_result
from lovspor.forarbeider.chapter_models import result_text
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.models import metadata_text
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.release_models import (
    PreflightReport,
    ReleaseAuthorization,
    ReleaseDossier,
    ReleaseRevocation,
    record_text,
)
from lovspor.forarbeider.release_preflight import evaluate
from lovspor.forarbeider.release_store import (
    checked_file,
    read_model,
    read_named,
    read_record,
    write_record,
)
from tests.unit.forarbeider_release_support import (
    AT,
    DECIDED,
    DOSSIER_AT,
    OWNER,
    VALID,
    Desk,
    moment,
    private,
    said_digest,
)
from tests.unit.test_forarbeider_release_models import (
    document,
    dossier,
    identity,
    outcome,
    report,
)


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def scope(desk: Desk) -> commands.ChapterScope:
    return commands.ChapterScope(
        release=commands.ReleaseScope(workspace=desk.workspace, ledger=desk.ledger),
        metadata=desk.metadata,
        observations=tuple(desk.observations.values()),
        pages=tuple(desk.pages.values()),
    )


def released(desk: Desk, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    desk.approve_all()
    sha = said_digest(desk.dossier().output, "dossier")
    monkeypatch.setattr(sys, "stdin", Terminal(f"authorize regjeringen-9000001 {sha[:12]}\n"))
    line = commands.authorize_impl(
        scope(desk), commands.Grant(dossier=sha, decided=f"{OWNER}@{DECIDED}", valid=VALID)
    )
    assert ": chapters 2,3 (" in line
    return sha, said_digest(line, "authorization")


def refused(action, arguments, expected):
    with pytest.raises(ForarbeiderRefusedError) as caught:
        action(*arguments)
    assert str(caught.value) == expected


@pytest.mark.parametrize("chapters", [(identity(3), identity(2)), (identity(2), identity(2))])
def test_chapter_order_diagnostic(chapters):
    with pytest.raises(ValidationError) as caught:
        ReleaseDossier(**dossier(chapters=chapters))
    assert caught.value.errors()[0]["msg"] == (
        "Value error, chapters must be in source order, once each"
    )


@pytest.mark.parametrize("field", ["id", "source_url"])
def test_foreign_document_diagnostic(field):
    fields = document().model_dump()
    fields[field] = fields[field].replace("9000001", "9000002")
    with pytest.raises(ValidationError) as caught:
        ReleaseDossier(**dossier(document=fields))
    assert caught.value.errors()[0]["msg"] == "Value error, a chapter belongs to another document"


def test_record_layout_is_canonical_utf8():
    record = ReleaseDossier(**dossier())
    expected = json.dumps(
        record.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False
    )
    assert record_text(record) == expected + "\n"


@pytest.mark.parametrize(
    ("parser", "text", "detail"),
    [
        (commands.actor, "owner", "release_invalid: actor"),
        (
            commands.actor,
            "owner@extra@2100-04-04T12:00:00Z",
            "invalid_timestamp: actor",
        ),
        (commands.interval, "today", "release_invalid: valid"),
        (commands.interval, "2100-04-04T12:00:00Z/bad/other", "invalid_timestamp: valid"),
        (commands.checkpoints, ["bad"], "release_invalid: checkpoint"),
    ],
)
def test_option_diagnostics(parser, text, detail):
    refused(parser, (text,), detail)


def test_invalid_model_reports_sorted_field_names_only():
    refused(
        commands.validated,
        (ReleaseRevocation, {}),
        "release_invalid: authorization_sha256,owner,record_type,revoked_at,schema_version",
    )


def test_store_refusals_identify_the_input(tmp_path):
    refused(checked_file, (Path("relative"), ()), "unsafe_input_path: relative")
    path = tmp_path / "missing.json"
    refused(read_model, (path, (), ReleaseDossier), f"input_unreadable: {path}")
    private(path, b"{}")
    refused(read_model, (path, (), ReleaseDossier), f"schema_invalid: {path}")
    workspace = tmp_path / "workspace"
    private(workspace / "dossiers" / "broken.json", b"{}")
    refused(
        read_named,
        (workspace, "dossiers", "broken", ReleaseDossier),
        "release_record_corrupt: broken",
    )
    record = ReleaseDossier(**dossier())
    private(workspace / "dossiers" / "layout.json", record_text(record).rstrip().encode())
    refused(
        read_named,
        (workspace, "dossiers", "layout", ReleaseDossier),
        "release_record_corrupt: layout",
    )
    wrong = "f" * 64
    private(workspace / "dossiers" / f"{wrong}.json", record_text(record).encode())
    refused(
        read_record,
        (workspace, "dossiers", wrong, ReleaseDossier),
        f"release_record_corrupt: {wrong}",
    )


def test_staged_refusals_identify_foreign_and_duplicate_chapters(tmp_path):
    desk = Desk(tmp_path / "local")
    other = Desk(tmp_path / "other", kind="ls")
    foreign = scope(desk).model_copy(update={"observations": (other.observations[2],)})
    refused(
        commands.dossier_impl,
        (foreign, (), f"owner@{AT}"),
        f"chapter_foreign: {other.results[2].id}",
    )
    duplicate = scope(desk).model_copy(update={"observations": (desk.observations[2],) * 2})
    refused(commands.dossier_impl, (duplicate, (), f"owner@{AT}"), "chapter_duplicate: observation")


def test_missing_records_and_invalid_options_have_specific_diagnostics(tmp_path):
    desk = Desk(tmp_path)
    chapter_scope = scope(desk)
    release = chapter_scope.release
    sha = "a" * 64
    refused(commands.show_impl, (release, "report", "bad"), "release_invalid: digest")
    refused(commands.show_impl, (release, "unknown", sha), "release_invalid: kind")
    refused(commands.show_impl, (release, "report", sha), f"release_record_missing: {sha}")
    refused(commands.revoke_impl, (release, "bad", f"owner@{AT}"), "release_invalid: authorization")
    refused(commands.revoke_impl, (release, sha, f"owner@{AT}"), f"release_record_missing: {sha}")
    refused(
        commands.authorize_impl,
        (chapter_scope, commands.Grant(dossier=sha, decided=f"owner@{AT}", valid=VALID)),
        f"release_record_missing: {sha}",
    )
    run = commands.Run(authorization="bad", checkpoints=(), at=AT)
    refused(commands.preflight_inputs, (chapter_scope, run), "release_invalid: authorization")
    desk.pages[2].unlink()
    refused(commands.preflight_inputs, (chapter_scope, run), "input_unreadable: page")


def test_cli_refusals_go_to_stderr(tmp_path):
    desk = Desk(tmp_path)
    desk.metadata.unlink()
    for result in (desk.dossier(), desk.preflight(None)):
        assert result.exit_code == 1
        assert result.stdout == ""
        assert result.stderr == "refused: input_unreadable: metadata\n"
    assert not (desk.workspace / "reports").exists()


def test_release_display_and_revocation(tmp_path, monkeypatch):
    desk = Desk(tmp_path)
    dossier_sha, authorization_sha = released(desk, monkeypatch)
    release = scope(desk).release
    dossier_record = read_record(desk.workspace, "dossiers", dossier_sha, ReleaseDossier)
    assert dossier_record is not None
    kinds = ",".join(sorted({item.kind.value for item in dossier_record.evidence}))
    assert desk.dossier().stdout == (
        f"dossier {dossier_sha} regjeringen-9000001: evidence {kinds} "
        "(a private record, not permission to publish)\n"
    )
    shown = commands.show_impl(release, "dossier", dossier_sha)
    for item in dossier_record.evidence:
        assert f"evidence {item.kind.value} {item.sha256} chapters 2,3" in shown.splitlines()
    record = read_record(desk.workspace, "authorizations", authorization_sha, ReleaseAuthorization)
    assert record is not None
    commands.revoke_impl(release, authorization_sha, f"{OWNER}@{AT}")
    shown = commands.show_impl(release, "authorization", authorization_sha)
    assert shown.splitlines() == [
        f"authorization {authorization_sha} {record.document.id} destination {record.destination}",
        (
            f"dossier {dossier_sha} purpose {record.purpose_sha256} "
            f"valid {record.valid_from.isoformat()}/{record.valid_until.isoformat()}"
        ),
        *(
            (
                f"chapter {item.chapter.chapter_id} review "
                f"{item.review.subject_key}@{item.review.revision}"
            )
            for item in record.chapters
        ),
        "revoked; identity_authenticated=false grants_publication=false",
    ]
    refused(
        commands.revoke_impl,
        (release, authorization_sha, f"{OWNER}@{AT}"),
        f"release_already_revoked: {authorization_sha}",
    )
    path = desk.workspace / "revocations" / f"{authorization_sha}.json"
    revocation = ReleaseRevocation.model_validate_json(path.read_bytes())
    path.write_text(record_text(revocation.model_copy(update={"authorization_sha256": "f" * 64})))
    refused(
        commands.show_impl,
        (release, "authorization", authorization_sha),
        f"release_record_corrupt: {authorization_sha}",
    )


def test_preflight_preserves_digests_and_blocks_otherwise_eligible_chapters(tmp_path, monkeypatch):
    desk = Desk(tmp_path)
    dossier_sha, authorization_sha = released(desk, monkeypatch)
    run = commands.Run(
        authorization=authorization_sha, checkpoints=tuple(desk.all_checkpoints()), at=AT
    )
    inputs = commands.preflight_inputs(scope(desk), run)
    good = evaluate(inputs)
    assert good.dossier_sha256 == dossier_sha
    assert good.authorization_sha256 == authorization_sha
    assert good.coverage == "partial_curated_excerpt"
    for item in good.chapters:
        assert item.source_sha256 == desk.results[item.chapter_number].source_sha256
        assert item.text_sha256 == desk.results[item.chapter_number].text_sha256
        assert item.eligible
    blocked = evaluate(inputs._replace(revoked=True))
    assert blocked.document_reasons == ("authorization_revoked",)
    assert all(not item.eligible and not item.reasons for item in blocked.chapters)
    assert blocked.planned_chapters == ()
    # The dossier's own boundary is observable without a later authorization decision masking it.
    boundary = evaluate(inputs._replace(authorization=None, at=moment(DOSSIER_AT)))
    assert "evaluated_before_decision" not in boundary.document_reasons
    sha = write_record(desk.workspace, "reports", good)
    expected = [
        (
            f"ELIGIBLE_PREVIEW regjeringen-9000001 report {sha} "
            "coverage partial_curated_excerpt publication_authorized=false"
        ),
        "document_reasons none",
        "chapter 2 regjeringen-9000001-ch2 eligible -",
        "chapter 3 regjeringen-9000001-ch3 eligible -",
    ]
    assert commands.show_impl(scope(desk).release, "report", sha).splitlines() == expected


def test_denied_report_prints_all_reasons_and_missing_gates():
    chapter = outcome(
        eligible=False,
        reasons=("review_missing", "source_page_missing"),
        missing_evidence=("source_rights",),
    )
    record = PreflightReport(
        **report(
            outcome="deny",
            coverage="none",
            planned_chapters=(),
            proposed_destination=None,
            document_reasons=("authorization_missing", "dossier_missing"),
            chapters=(chapter,),
        )
    )
    assert commands.report_lines(record, "a" * 64).splitlines() == [
        f"DENY regjeringen-9000001 report {'a' * 64} coverage none publication_authorized=false",
        "document_reasons authorization_missing,dossier_missing",
        (
            "chapter 2 regjeringen-9000001-ch2 deny "
            "review_missing,source_page_missing,missing:source_rights"
        ),
    ]


def test_all_listed_chapters_have_distinct_coverage(tmp_path, monkeypatch):
    desk = Desk(tmp_path)
    # A saved proposition whose inventory consists of its first chapter only.
    payload = desk.pages[2].read_bytes()
    payload = re.sub(rb'<li><a[^>]+\?ch=[234]"[^>]*>.*?</a></li>', b"", payload)
    payload = payload.replace(b'nav-item-current="2"', b'nav-item-current="1"')
    payload = payload.replace(b">2 Bakgrunn</h1>", b">1 Innledning</h1>")
    payload = payload.replace(b'?ch=1" data-longdoc-prev', b'" data-longdoc-prev')
    payload = re.sub(rb"<a[^>]+data-longdoc-next[^>]*>.*?</a>", b"", payload)
    url = desk.results[2].source_page_url.replace("?ch=2", "?ch=1")
    result = build_chapter_result(payload, url, moment("2100-04-01T12:00:00Z"))
    metadata = build_record(payload, url, result.retrieved_at)
    desk.pages = {1: private(tmp_path / "single.html", payload)}
    desk.observations = {1: private(tmp_path / "single.json", result_text(result).encode())}
    desk.results = {1: result}
    desk.metadata.write_text(metadata_text(metadata))
    desk.approve_all()
    dossier_sha = said_digest(desk.dossier(chapters=(1,)).output, "dossier")
    monkeypatch.setattr(
        sys, "stdin", Terminal(f"authorize regjeringen-9000001 {dossier_sha[:12]}\n")
    )
    line = commands.authorize_impl(
        scope(desk), commands.Grant(dossier=dossier_sha, decided=f"{OWNER}@{DECIDED}", valid=VALID)
    )
    run = commands.Run(
        authorization=said_digest(line, "authorization"),
        checkpoints=tuple(desk.all_checkpoints()),
        at=AT,
    )
    verdict = evaluate(commands.preflight_inputs(scope(desk), run))
    assert verdict.planned_chapters == (1,)
    assert verdict.coverage == "every_listed_chapter"
