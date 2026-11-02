"""Edges of the PR3B-1 preflight: time bounds, parsers, coverage and the private store."""

from datetime import datetime
from pathlib import Path

import pytest

from lovspor.forarbeider import release_commands, release_store
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.page import build_record
from lovspor.forarbeider.release_commands import (
    ChapterScope,
    ReleaseScope,
    Run,
    actor,
    checkpoints,
    interval,
    preflight_inputs,
    revoke_impl,
    show_impl,
)
from lovspor.forarbeider.release_models import ReleaseDossier, record_text
from lovspor.forarbeider.release_preflight import _coverage, evaluate
from lovspor.forarbeider.release_store import (
    checked_file,
    read_file,
    read_named,
    read_record,
    write_record,
)
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    AT,
    DECIDED,
    DOSSIER_AT,
    RETRIEVED,
    Desk,
    at_a_terminal,  # noqa: F401
    page,
    said_digest,
)

KEY = "a" * 64
HEAD = "b" * 64


def _refusal(action: object, *arguments: object) -> RefusalReason:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        action(*arguments)  # type: ignore[operator]
    return caught.value.reason


def test_an_actor_is_a_handle_and_an_aware_moment() -> None:
    handle, moment = actor("owner-b@2100-04-04T14:00:00+02:00")
    assert handle == "owner-b"
    assert moment == datetime.fromisoformat("2100-04-04T12:00:00+00:00")
    assert _refusal(actor, "owner-b") is RefusalReason.RELEASE_INVALID
    assert _refusal(actor, "@2100-04-04T12:00:00Z") is RefusalReason.RELEASE_INVALID
    assert _refusal(actor, "owner-b@2100-04-04T12:00:00") is RefusalReason.INVALID_TIMESTAMP


def test_an_interval_has_two_aware_ends() -> None:
    start, end = interval("2100-04-04T12:00:00Z/2100-05-01T00:00:00Z")
    assert (start.day, end.month) == (4, 5)
    assert _refusal(interval, "2100-04-04T12:00:00Z") is RefusalReason.RELEASE_INVALID
    assert _refusal(interval, "2100-04-04T12:00:00Z/later") is RefusalReason.INVALID_TIMESTAMP


def test_checkpoints_are_pins_once_each() -> None:
    assert checkpoints([f"{KEY}@12:{HEAD}"]) == {KEY: (12, HEAD)}
    assert checkpoints([]) == {}
    for bad in ([f"{KEY}@0:{HEAD}"], [f"{KEY}@1:{HEAD[:-1]}"], [f"{KEY}@1:{HEAD}"] * 2):
        assert _refusal(checkpoints, bad) is RefusalReason.RELEASE_INVALID


def test_a_relative_or_symlinked_input_path_is_refused(tmp_path: Path) -> None:
    assert _refusal(checked_file, Path("relative.txt"), ()) is RefusalReason.UNSAFE_INPUT_PATH
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    linked = tmp_path / "link" / "file.txt"
    assert _refusal(checked_file, linked, ()) is RefusalReason.UNSAFE_INPUT_PATH
    dotted = tmp_path / "real" / ".." / "real" / "file.txt"
    assert _refusal(checked_file, dotted, ()) is RefusalReason.UNSAFE_INPUT_PATH
    assert _refusal(checked_file, real / "x", (tmp_path,)) is RefusalReason.UNSAFE_OUTPUT_PATH
    assert checked_file(real / "x", ()) == real / "x"


def test_a_missing_file_reads_as_none_and_a_present_one_whole(tmp_path: Path) -> None:
    assert read_file(tmp_path / "absent", (), 10) is None
    path = tmp_path / "present"
    path.write_bytes(b"0123456789")
    path.chmod(0o600)
    assert read_file(path, (), 10) == b"0123456789"
    assert _refusal(read_file, path, (), 9) is RefusalReason.INPUT_TOO_LARGE


