"""The private real-document pilot audit, on synthetic Prop. L and Prop. LS (#738, ADR-0018 PR4).

``chapters pilot-audit`` is the preflight's own evaluation read per chapter as
PASS, HOLD or DENY. These tests drive it through the supported commands only,
on the bundled synthetic pages: no real document is read here.
"""

import re
from pathlib import Path
from typing import Any

import pytest

from lovspor.forarbeider.review_models import ReviewStatus
from tests.unit.cli_output import said
from tests.unit.forarbeider_release_support import (
    AT,
    OWNER,
    PRIVATE_MARKER,
    REVIEWER,
    Desk,
    at_a_terminal,  # noqa: F401
    said_digest,
)

pytestmark = pytest.mark.usefixtures("at_a_terminal")

KINDS = ("l", "ls")
DOCUMENTS = {"l": ("regjeringen-9000001", "prop-l"), "ls": ("regjeringen-9000003", "prop-ls")}
URL = {"l": "prop.-901-l-20992100/id9000001", "ls": "prop.-902-ls-20992100/id9000003"}
TABLE = "<table><tr><td>Synthetic cell</td></tr></table>"


def _audit(desk: Desk, authorization: str | None, checkpoints: list[str], **scope: Any) -> Any:
    options = ["--at", scope.pop("at", AT)]
    if authorization is not None:
        options += ["--authorization", authorization]
    for value in checkpoints:
        options += ["--checkpoint", value]
    return desk.invoke([*desk.scope(**scope), "pilot-audit", *options])


def _released(tmp_path: Path, kind: str, **desk: Any) -> tuple[Desk, str]:
    staged = Desk(tmp_path / kind, kind, **desk)
    staged.approve_all()
    return staged, staged.released()[1]


def _chapters(output: str) -> dict[int, list[str]]:
    lines = [line.split() for line in output.splitlines() if line.startswith("chapter ")]
    return {int(words[1]): words for words in lines}


def _verdicts(output: str) -> dict[int, str]:
    return {number: words[2] for number, words in _chapters(output).items()}


def _reasons(output: str, number: int) -> list[str]:
    words = _chapters(output)[number]
    return words[words.index("reasons") + 1].split(",")


@pytest.mark.parametrize("kind", KINDS)
def test_a_released_document_passes_every_chapter_and_still_authorizes_nothing(
    tmp_path: Path, kind: str
) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 0, result.output
    document, document_type = DOCUMENTS[kind]
    head = result.output.splitlines()[0].split()
    assert head[:4] == ["PILOT_AUDIT", document, document_type, "2099-2100"]
    assert head[-2:] == ["publication_authorized=false", "release_clearance=false"]
    assert _verdicts(result.output) == {2: "PASS", 3: "PASS"}
    assert "audited 2 pass 2 hold 0 deny 0" in result.output
    assert result.output.rstrip().endswith("PASS is not release clearance")


@pytest.mark.parametrize("kind", KINDS)
def test_every_chapter_line_names_its_page_url_and_digests(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, desk.all_checkpoints())

    for number, words in _chapters(result.output).items():
        observed = desk.results[number]
        assert words[words.index("source") + 1] == observed.source_sha256
        assert words[words.index("text") + 1] == observed.text_sha256
        url = f"https://www.regjeringen.no/no/dokumenter/{URL[kind]}/?ch={number}"
        assert words[words.index("url") + 1] == url
        assert words[words.index("reasons") + 1] == "-"


@pytest.mark.parametrize("kind", KINDS)
def test_the_report_it_states_is_the_private_preflight_report(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, desk.all_checkpoints())

    report = desk.report(result.output)
    assert report["publication_authorized"] is False
    assert report["outcome"] == "eligible_preview"
    listed = len(report["listed_chapters"])
    assert f"counts listed {listed} audited 2" in result.output
    assert f"coverage {report['coverage']}" in result.output


@pytest.mark.parametrize("kind", KINDS)
def test_nothing_given_yet_holds_every_chapter(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)

    result = _audit(desk, None, [])

    assert result.exit_code == 2, result.output
    assert _verdicts(result.output) == {2: "HOLD", 3: "HOLD"}
    assert "review_missing" in _reasons(result.output, 2)
    assert "authorization_missing" in result.output


