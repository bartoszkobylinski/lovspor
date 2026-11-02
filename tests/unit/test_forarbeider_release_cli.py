"""The PR3B-1 release preflight through its supported CLI (#738, ADR-0018).

Every test reaches its state through ``lovspor ops forarbeider-release`` or the
PR3A ledger API, never by writing a record by hand: a state an operator cannot
reach is not shipped (CLAUDE.md).
"""

import json
import subprocess
from pathlib import Path

import pytest

from lovspor.forarbeider import release_commands
from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    AT,
    OWNER,
    PRIVATE_MARKER,
    REVIEWER,
    Desk,
    at_a_terminal,  # noqa: F401
    chapter_reasons,
    page,
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")
ROOT = Path(__file__).parents[2]


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    staged = Desk(tmp_path)
    staged.approve_all()
    return staged


def _git_state() -> str:
    command = ["git", "status", "--porcelain", "--untracked-files=all"]
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=True).stdout


@pytest.mark.parametrize("kind", ["l", "ls"])
def test_a_complete_release_is_a_preview_that_authorizes_nothing(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path, kind)
    desk.approve_all()
    before = _git_state()
    _, authorization = desk.released()
    result = desk.preflight(authorization, desk.all_checkpoints())
    assert result.exit_code == 0, result.output
    assert said("ELIGIBLE_PREVIEW", result.output)
    report = desk.report(result.output)
    assert report["publication_authorized"] is False
    assert report["writes_corpus"] is False
    assert report["asserts_complete_proposition"] is False
    assert report["legal_sufficiency_decided_by_code"] is False
    assert report["checkpoint_basis"] == "operator_supplied_unauthenticated"
    assert report["planned_chapters"] == [2, 3]
    assert report["listed_chapters"] == [1, 2, 3, 4]
    assert report["coverage"] == "partial_curated_excerpt"
    number = desk.results[2].source_document_id
    expected = f"forarbeider/2099-2100/prop-{kind}/{number}.md"
    assert report["proposed_destination"] == expected
    assert _git_state() == before


def test_four_adr_0018_attestations_alone_are_denied(desk: Desk) -> None:
    result = desk.preflight(None, desk.all_checkpoints())
    assert result.exit_code == 2, result.output
    report = desk.report(result.output)
    assert report["outcome"] == "deny"
    assert set(report["document_reasons"]) >= {"authorization_missing", "dossier_missing"}
    for number in (2, 3):
        outcome = next(item for item in report["chapters"] if item["chapter_number"] == number)
        assert outcome["eligible"] is False
        assert outcome["missing_evidence"] == [
            "publication_purpose",
            "privacy",
            "third_party_screening",
            "source_rights",
            "source_completeness",
        ]
    assert report["proposed_destination"] is None
    assert report["coverage"] == "none"


@pytest.mark.parametrize("reference", ["adr:0018", "issue:738", "pr:754", "sha256:" + "a" * 64])
def test_a_policy_pointer_or_bare_hash_is_not_evidence(desk: Desk, reference: str) -> None:
    result = desk.dossier(["--evidence", f"source_rights@2,3={reference}"])
    assert result.exit_code == 1
    assert said("refused: release_invalid: evidence", result.output)
    assert not (desk.workspace / "dossiers").exists()


def test_missing_dossier_denies(desk: Desk) -> None:
    dossier, authorization = desk.released()
    (desk.workspace / "dossiers" / f"{dossier}.json").unlink()
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert "dossier_missing" in report["document_reasons"]
    assert "purpose_evidence_missing" in report["document_reasons"]
    assert report["dossier_sha256"] is None


def test_missing_checkpoint_denies(desk: Desk) -> None:
    _, authorization = desk.released()
    result = desk.preflight(authorization, [desk.checkpoint(2)])
    assert result.exit_code == 0, result.output
    report = desk.report(result.output)
    assert chapter_reasons(report, 2) == []
    assert chapter_reasons(report, 3) == ["checkpoint_missing"]
    assert report["planned_chapters"] == [2]
    denied = desk.preflight(authorization, [])
    assert denied.exit_code == 2
    assert desk.report(denied.output)["outcome"] == "deny"