def test_records_are_named_by_their_bytes_and_never_replaced(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    dossier = said_digest(desk.dossier().output, "dossier")
    workspace = desk.workspace
    stored = read_record(workspace, "dossiers", dossier, ReleaseDossier)
    assert stored is not None
    assert write_record(workspace, "dossiers", stored) == dossier
    assert read_record(workspace, "dossiers", "c" * 64, ReleaseDossier) is None
    assert read_named(workspace, "reports", dossier, ReleaseDossier) is None
    (workspace / "dossiers" / f"{dossier}.json").rename(workspace / "dossiers" / "renamed.json")
    moved = read_named(workspace, "dossiers", "renamed", ReleaseDossier)
    assert moved == stored
    path = workspace / "dossiers" / f"{'d' * 64}.json"
    path.write_text(record_text(stored))
    path.chmod(0o600)
    assert _refusal(read_record, workspace, "dossiers", "d" * 64, ReleaseDossier) is (
        RefusalReason.RELEASE_RECORD_CORRUPT
    )


def test_a_record_not_in_its_written_form_is_corrupt(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    dossier = said_digest(desk.dossier().output, "dossier")
    path = desk.workspace / "dossiers" / f"{dossier}.json"
    path.write_text(path.read_text().rstrip("\n"))
    reason = _refusal(read_named, desk.workspace, "dossiers", dossier, ReleaseDossier)
    assert reason is RefusalReason.RELEASE_RECORD_CORRUPT
    path.write_text("{}")
    reason = _refusal(read_named, desk.workspace, "dossiers", dossier, ReleaseDossier)
    assert reason is RefusalReason.RELEASE_RECORD_CORRUPT


def test_a_workspace_others_can_reach_is_refused(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.workspace.mkdir(mode=0o755)
    desk.workspace.chmod(0o755)
    result = desk.dossier()
    assert said("refused: review_ledger_unsafe", result.output)


def _scope(desk: Desk) -> ChapterScope:
    release = ReleaseScope(workspace=desk.workspace, ledger=desk.ledger)
    observations = tuple(desk.observations[number] for number in (2, 3))
    pages = tuple(desk.pages[number] for number in (2, 3))
    return ChapterScope(
        release=release, metadata=desk.metadata, observations=observations, pages=pages
    )


@pytest.mark.parametrize("numbers", [(), (2,), (2, 3), (3, 2), (2, 2)])
def test_approval_pins_preserve_every_staged_result(
    tmp_path: Path, numbers: tuple[int, ...]
) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    scope = _scope(desk)
    staged = release_commands._staged(scope)
    staged = staged.model_copy(update={"results": tuple(desk.results[n] for n in numbers)})

    pins = release_commands._approved_pins(scope, staged)

    assert tuple(pin.chapter.chapter_number for pin in pins) == numbers
    assert tuple(pin.chapter.text_sha256 for pin in pins) == tuple(
        desk.results[n].text_sha256 for n in numbers
    )


def test_approval_pins_refuse_a_bodyless_result_instead_of_omitting_it(tmp_path: Path) -> None:
    desk = Desk(tmp_path, extras={3: '<img src="bilde.png">'})
    scope = _scope(desk)
    staged = release_commands._staged(scope)
    assert staged.results[1].text_sha256 is None

    with pytest.raises(ForarbeiderRefusedError) as caught:
        release_commands._approved_pins(scope, staged)

    assert caught.value.reason is RefusalReason.RELEASE_NOT_ELIGIBLE


@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize(
    ("at", "reasons"),
    [
        (DECIDED, set()),
        ("2100-04-04T11:59:59Z", {"authorization_not_yet_valid", "evaluated_before_decision"}),
        ("2100-04-30T23:59:59Z", set()),
        ("2100-05-01T00:00:00Z", {"authorization_expired"}),
    ],
)
def test_validity_starts_at_its_first_moment_and_ends_before_its_last(
    tmp_path: Path, at: str, reasons: set[str]
) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=tuple(desk.all_checkpoints()), at=at)
    report = evaluate(preflight_inputs(_scope(desk), run))
    assert set(report.document_reasons) == reasons


@pytest.mark.usefixtures("at_a_terminal")
def test_a_preflight_before_the_dossier_was_reviewed_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=(), at="2100-04-03T11:59:59Z")
    report = evaluate(preflight_inputs(_scope(desk), run))
    assert "evaluated_before_decision" in report.document_reasons
    later = Run(authorization=authorization, checkpoints=(), at=DOSSIER_AT)
    assert (
        "evaluated_before_decision"
        in evaluate(preflight_inputs(_scope(desk), later)).document_reasons
    )


@pytest.mark.usefixtures("at_a_terminal")
def test_every_listed_chapter_is_still_not_a_complete_proposition(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=tuple(desk.all_checkpoints()), at=AT)
    inputs = preflight_inputs(_scope(desk), run)
    listed = inputs.metadata.chapters
    narrowed = inputs.metadata.model_copy(update={"chapters": listed[:3]})
    report = evaluate(inputs._replace(metadata=narrowed, metadata_page=None))
    assert report.outcome == "deny"
    payload, url = page("l", 2)
    rebuilt = build_record(payload, url, datetime.fromisoformat(RETRIEVED))
    assert evaluate(inputs._replace(metadata=rebuilt)).coverage == "partial_curated_excerpt"
    two = rebuilt.model_copy(update={"chapters": rebuilt.chapters[:2]})
    assert _coverage(two, (1, 2), True) == "every_listed_chapter"
    assert _coverage(two, (2,), True) == "partial_curated_excerpt"
    assert _coverage(two, (1, 2), False) == "none"
    assert evaluate(inputs).asserts_complete_proposition is False


@pytest.mark.usefixtures("at_a_terminal")
def test_a_metadata_record_its_page_does_not_reproduce_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=tuple(desk.all_checkpoints()), at=AT)
    inputs = preflight_inputs(_scope(desk), run)
    renamed = inputs.metadata.model_copy(update={"title": "Annen tittel"})
    assert evaluate(inputs._replace(metadata=renamed)).document_reasons == (
        "metadata_not_reproducible",
    )
    garbage = evaluate(inputs._replace(metadata_page=b"<html></html>"))
    assert garbage.document_reasons == ("metadata_not_reproducible",)