@pytest.mark.parametrize("kind", KINDS)
def test_a_held_review_holds_its_chapter(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.decide(2, ReviewStatus.APPROVED)
    desk.decide(3, ReviewStatus.HELD)

    result = _audit(desk, None, [])

    assert result.exit_code == 2
    assert _verdicts(result.output) == {2: "HOLD", 3: "HOLD"}
    assert "review_not_approved" in _reasons(result.output, 3)
    assert "review_not_approved" not in _reasons(result.output, 2)


@pytest.mark.parametrize("kind", KINDS)
def test_a_hold_after_the_owner_decided_denies_as_superseded(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    desk.decide(3, ReviewStatus.HELD, at="2100-04-05T12:00:00Z")

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "PASS", 3: "DENY"}
    assert "review_superseded" in _reasons(result.output, 3)


@pytest.mark.parametrize("kind", KINDS)
def test_a_missing_checkpoint_holds(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, [desk.checkpoint(2)])

    assert _verdicts(result.output) == {2: "PASS", 3: "HOLD"}
    assert _reasons(result.output, 3) == ["checkpoint_missing"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_withheld_body_holds_and_states_no_text_digest(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind, extras={3: TABLE})
    desk.decide(2, ReviewStatus.APPROVED)

    result = _audit(desk, None, [])

    words = _chapters(result.output)[3]
    assert words[2] == "HOLD"
    assert words[words.index("text") + 1] == "-"
    assert "body_withheld" in _reasons(result.output, 3)


@pytest.mark.parametrize("kind", KINDS)
def test_a_partial_excerpt_passes_the_authorized_chapter_only(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    _, authorization = desk.released(chapters=(2,))

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "PASS", 3: "HOLD"}
    assert "not_authorized" in _reasons(result.output, 3)
    assert "coverage partial_curated_excerpt" in result.output


@pytest.mark.parametrize("kind", KINDS)
def test_source_drift_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    desk.pages[3].write_bytes(desk.pages[3].read_bytes().replace(b"Avsluttende", b"Endret"))

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "PASS", 3: "DENY"}
    assert _reasons(result.output, 3) == ["source_page_missing"]


@pytest.mark.parametrize("kind", KINDS)
def test_forged_evidence_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    desk.evidence["source_rights"].write_bytes(b"forged after the dossier was reviewed")

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert set(_verdicts(result.output).values()) == {"DENY"}
    assert "evidence_digest_mismatch" in _reasons(result.output, 2)


@pytest.mark.parametrize("kind", KINDS)
def test_a_revoked_grant_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    revoke = ["ops", "forarbeider-release", "--workspace", str(desk.workspace)]
    revoke += ["--ledger", str(desk.ledger), "revoke", "--authorization", authorization]
    assert desk.invoke([*revoke, "--decided", f"{OWNER}@2100-04-06T12:00:00Z"]).exit_code == 0

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert set(_verdicts(result.output).values()) == {"DENY"}
    assert "authorization_revoked" in result.output


@pytest.mark.parametrize("kind", KINDS)
def test_an_expired_grant_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, desk.all_checkpoints(), at="2100-06-01T12:00:00Z")

    assert set(_verdicts(result.output).values()) == {"DENY"}
    assert "authorization_expired" in result.output


@pytest.mark.parametrize("kind", KINDS)
def test_a_foreign_chapter_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    other = Desk(tmp_path / f"other-{kind}", "ls" if kind == "l" else "l")
    desk.observations[3] = other.observations[3]
    desk.pages[3] = other.pages[3]

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 2
    assert "DENY" in _verdicts(result.output).values()


@pytest.mark.parametrize("kind", KINDS)
def test_the_audit_prints_no_path_handle_evidence_or_chapter_text(
    tmp_path: Path, kind: str
) -> None:
    desk, authorization = _released(tmp_path, kind)
    desk.evidence["privacy_processing_basis"].write_bytes(b"x")

    for result in (
        _audit(desk, authorization, desk.all_checkpoints()),
        _audit(desk, None, []),
    ):
        output = result.output
        assert str(tmp_path) not in output
        assert PRIVATE_MARKER not in output
        assert REVIEWER not in output
        assert OWNER not in output
        assert "Avsluttende vurdering" not in output
        assert "Bakgrunn" not in output
        assert not re.search(r"\.(json|html|txt)\b", output)


