"""``lovspor ops forarbeider-review``: the one route to a decision (#738, ADR-0018 PR3A)."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.forarbeider import review_commands
from lovspor.forarbeider.chapter_models import ChapterResult, read_chapter
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.review_commands import (
    MAX_OBSERVATION_BYTES,
    ReviewScope,
    candidates_impl,
    read_observation,
    show_impl,
    verify_impl,
)
from lovspor.forarbeider.review_ledger import load_history
from lovspor.forarbeider.review_models import AttestationKind, subject_key, subject_of
from tests.unit.cli_output import said
from tests.unit.forarbeider_review_support import (
    MARKER,
    chapter,
    saved,
    with_changed_source,
    with_changed_text,
    withheld,
)

WHEN = "2100-04-02T12:00:00Z"
_ENGINE = Path(__file__).parents[2]


class Desk:
    """One staged observation and one ledger, outside any repository."""

    def __init__(self, root: Path, result: ChapterResult) -> None:
        self.root = root
        self.result = result
        self.staging = root / "staging"
        self.staging.mkdir(mode=0o700, exist_ok=True)
        self.ledger = root / "ledger"
        self.observation = saved(
            result, self.staging / f"{subject_key(subject_of(result))[:8]}.json"
        )

    @property
    def key(self) -> str:
        return subject_key(subject_of(self.result))

    def group(self, reviewer: str = "reviewer-1", when: str = WHEN) -> list[str]:
        scope = ["ops", "forarbeider-review", "--ledger", str(self.ledger)]
        return [*scope, "--reviewer", reviewer, "--decided-at", when]

    def run(self, *arguments: str, reviewer: str = "reviewer-1", when: str = WHEN) -> Any:
        return self.invoke([*self.group(reviewer, when), *arguments])

    def invoke(self, arguments: list[str], text: str | None = None) -> Any:
        return CliRunner().invoke(app, arguments, input=text)

    def decide(self, command: str, revision: int = 0, *extra: str, **scope: str) -> Any:
        pin = f"{self.key}@{revision}"
        target = ("--observation", str(self.observation), "--expect", pin)
        return self.run(command, *target, *extra, **scope)

    def hold(self, revision: int = 0, **scope: str) -> Any:
        arguments = ("--because", "awaiting_evidence=issue:738")
        return self.decide("hold", revision, *arguments, **scope)

    def attests(self, kinds: tuple[AttestationKind, ...] = tuple(AttestationKind)) -> list[str]:
        pairs = [("--attest", f"{kind.value}=adr:0018") for kind in kinds]
        return [item for pair in pairs for item in pair]

    def approve(self, revision: int = 0, phrase: str | None = None, **scope: str) -> Any:
        pin = f"{self.key}@{revision}"
        arguments = ["approve", "--observation", str(self.observation), "--expect", pin]
        command = [*self.group(**scope), *arguments, *self.attests()]
        short = (self.result.text_sha256 or "")[:12]
        typed = f"approve {self.result.id} {short}" if phrase is None else phrase
        return self.invoke(command, f"{typed}\n")

    def statuses(self) -> list[str]:
        history = load_history(self.ledger, subject_of(self.result))
        return [item.decision.status.value for item in history]


@pytest.fixture
def desk(tmp_path: Path) -> Desk:
    return Desk(tmp_path, chapter())


@pytest.fixture
def at_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_commands, "_interactive", lambda: True)


def test_hold_records_a_revision(desk: Desk) -> None:
    result = desk.hold()
    assert result.exit_code == 0, result.output
    assert said(f"held {desk.result.id}: revision 1", result.output)
    assert desk.statuses() == ["held"]


def test_every_line_says_a_decision_permits_nothing(desk: Desk) -> None:
    assert said("not permission to publish or crawl", desk.hold().output)


def test_reject_records_a_revision(desk: Desk) -> None:
    arguments = ("--because", "private_individual=issue:738")
    assert desk.decide("reject", 0, *arguments).exit_code == 0
    assert desk.statuses() == ["rejected"]


def test_reopen_returns_a_rejection_to_pending(desk: Desk) -> None:
    arguments = ("--because", "private_individual=issue:738")
    desk.decide("reject", 0, *arguments)
    reopened = ("--because", "reopened=issue:738")
    result = desk.decide("reopen", 1, *reopened, when="2100-04-03T12:00:00Z")
    assert result.exit_code == 0, result.output
    assert desk.statuses() == ["rejected", "pending"]


@pytest.mark.usefixtures("at_a_terminal")
def test_reopening_withdraws_an_approval(desk: Desk) -> None:
    desk.approve()
    arguments = ("--because", "reopened=issue:738")
    assert desk.decide("reopen", 1, *arguments, when="2100-04-03T12:00:00Z").exit_code == 0
    assert desk.statuses() == ["approved", "pending"]


def test_retry_says_unchanged(desk: Desk) -> None:
    desk.hold()
    result = desk.hold()
    assert result.exit_code == 0
    assert said("unchanged", result.output)
    assert desk.statuses() == ["held"]


def test_a_reviewer_is_required(desk: Desk) -> None:
    command = ["ops", "forarbeider-review", "--ledger", str(desk.ledger), "--decided-at", WHEN]
    pin = f"{desk.key}@0"
    result = desk.invoke(
        [
            *command,
            "hold",
            "--observation",
            str(desk.observation),
            "--expect",
            pin,
            "--because",
            "awaiting_evidence=issue:738",
        ]
    )
    assert result.exit_code == 1
    assert said("refused: review_invalid: reviewer", result.output)
    assert desk.statuses() == []


def test_a_decision_time_is_required(desk: Desk) -> None:
    scope = ReviewScope(ledger=desk.ledger, reviewer="reviewer-1", decided_at=None)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        review_commands.decide_impl(
            scope,
            review_commands.Pinned(observation=desk.observation, expect=f"{desk.key}@0"),
            (review_commands.ReviewStatus.HELD, ("awaiting_evidence",), ("issue:738",)),
        )
    assert caught.value.reason is RefusalReason.INVALID_TIMESTAMP


@pytest.mark.parametrize("when", ["2100-04-02T12:00:00", "yesterday"])
def test_a_time_without_an_offset_is_refused(desk: Desk, when: str) -> None:
    result = desk.hold(when=when)
    assert result.exit_code == 1
    assert said("refused: invalid_timestamp", result.output)


def test_evidence_is_required(desk: Desk) -> None:
    result = desk.decide("hold", 0, "--because", "awaiting_evidence")
    assert result.exit_code == 1
    assert said("refused: review_invalid: evidence", result.output)
    assert desk.statuses() == []


def test_the_approval_reason_is_refused_outside_an_approval(desk: Desk) -> None:
    result = desk.decide("hold", 0, "--because", "attestations_complete=adr:0018")
    assert result.exit_code == 1
    assert said("refused: review_invalid: decision", result.output)
    assert desk.statuses() == []


def test_a_decision_needs_a_reason_at_all(desk: Desk) -> None:
    assert desk.decide("hold", 0).exit_code != 0
    assert desk.statuses() == []


def test_free_text_evidence_is_refused_and_not_echoed(desk: Desk) -> None:
    arguments = ("--because", f"awaiting_evidence=note:{MARKER}")
    result = desk.decide("hold", 0, *arguments)
    assert result.exit_code == 1
    assert said("refused: review_invalid: evidence", result.output)
    assert "Synthetisk" not in result.output


def test_an_unknown_reason_is_refused_and_not_echoed(desk: Desk) -> None:
    arguments = ("--because", "Ola=issue:738")
    result = desk.decide("hold", 0, *arguments)
    assert result.exit_code == 1
    assert said("refused: review_invalid: reason_codes", result.output)
    assert "Ola" not in result.output


def test_a_mismatched_subject_is_refused(desk: Desk) -> None:
    other = subject_key(subject_of(with_changed_text(desk.result)))
    command = ["hold", "--observation", str(desk.observation), "--expect", f"{other}@0"]
    result = desk.run(*command, "--because", "awaiting_evidence=issue:738")
    assert result.exit_code == 1
    assert said("refused: review_subject_mismatch", result.output)
    assert desk.statuses() == []


@pytest.mark.parametrize("pin", ["", "abc@0", "a" * 64, f"{'a' * 64}@x", f"{'A' * 64}@0"])
def test_a_malformed_pin_is_refused(desk: Desk, pin: str) -> None:
    command = ["hold", "--observation", str(desk.observation), "--expect", pin]
    result = desk.run(*command, "--because", "awaiting_evidence=issue:738")
    assert result.exit_code == 1
    assert said("refused: review_invalid: expect", result.output)


def test_a_conflicting_reviewer_is_refused(desk: Desk) -> None:
    desk.hold()
    arguments = ("--because", "private_individual=issue:738")
    result = desk.decide("reject", 0, *arguments, reviewer="reviewer-2")
    assert result.exit_code == 1
    assert said("refused: review_conflict", result.output)
    assert desk.statuses() == ["held"]


def test_approval_needs_a_terminal(desk: Desk) -> None:
    result = desk.approve()
    assert result.exit_code == 1
    assert said("refused: review_not_interactive", result.output)
    assert desk.statuses() == []


@pytest.mark.usefixtures("at_a_terminal")
def test_approval_needs_the_phrase_typed(desk: Desk) -> None:
    result = desk.approve(phrase="y")
    assert result.exit_code == 1
    assert said("refused: review_not_interactive", result.output)
    assert desk.statuses() == []


@pytest.mark.usefixtures("at_a_terminal")
def test_a_complete_approval_is_recorded_and_permits_nothing(desk: Desk) -> None:
    result = desk.approve()
    assert result.exit_code == 0, result.output
    assert said("approved", result.output)
    assert said("not permission to publish or crawl", result.output)
    stored = load_history(desk.ledger, subject_of(desk.result))[0]
    assert stored.decision.grants_publication is False
    assert stored.decision.grants_crawling is False
    assert stored.decision.evidence == ("adr:0018",)
    assert read_chapter(desk.observation).release_state == "held"


@pytest.mark.usefixtures("at_a_terminal")
def test_a_retried_approval_asks_nothing_new(desk: Desk) -> None:
    desk.approve()
    assert said("unchanged", desk.approve().output)
    assert desk.statuses() == ["approved"]


@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize("missing", list(AttestationKind))
def test_a_missing_attestation_keeps_the_chapter_unapproved(
    desk: Desk, missing: AttestationKind
) -> None:
    desk.hold()
    kinds = tuple(kind for kind in AttestationKind if kind is not missing)
    target = ("--observation", str(desk.observation), "--expect", f"{desk.key}@1")
    result = desk.run("approve", *target, *desk.attests(kinds), when="2100-04-03T12:00:00Z")
    assert result.exit_code == 1
    assert said(f"refused: review_attestation_missing: {missing.value}", result.output)
    assert desk.statuses() == ["held"]


@pytest.mark.usefixtures("at_a_terminal")
def test_an_attestation_without_a_shape_is_refused(desk: Desk) -> None:
    target = ("--observation", str(desk.observation), "--expect", f"{desk.key}@0")
    result = desk.run("approve", *target, "--attest", "privacy_basis")
    assert result.exit_code == 1
    assert said("refused: review_invalid: attest", result.output)


@pytest.mark.usefixtures("at_a_terminal")
def test_an_attestation_of_an_unknown_kind_is_refused_and_not_echoed(desk: Desk) -> None:
    target = ("--observation", str(desk.observation), "--expect", f"{desk.key}@0")
    result = desk.run("approve", *target, "--attest", "Ola=adr:0018")
    assert result.exit_code == 1
    assert said("refused: review_invalid: attest", result.output)
    assert "Ola" not in result.output


@pytest.mark.usefixtures("at_a_terminal")
def test_an_attestation_with_free_text_evidence_is_refused(desk: Desk) -> None:
    arguments = [*desk.attests()[:-1], "third_party_checks=note:Ola"]
    target = ("--observation", str(desk.observation), "--expect", f"{desk.key}@0")
    result = desk.invoke([*desk.group(), "approve", *target, *arguments], "x\n")
    assert result.exit_code == 1
    assert said("refused: review_invalid: attest", result.output)
    assert "Ola" not in result.output


@pytest.mark.usefixtures("at_a_terminal")
def test_two_references_for_one_attestation_are_kept_together(desk: Desk) -> None:
    extra = ["--attest", "privacy_basis=issue:738"]
    target = ["--observation", str(desk.observation), "--expect", f"{desk.key}@0"]
    short = (desk.result.text_sha256 or "")[:12]
    typed = f"approve {desk.result.id} {short}\n"
    result = desk.invoke([*desk.group(), "approve", *target, *desk.attests(), *extra], typed)
    assert result.exit_code == 0, result.output
    stored = load_history(desk.ledger, subject_of(desk.result))[0].decision
    held = {item.kind: item.evidence for item in stored.attestations}
    assert held[AttestationKind.PRIVACY_BASIS] == ("adr:0018", "issue:738")
    assert stored.evidence == ("adr:0018", "issue:738")


@pytest.mark.usefixtures("at_a_terminal")
def test_a_chapter_without_a_body_cannot_be_approved(tmp_path: Path) -> None:
    desk = Desk(tmp_path, withheld(chapter()))
    result = desk.approve()
    assert result.exit_code == 1
    assert said("refused: review_not_eligible", result.output)
    assert desk.statuses() == []


@pytest.mark.usefixtures("at_a_terminal")
def test_a_rejection_cannot_be_approved_in_one_step(desk: Desk) -> None:
    arguments = ("--because", "private_individual=issue:738")
    desk.decide("reject", 0, *arguments)
    result = desk.approve(1, when="2100-04-03T12:00:00Z")
    assert result.exit_code == 1
    assert said("refused: review_transition", result.output)


@pytest.mark.usefixtures("at_a_terminal")
@pytest.mark.parametrize("changed", [with_changed_text, with_changed_source])
def test_an_approval_does_not_reach_a_changed_observation(desk: Desk, changed: Any) -> None:
    desk.approve()
    later = Desk(desk.root, changed(desk.result))
    later.observation = saved(later.result, desk.staging / "later.json")
    assert said("status pending", show_impl(_scope(desk), later.observation))
    assert said("status approved", show_impl(_scope(desk), desk.observation))


def _scope(desk: Desk) -> ReviewScope:
    return ReviewScope(ledger=desk.ledger, reviewer=None, decided_at=None)


def test_show_prints_the_binding_and_the_pin(desk: Desk) -> None:
    shown = show_impl(_scope(desk), desk.observation)
    for value in (
        desk.result.source_page_url,
        desk.result.source_sha256,
        desk.result.text_sha256,
        f"--expect {desk.key}@0",
        "status pending",
        "review_required",
    ):
        assert value in shown


def test_show_lists_every_revision(desk: Desk) -> None:
    desk.hold()
    desk.hold(1, when="2100-04-03T12:00:00Z")
    shown = show_impl(_scope(desk), desk.observation)
    assert "revision 1 held reviewer-1 2100-04-02T12:00:00Z awaiting_evidence issue:738" in shown
    assert f"--expect {desk.key}@2" in shown


def test_candidates_lists_each_observation(desk: Desk) -> None:
    desk.hold()
    other = with_changed_source(desk.result)
    saved(other, desk.staging / "other.json")
    listed = candidates_impl(_scope(desk), desk.staging).splitlines()
    assert len(listed) == 2
    assert sum(" status held " in line for line in listed) == 1
    assert sum(" status pending " in line for line in listed) == 1


def test_candidates_of_an_empty_staging_directory(desk: Desk, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir(mode=0o700)
    assert candidates_impl(_scope(desk), empty) == "no observations"


def test_candidates_ignores_what_is_not_json(desk: Desk) -> None:
    (desk.staging / "notes.txt").write_text("x", encoding="utf-8")
    assert len(candidates_impl(_scope(desk), desk.staging).splitlines()) == 1


def test_candidates_refuses_a_missing_staging_directory(desk: Desk, tmp_path: Path) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        candidates_impl(_scope(desk), tmp_path / "gone")
    assert caught.value.reason is RefusalReason.INPUT_UNREADABLE


def test_candidates_refuses_a_staging_directory_others_can_list(desk: Desk) -> None:
    desk.staging.chmod(0o755)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        candidates_impl(_scope(desk), desk.staging)
    assert caught.value.reason is RefusalReason.REVIEW_LEDGER_UNSAFE


def test_verify_reports_each_chain_and_its_head(desk: Desk) -> None:
    desk.hold()
    stored = load_history(desk.ledger, subject_of(desk.result))[0]
    assert verify_impl(_scope(desk)).splitlines() == [
        f"{desk.key} {desk.result.id} revision 1 status held head {stored.sha256}",
        "1 chains verified; a cut or rebuilt chain is not detectable here",
    ]


def test_verify_of_no_ledger(desk: Desk) -> None:
    assert said("0 chains verified", verify_impl(_scope(desk)))


def test_read_commands_refuse_through_the_cli(desk: Desk) -> None:
    desk.observation.chmod(0o644)
    show = ["show", "--observation", str(desk.observation)]
    result = desk.invoke([*desk.group(), *show])
    assert result.exit_code == 1
    assert said("refused: observation_unsafe", result.output)


def test_read_commands_work_without_reviewer_or_time(desk: Desk) -> None:
    scope = ["ops", "forarbeider-review", "--ledger", str(desk.ledger)]
    for arguments in (
        ["show", "--observation", str(desk.observation)],
        ["candidates", "--staging", str(desk.staging)],
        ["verify"],
    ):
        result = desk.invoke([*scope, *arguments])
        assert result.exit_code == 0, result.output


def test_commands_refuse_through_the_cli_for_candidates_and_verify(desk: Desk) -> None:
    desk.ledger.mkdir(mode=0o755)
    scope = ["ops", "forarbeider-review", "--ledger", str(desk.ledger)]
    for arguments in (["candidates", "--staging", str(desk.staging)], ["verify"]):
        result = desk.invoke([*scope, *arguments])
        assert result.exit_code == 1
        assert said("refused: review_ledger_unsafe", result.output)


def test_an_observation_others_can_read_is_refused_before_any_decision(desk: Desk) -> None:
    desk.observation.chmod(0o644)
    result = desk.hold()
    assert result.exit_code == 1
    assert said("refused: observation_unsafe", result.output)
    assert not desk.ledger.exists()


def test_an_unsafe_ledger_is_refused(desk: Desk) -> None:
    desk.ledger.mkdir(mode=0o755)
    result = desk.hold()
    assert result.exit_code == 1
    assert said("refused: review_ledger_unsafe", result.output)


def test_a_ledger_inside_the_engine_is_refused(desk: Desk) -> None:
    desk.ledger = _ENGINE / "review-ledger"
    result = desk.hold()
    assert result.exit_code == 1
    assert said("refused: unsafe_output_path", result.output)
    assert not desk.ledger.exists()


def test_an_observation_in_a_repository_is_refused(desk: Desk) -> None:
    (desk.root / ".git").mkdir()
    result = desk.hold()
    assert result.exit_code == 1
    assert said("refused: unsafe_output_path", result.output)


def test_an_observation_too_large_is_refused(desk: Desk) -> None:
    with desk.observation.open("r+b") as stream:
        stream.truncate(MAX_OBSERVATION_BYTES + 1)
    result = desk.hold()
    assert result.exit_code == 1
    assert said("refused: input_too_large", result.output)


def test_an_observation_at_the_limit_reaches_the_schema(desk: Desk) -> None:
    with desk.observation.open("r+b") as stream:
        stream.truncate(MAX_OBSERVATION_BYTES)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_observation(desk.observation, ())
    assert caught.value.reason is RefusalReason.SCHEMA_INVALID


def test_an_observation_of_another_schema_is_refused(desk: Desk) -> None:
    path = desk.staging / "metadata.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_observation(path, ())
    assert caught.value.reason is RefusalReason.SCHEMA_INVALID


@pytest.mark.usefixtures("at_a_terminal")
def test_no_chapter_text_reaches_output_or_ledger(tmp_path: Path) -> None:
    desk = Desk(tmp_path, with_changed_text(chapter()))
    printed = [
        desk.approve().output,
        desk.hold(1, when="2100-04-03T12:00:00Z").output,
        show_impl(_scope(desk), desk.observation),
        candidates_impl(_scope(desk), desk.staging),
        verify_impl(_scope(desk)),
    ]
    assert not any(MARKER in text for text in printed)
    records = list(desk.ledger.rglob("*.json"))
    assert len(records) == 2
    assert not any(MARKER in path.read_text(encoding="utf-8") for path in records)


def test_no_chapter_text_reaches_a_refusal(tmp_path: Path) -> None:
    desk = Desk(tmp_path, with_changed_text(chapter()))
    desk.ledger.mkdir(mode=0o755)
    assert MARKER not in desk.hold().output


def test_the_same_decisions_write_the_same_bytes(tmp_path: Path) -> None:
    trees = []
    for name in ("one", "two"):
        root = tmp_path / name
        root.mkdir()
        desk = Desk(root, chapter())
        desk.hold()
        trees.append({p.name: p.read_bytes() for p in desk.ledger.rglob("*.json")})
    assert trees[0] == trees[1]


def test_loading_the_cli_brings_in_no_law_path() -> None:
    probe = (
        "import sys; import lovspor.forarbeider.review_commands; "
        "print([m for m in sys.modules if m in ('lovspor.mcp', 'httpx')])"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert "lovspor.mcp" not in done.stdout