def test_a_cut_tail_is_found_by_the_external_checkpoint(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.decide(2, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    kept = desk.checkpoint(2)
    chain = sorted((desk.ledger).glob("*/000002.json"))
    assert len(chain) == 1
    chain[0].unlink()
    report = desk.report(desk.preflight(authorization, [kept, desk.checkpoint(3)]).output)
    assert chapter_reasons(report, 2) == ["checkpoint_mismatch"]


def test_an_edited_only_record_is_found_by_its_pins(desk: Desk) -> None:
    _, authorization = desk.released()
    kept = desk.all_checkpoints()
    record = desk.ledger / kept[0].split("@")[0] / "000001.json"
    record.write_bytes(record.read_bytes().replace(REVIEWER.encode(), b"reviewer-z"))
    report = desk.report(desk.preflight(authorization, kept).output)
    assert chapter_reasons(report, 2) == ["checkpoint_mismatch", "review_superseded"]


def test_an_edited_earlier_record_breaks_the_chain(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.decide(2, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-06T12:00:00Z")
    kept = desk.all_checkpoints()
    record = desk.ledger / kept[0].split("@")[0] / "000001.json"
    record.write_bytes(record.read_bytes().replace(REVIEWER.encode(), b"reviewer-z"))
    report = desk.report(desk.preflight(authorization, kept).output)
    assert chapter_reasons(report, 2) == ["review_ledger_corrupt"]
    assert report["planned_chapters"] == [3]


def test_an_approval_superseded_after_authorization_denies(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.decide(2, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert chapter_reasons(report, 2) == ["review_not_approved", "review_superseded"]
    assert report["planned_chapters"] == [3]
    assert report["outcome"] == "eligible_preview"
    assert report["coverage"] == "partial_curated_excerpt"


def test_a_reapproval_after_authorization_is_still_superseded(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.decide(2, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    desk.decide(2, ReviewStatus.APPROVED, "2100-04-06T12:00:00Z")
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert chapter_reasons(report, 2) == ["authorized_before_approval", "review_superseded"]


def test_changed_chapter_text_has_no_approval_and_no_binding(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.stage(2, "<p>Endret avsnitt.</p>", name="ch2-new")
    desk.decide(3, ReviewStatus.HELD, "2100-04-05T12:00:00Z")
    report = desk.report(desk.preflight(authorization, [desk.checkpoint(3)]).output)
    assert chapter_reasons(report, 2) == [
        "authorization_chapter_mismatch",
        "dossier_chapter_mismatch",
        "review_missing",
    ]
    assert report["document_reasons"] == ["metadata_page_missing"]
    assert report["outcome"] == "deny"


def test_changed_page_bytes_lose_their_source(desk: Desk) -> None:
    _, authorization = desk.released()
    altered = desk.pages[2].read_bytes() + b"\n"
    desk.pages[2].chmod(0o600)
    desk.pages[2].write_bytes(altered)
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert "source_page_missing" in chapter_reasons(report, 2)


def test_an_edited_observation_is_not_reproduced_by_its_page(desk: Desk) -> None:
    _, authorization = desk.released()
    payload = json.loads(desk.observations[3].read_text())
    payload["chapter_title"] = "Annen tittel"
    desk.observations[3].write_text(json.dumps(payload))
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    reasons = chapter_reasons(report, 3)
    assert "observation_not_reproducible" in reasons
    assert "chapter_not_in_inventory" in reasons


def test_a_chapter_without_its_page_is_denied(desk: Desk) -> None:
    _, authorization = desk.released()
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints(), pages=False).output)
    assert "metadata_page_missing" in report["document_reasons"]
    assert "source_page_missing" in chapter_reasons(report, 3)


def test_an_unobserved_authorized_chapter_is_denied(desk: Desk) -> None:
    _, authorization = desk.released()
    result = desk.preflight(authorization, desk.all_checkpoints(), chapters=[2])
    report = desk.report(result.output)
    assert chapter_reasons(report, 3) == ["observation_missing"]
    assert report["planned_chapters"] == [2]


def test_a_chapter_outside_the_authorization_is_denied(desk: Desk) -> None:
    _, authorization = desk.released(chapters=[2])
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert chapter_reasons(report, 3) == [
        "dossier_chapter_missing",
        "evidence_incomplete",
        "not_authorized",
        "purpose_not_authorized",
    ]
    assert report["planned_chapters"] == [2]


def test_a_foreign_chapter_is_denied(tmp_path: Path, desk: Desk) -> None:
    _, authorization = desk.released()
    other = Desk(tmp_path / "other", "ls")
    desk.observations[3] = other.observations[3]
    desk.pages[3] = other.pages[3]
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()[:1]).output)
    assert "chapter_foreign" in report["document_reasons"]
    assert report["outcome"] == "deny"


def test_a_duplicate_chapter_is_denied(desk: Desk) -> None:
    _, authorization = desk.released()
    arguments = [*desk.scope(), "--observation", str(desk.observations[2])]
    index = arguments.index("chapters") + 1
    duplicated = [*arguments[:index], *arguments[index:]]
    result = desk.invoke([*duplicated, "preflight", "--at", AT, "--authorization", authorization])
    assert result.exit_code == 2, result.output
    assert "chapter_duplicate" in desk.report(result.output)["document_reasons"]


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ('<div id="ved1"><p>Vedlegg.</p></div>', "annex"),
        ("<table><tr><td>Celle</td></tr></table>", "table"),
        ('<img src="bilde.png">', "image"),
    ],
)
def test_a_withheld_body_cannot_enter_a_release(tmp_path: Path, extra: str, reason: str) -> None:
    desk = Desk(tmp_path, extras={3: extra})
    desk.decide(2, ReviewStatus.APPROVED)
    refused = desk.dossier()
    assert refused.exit_code == 1
    assert said("refused: release_not_eligible: regjeringen-9000001-ch3", refused.output)
    report = desk.report(desk.preflight(None, [desk.checkpoint(2)]).output)
    outcome = next(item for item in report["chapters"] if item["chapter_number"] == 3)
    assert "body_withheld" in outcome["reasons"]
    assert reason in outcome["hold_reasons"]
    assert outcome["text_sha256"] is None


@pytest.mark.parametrize(
    ("at", "reason"),
    [
        ("2100-05-01T00:00:00Z", "authorization_expired"),
        ("2100-04-04T11:59:59Z", "authorization_not_yet_valid"),
    ],
)
def test_an_authorization_outside_its_window_denies(desk: Desk, at: str, reason: str) -> None:
    _, authorization = desk.released()
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints(), at=at).output)
    assert reason in report["document_reasons"]


def test_a_revoked_authorization_denies(desk: Desk) -> None:
    _, authorization = desk.released()
    head = ["ops", "forarbeider-release", "--workspace", str(desk.workspace)]
    head += ["--ledger", str(desk.ledger)]
    revoke = [*head, "revoke", "--authorization", authorization]
    result = desk.invoke([*revoke, "--decided", f"{OWNER}@2100-04-05T00:00:00Z"])
    assert result.exit_code == 0, result.output
    again = desk.invoke([*revoke, "--decided", f"{OWNER}@2100-04-06T00:00:00Z"])
    assert said("refused: release_already_revoked", again.output)
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert "authorization_revoked" in report["document_reasons"]
    shown = desk.invoke([*head, "show", "authorization", authorization])
    assert said("revoked; identity_authenticated=false", shown.output)


def test_an_unknown_authorization_denies(desk: Desk) -> None:
    report = desk.report(desk.preflight("e" * 64, desk.all_checkpoints()).output)
    assert "authorization_missing" in report["document_reasons"]


def test_a_changed_evidence_file_denies_its_chapters(desk: Desk) -> None:
    _, authorization = desk.released()
    desk.evidence["third_party_screening"].write_bytes(b"replaced")
    desk.evidence["source_rights"].unlink()
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    outcome = next(item for item in report["chapters"] if item["chapter_number"] == 2)
    assert outcome["missing_evidence"] == ["third_party_screening", "source_rights"]
    assert set(outcome["reasons"]) == {
        "evidence_digest_mismatch",
        "evidence_incomplete",
        "evidence_unavailable",
    }


def test_evidence_must_be_bound_to_the_chapter(desk: Desk) -> None:
    options: list[str] = []
    for kind, path in desk.evidence.items():
        chapters = "2,3" if kind == "publication_purpose" else "2"
        options += ["--evidence", f"{kind}@{chapters}={path}"]
    dossier = said_digest(desk.dossier(options).output, "dossier")
    authorization = said_digest(desk.authorize(dossier).output, "authorization")
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    outcome = next(item for item in report["chapters"] if item["chapter_number"] == 3)
    assert outcome["missing_evidence"] == [
        "privacy",
        "third_party_screening",
        "source_rights",
        "source_completeness",
    ]
    assert report["planned_chapters"] == [2]


def test_a_no_personal_data_finding_cannot_stand_against_a_flagged_chapter(
    tmp_path: Path,
) -> None:
    desk = Desk(tmp_path, extras={3: "<p>Skriv til postmottak@example.invalid.</p>"})
    desk.approve_all()
    finding = desk.write_evidence("privacy_no_personal_data")
    options = [item for item in desk.evidence_options() if "privacy_processing" not in item]
    options = [item for item in options if item != "--evidence"]
    flags = [part for item in options for part in ("--evidence", item)]
    flags += ["--evidence", f"privacy_no_personal_data@2,3={finding}"]
    dossier = said_digest(desk.dossier(flags).output, "dossier")
    authorization = said_digest(desk.authorize(dossier).output, "authorization")
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert chapter_reasons(report, 3) == ["privacy_finding_contradicted"]
    assert chapter_reasons(report, 2) == []


def test_the_same_inputs_give_a_byte_identical_report(tmp_path: Path, desk: Desk) -> None:
    _, authorization = desk.released()
    first = desk.preflight(authorization, desk.all_checkpoints())
    path = desk.workspace / "reports" / f"{said_digest(first.output, 'report')}.json"
    before = path.read_bytes()
    second = desk.preflight(authorization, desk.all_checkpoints())
    assert second.output == first.output
    assert path.read_bytes() == before
    assert len(list((desk.workspace / "reports").iterdir())) == 1


def test_nothing_private_reaches_output_or_reports(tmp_path: Path) -> None:
    desk = Desk(tmp_path, extras={2: f"<p>{PRIVATE_MARKER}</p>"})
    desk.approve_all()
    dossier, authorization = desk.released()
    results = [desk.preflight(authorization, desk.all_checkpoints())]
    head = ["ops", "forarbeider-release", "--workspace", str(desk.workspace)]
    head += ["--ledger", str(desk.ledger)]
    results += [desk.invoke([*head, "show", "dossier", dossier])]
    results += [desk.invoke([*head, "show", "authorization", authorization])]
    sha = said_digest(results[0].output, "report")
    results += [desk.invoke([*head, "show", "report", sha])]
    printed = "".join(result.output for result in results)
    written = "".join(path.read_text() for path in (desk.workspace / "reports").iterdir())
    for secret in (PRIVATE_MARKER, REVIEWER, OWNER, str(tmp_path), "Bakgrunn"):
        assert secret not in printed, secret
        assert secret not in written, secret


def test_authorization_needs_a_person_at_a_terminal(
    desk: Desk, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(release_commands, "_interactive", lambda: False)
    dossier = said_digest(desk.dossier().output, "dossier")
    result = desk.authorize(dossier)
    assert result.exit_code == 1
    assert said("refused: review_not_interactive: authorization", result.output)
    assert not (desk.workspace / "authorizations").exists()


def test_authorization_needs_a_current_approval(tmp_path: Path) -> None:
    desk = Desk(tmp_path)
    desk.decide(2, ReviewStatus.APPROVED)
    desk.decide(3, ReviewStatus.HELD)
    dossier = said_digest(desk.dossier().output, "dossier")
    result = desk.authorize(dossier)
    assert said("refused: review_not_eligible: regjeringen-9000001-ch3", result.output)


def test_authorization_of_a_stale_dossier_is_refused(desk: Desk) -> None:
    dossier = said_digest(desk.dossier(chapters=[2]).output, "dossier")
    result = desk.authorize(dossier)
    assert said(f"refused: release_stale: {dossier}", result.output)


def test_a_workspace_inside_a_checkout_is_refused(desk: Desk) -> None:
    desk.workspace = ROOT / "data" / "release-workspace"
    result = desk.dossier()
    assert result.exit_code == 1
    assert said("refused: unsafe_output_path", result.output)
    assert not desk.workspace.exists()


def test_symlinked_evidence_is_refused(desk: Desk, tmp_path: Path) -> None:
    link = tmp_path / "evidence" / "linked.txt"
    link.symlink_to(desk.evidence["source_rights"])
    result = desk.dossier(["--evidence", f"source_rights@2,3={link}"])
    assert said("refused: unsafe_input_path: evidence source_rights", result.output)
    assert str(tmp_path) not in result.output


def test_evidence_others_can_read_is_refused(desk: Desk) -> None:
    desk.evidence["source_rights"].chmod(0o644)
    result = desk.dossier()
    assert said("refused: observation_unsafe: evidence source_rights", result.output)
    assert str(desk.root) not in result.output


def test_oversized_evidence_is_refused_unread(desk: Desk, tmp_path: Path) -> None:
    large = tmp_path / "evidence" / "large.bin"
    with large.open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    large.chmod(0o600)
    result = desk.dossier(["--evidence", f"source_rights@2,3={large}"])
    assert said("refused: input_too_large: evidence source_rights", result.output)
    assert str(tmp_path) not in result.output


def test_an_edited_stored_record_is_refused(desk: Desk) -> None:
    _, authorization = desk.released()
    path = desk.workspace / "authorizations" / f"{authorization}.json"
    path.write_text(path.read_text().replace("2100-05-01", "2199-05-01"))
    result = desk.preflight(authorization, desk.all_checkpoints())
    assert result.exit_code == 1
    assert said("refused: release_record_corrupt", result.output)
    assert not (desk.workspace / "reports").exists()


def test_a_page_of_another_chapter_does_not_prove_this_one(desk: Desk) -> None:
    _, authorization = desk.released()
    swapped = desk.pages[3].read_bytes()
    payload, _ = page("l", 2)
    desk.pages[3].write_bytes(payload)
    desk.pages[2].write_bytes(swapped)
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert report["planned_chapters"] == [2, 3]
    desk.pages[2].write_bytes(payload)
    desk.pages[3].write_bytes(payload)
    report = desk.report(desk.preflight(authorization, desk.all_checkpoints()).output)
    assert chapter_reasons(report, 3) == ["source_page_missing"]