@pytest.mark.parametrize("kind", KINDS)
def test_the_audit_writes_only_its_private_report(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    result = _audit(desk, authorization, desk.all_checkpoints())

    after = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    sha = said_digest(result.output, "report")
    assert set(after) - set(before) <= {desk.workspace / "reports" / f"{sha}.json"}
    assert {path: after[path] for path in before} == before


@pytest.mark.parametrize("kind", KINDS)
def test_the_same_inputs_audit_byte_identically(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    first = _audit(desk, authorization, desk.all_checkpoints())
    second = _audit(desk, authorization, desk.all_checkpoints())

    assert first.output == second.output


@pytest.mark.parametrize("kind", KINDS)
def test_privacy_or_rights_evidence_not_given_holds(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    options = []
    for gate, path in desk.evidence.items():
        bound = "2" if gate in {"privacy_processing_basis", "source_rights"} else "2,3"
        options += ["--evidence", f"{gate}@{bound}={path}"]
    dossier = said_digest(desk.dossier(options).output, "dossier")
    authorized = desk.authorize(dossier)
    assert authorized.exit_code == 0, authorized.output

    result = _audit(desk, said_digest(authorized.output, "authorization"), desk.all_checkpoints())

    assert _verdicts(result.output) == {2: "PASS", 3: "HOLD"}
    assert {"missing:privacy", "missing:source_rights"} <= set(_reasons(result.output, 3))


@pytest.mark.parametrize("kind", KINDS)
def test_metadata_of_another_document_denies(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    desk.metadata = Desk(tmp_path / "other", "ls" if kind == "l" else "l").metadata

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 2
    assert set(_verdicts(result.output).values()) == {"DENY"}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("role", ["page", "observation", "metadata"])
def test_a_symlinked_staged_input_is_refused_and_reports_nothing(
    tmp_path: Path, kind: str, role: str
) -> None:
    desk, authorization = _released(tmp_path, kind)
    target = {"page": desk.pages[3], "observation": desk.observations[3]}.get(role, desk.metadata)
    moved = target.with_name(f"moved-{target.name}")
    target.rename(moved)
    target.symlink_to(moved)
    before = (
        sorted((desk.workspace / "reports").glob("*"))
        if (desk.workspace / "reports").exists()
        else []
    )

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 1
    assert said(f"refused: unsafe_input_path: {role}", result.output)
    assert str(tmp_path) not in result.output
    after = (
        sorted((desk.workspace / "reports").glob("*"))
        if (desk.workspace / "reports").exists()
        else []
    )
    assert after == before


@pytest.mark.parametrize("kind", KINDS)
def test_a_workspace_linked_into_a_repository_is_refused(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)
    repository = tmp_path / "repository"
    (repository / ".git").mkdir(parents=True)
    desk.workspace.rename(repository / "workspace")
    desk.workspace.symlink_to(repository / "workspace")

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 1
    assert said("refused: unsafe_output_path: workspace", result.output)
    assert str(tmp_path) not in result.output
    assert not (repository / "workspace" / "reports").exists()


@pytest.mark.parametrize("kind", KINDS)
def test_an_authorized_chapter_not_supplied_holds(tmp_path: Path, kind: str) -> None:
    desk, authorization = _released(tmp_path, kind)

    result = _audit(desk, authorization, desk.all_checkpoints(), chapters=(2,))

    assert _verdicts(result.output)[3] == "HOLD"
    assert _reasons(result.output, 3) == ["observation_missing"]


@pytest.mark.parametrize("kind", KINDS)
def test_a_forged_stored_dossier_is_refused_and_reports_nothing(tmp_path: Path, kind: str) -> None:
    desk = Desk(tmp_path / kind, kind)
    desk.approve_all()
    dossier, authorization = desk.released()
    stored = desk.workspace / "dossiers" / f"{dossier}.json"
    stored.write_text(stored.read_text().replace(REVIEWER, "reviewer-z"))

    result = _audit(desk, authorization, desk.all_checkpoints())

    assert result.exit_code == 1
    assert said("refused: release_record_corrupt", result.output)
    assert not (desk.workspace / "reports").exists()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", ["pass", "hold", "deny"])
def test_audit_and_preflight_store_the_identical_report(
    tmp_path: Path, kind: str, state: str
) -> None:
    desk, authorization = _released(tmp_path, kind)
    checkpoints = desk.all_checkpoints()
    if state == "hold":
        checkpoints = [desk.checkpoint(2)]
    elif state == "deny":
        desk.evidence["source_rights"].write_bytes(b"changed evidence")
    preflight = desk.preflight(authorization, checkpoints)
    sha = said_digest(preflight.output, "report")
    path = desk.workspace / "reports" / f"{sha}.json"
    before = path.read_bytes()

    audit = _audit(desk, authorization, checkpoints)

    assert audit.exit_code == (0 if state == "pass" else 2), audit.output
    assert said_digest(audit.output, "report") == sha
    assert path.read_bytes() == before
    assert list((desk.workspace / "reports").glob("*.json")) == [path]


@pytest.mark.parametrize(
    ("at", "checkpoints", "reason"),
    [
        ("not-a-date", [], "invalid_timestamp"),
        ("2100-04-10T12:00:00", [], "invalid_timestamp"),
        (AT, ["malformed-checkpoint"], "release_invalid: checkpoint"),
    ],
)
def test_malformed_audit_options_are_refused_without_a_report(
    tmp_path: Path, at: str, checkpoints: list[str], reason: str
) -> None:
    desk = Desk(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    result = _audit(desk, None, checkpoints, at=at)

    assert result.exit_code == 1, result.output
    assert said(f"refused: {reason}", result.output)
    assert said(f"refused: {reason}", result.stderr)
    assert result.stdout == ""
    assert str(tmp_path) not in result.output
    assert not (desk.workspace / "reports").exists()
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before


def test_audit_requires_an_evaluation_time_before_writing(tmp_path: Path) -> None:
    desk = Desk(tmp_path)

    result = desk.invoke([*desk.scope(), "pilot-audit"])

    assert result.exit_code == 2, result.output
    assert said("Missing option '--at'", result.output)
    assert not (desk.workspace / "reports").exists()