@pytest.mark.usefixtures("at_a_terminal")
def test_an_unparseable_page_does_not_reproduce_its_chapter(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=tuple(desk.all_checkpoints()), at=AT)
    inputs = preflight_inputs(_scope(desk), run)
    broken = tuple(item._replace(page=b"<html></html>") for item in inputs.observed)
    report = evaluate(inputs._replace(observed=broken))
    assert all("observation_not_reproducible" in item.reasons for item in report.chapters)


@pytest.mark.usefixtures("at_a_terminal")
def test_an_authorization_for_another_dossier_or_document_is_denied(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    _, authorization = desk.released()
    run = Run(authorization=authorization, checkpoints=tuple(desk.all_checkpoints()), at=AT)
    inputs = preflight_inputs(_scope(desk), run)
    assert inputs.dossier is not None and inputs.authorization is not None
    moved = inputs.dossier._replace(sha256="f" * 64)
    assert "dossier_missing" in evaluate(inputs._replace(dossier=moved)).document_reasons
    other = inputs.authorization.record.document.model_copy(update={"session": "2098-2099"})
    foreign = inputs.authorization.record.model_copy(update={"document": other})
    stored = inputs.authorization._replace(record=foreign)
    reasons = evaluate(inputs._replace(authorization=stored)).document_reasons
    assert "authorization_document_mismatch" in reasons
    record = inputs.dossier.record.model_copy(update={"document": other})
    reasons = evaluate(
        inputs._replace(dossier=inputs.dossier._replace(record=record))
    ).document_reasons
    assert "dossier_document_mismatch" in reasons
    purpose = inputs.authorization.record.model_copy(update={"purpose_sha256": "f" * 64})
    stored = inputs.authorization._replace(record=purpose)
    assert evaluate(inputs._replace(authorization=stored)).document_reasons == (
        "purpose_evidence_missing",
    )


@pytest.mark.usefixtures("at_a_terminal")
def test_the_confirmation_is_asked_in_process(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier().output, "dossier")
    result = desk.authorize(dossier)
    assert said(f"Type to confirm: authorize regjeringen-9000001 {dossier[:12]}", result.output)
    wrong = desk.invoke(
        [
            *desk.scope(),
            "authorize",
            "--dossier",
            dossier,
            "--decided",
            f"owner-b@{DECIDED}",
            "--valid",
            "2100-04-04T12:00:00Z/2100-06-01T00:00:00Z",
        ],
        "authorize something else\n",
    )
    assert said("refused: review_not_interactive: authorization", wrong.output)


@pytest.mark.usefixtures("at_a_terminal")
def test_authorization_needs_intact_purpose_evidence(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier = said_digest(desk.dossier().output, "dossier")
    desk.evidence["publication_purpose"].write_bytes(b"changed")
    result = desk.authorize(dossier)
    assert said("refused: release_not_eligible: publication_purpose", result.output)
    missing = desk.authorize("c" * 64)
    assert said("refused: release_record_missing", missing.output)
    malformed = desk.authorize("C" * 64)
    assert said("refused: release_invalid: dossier", malformed.output)


def test_show_and_revoke_refuse_what_is_not_there(tmp_path: Path) -> None:
    scope = ReleaseScope(workspace=tmp_path / "workspace", ledger=tmp_path / "ledger")
    assert _refusal(show_impl, scope, "manifest", "a" * 64) is RefusalReason.RELEASE_INVALID
    assert _refusal(show_impl, scope, "dossier", "a" * 64) is RefusalReason.RELEASE_RECORD_MISSING
    assert _refusal(show_impl, scope, "report", "nothex") is RefusalReason.RELEASE_INVALID
    revoked = _refusal(revoke_impl, scope, "a" * 64, f"owner-b@{DECIDED}")
    assert revoked is RefusalReason.RELEASE_RECORD_MISSING


@pytest.mark.usefixtures("at_a_terminal")
def test_show_states_digests_and_kinds_only(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.approve_all()
    dossier, authorization = desk.released()
    scope = ReleaseScope(workspace=desk.workspace, ledger=desk.ledger)
    shown = show_impl(scope, "dossier", dossier)
    assert shown.splitlines()[0] == f"dossier {dossier} regjeringen-9000001"
    assert "evidence publication_purpose" in shown
    assert f"chapter regjeringen-9000001-ch2 text {desk.results[2].text_sha256}" in shown
    stated = show_impl(scope, "authorization", authorization)
    assert "destination forarbeider/2099-2100/prop-l/9000001.md" in stated
    assert f"dossier {dossier}" in stated
    assert stated.endswith("not revoked; identity_authenticated=false grants_publication=false")


def test_an_evidence_option_names_a_known_kind(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    path = desk.evidence["source_rights"]
    for option in (f"licence@2={path}", f"source_rights@0={path}", f"source_rights@2={path.name}"):
        result = desk.dossier(["--evidence", option])
        assert said("refused: release_invalid: evidence", result.output), option
    empty = tmp_path / "evidence" / "empty.txt"
    empty.touch(mode=0o600)
    result = desk.dossier(["--evidence", f"source_rights@2,3={empty}"])
    assert said("refused: input_unreadable: evidence", result.output)


def test_the_store_bounds_match_the_staging_bounds() -> None:
    assert release_store.MAX_STAGED_BYTES == 64 * 1024 * 1024
    assert release_store.MAX_EVIDENCE_BYTES == 16 * 1024 * 1024
    assert release_commands.MAX_HTML_BYTES == 16 * 1024 * 1024


def test_a_record_file_is_owner_only(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    dossier = said_digest(desk.dossier().output, "dossier")
    path = desk.workspace / "dossiers" / f"{dossier}.json"
    assert path.stat().st_mode & 0o077 == 0
    assert desk.workspace.stat().st_mode & 0o077 == 0
