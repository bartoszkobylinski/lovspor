"""The promotion decision log: append-only, strict, human-attributed (ADR-0016 4c)."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import DecisionLogError, StorageBoundaryError
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.decisions import (
    DECISIONS_FILENAME,
    ArtifactKey,
    Decision,
    DecisionLog,
    HeldRecord,
    HumanDecision,
    utc_text,
)

KEY = ArtifactKey(authority_id="0301", sha256="a" * 64, source_url="https://x.invalid/f")
AT = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


def _log(tmp_path: Path) -> DecisionLog:
    return DecisionLog(ObservatoryRoot(tmp_path, []))


def _decision(decision: Decision = Decision.REJECT, at: datetime = AT) -> HumanDecision:
    return HumanDecision(
        artifact=KEY, decision=decision, decided_by="Kari", decided_at=at, reason="Ikke vedtatt."
    )


def _held(at: datetime = AT, reason: str = "no_identity") -> HeldRecord:
    return HeldRecord(
        artifact=KEY, recorded_at=at, stage="identity", reason=reason, detail="no vedtaksdato"
    )


def test_the_log_lives_in_the_observatory_root(tmp_path: Path) -> None:
    assert _log(tmp_path).path == tmp_path.resolve() / DECISIONS_FILENAME


def test_a_bare_path_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StorageBoundaryError) as refused:
        DecisionLog(tmp_path)  # type: ignore[arg-type]

    assert str(refused.value) == (
        "DecisionLog requires an ObservatoryRoot; resolve it through observatory_root()"
    )


def test_records_read_back_in_append_order(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(_decision())
    log.append(_held())

    assert [record.kind for record in log.records()] == ["decision", "held"]


def test_the_last_decision_on_an_artifact_stands(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(_decision(Decision.HOLD))
    log.append(_decision(Decision.REJECT, at=AT.replace(hour=10)))

    standing = log.latest_decision(KEY)

    assert standing is not None
    assert standing.decision is Decision.REJECT


def test_no_decision_is_none(tmp_path: Path) -> None:
    assert _log(tmp_path).latest_decision(KEY) is None


def test_a_line_that_does_not_parse_is_refused(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(_decision())
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "decision"}\n')

    with pytest.raises(DecisionLogError, match="line 2"):
        log.records()


def test_a_torn_last_record_is_refused(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.append(_decision())
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "dec')

    with pytest.raises(DecisionLogError, match="incomplete"):
        log.records()


def test_a_repeated_outcome_is_not_appended_again(tmp_path: Path) -> None:
    log = _log(tmp_path)

    assert log.record_outcome(_held()) is True
    assert log.record_outcome(_held(at=AT.replace(hour=11))) is False
    assert log.record_outcome(_held(reason="central_collision")) is True
    assert len(log.records()) == 2


def test_an_approval_must_name_the_text_it_read() -> None:
    with pytest.raises(ValidationError, match="content_hash"):
        _decision(Decision.APPROVE)


@pytest.mark.parametrize("name", ["classifier", "LOVSPOR", "  auto  ", ""])
def test_a_decision_is_a_persons(name: str) -> None:
    with pytest.raises(ValidationError):
        HumanDecision(
            artifact=KEY, decision=Decision.REJECT, decided_by=name, decided_at=AT, reason="r"
        )


def test_utc_text_spells_every_instant_in_utc_with_z() -> None:
    assert utc_text(datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC)) == "2026-08-19T15:17:23Z"


def test_utc_text_ignores_the_process_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Europe/Oslo")
    time.tzset()
    try:
        spelled = utc_text(datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC))
    finally:
        monkeypatch.undo()
        time.tzset()

    assert spelled == "2026-08-19T15:17:23Z"


def test_a_machine_reviewer_is_refused_with_the_rule() -> None:
    with pytest.raises(ValidationError) as refused:
        HumanDecision(
            artifact=KEY, decision=Decision.REJECT, decided_by="auto", decided_at=AT, reason="r"
        )

    assert "decided_by must name a person, not 'auto' (ADR-0016 4c)" in str(refused.value)


def test_the_log_is_recreated_with_its_parents_when_the_root_went_missing(
    tmp_path: Path,
) -> None:
    log = DecisionLog(ObservatoryRoot(tmp_path / "gone" / "root", []))

    log.append(_decision())

    assert [record.kind for record in log.records()] == ["decision"]


def test_the_log_is_utf8_whatever_the_locale(tmp_path: Path, c_locale: None) -> None:
    log = _log(tmp_path)
    decision = HumanDecision(
        artifact=KEY,
        decision=Decision.REJECT,
        decided_by="Åse Ødegård",
        decided_at=AT,
        reason="Kilden er ikke kunngjort på nett.",
    )

    log.append(decision)

    assert log.records() == (decision,)
    assert "Åse Ødegård" in log.path.read_bytes().decode("utf-8")


def test_another_artifacts_outcome_in_between_does_not_hide_a_repeat(tmp_path: Path) -> None:
    log = _log(tmp_path)
    other = KEY.model_copy(update={"sha256": "b" * 64})
    log.record_outcome(_held())
    log.record_outcome(_held().model_copy(update={"artifact": other}))

    assert log.record_outcome(_held(at=AT.replace(hour=11))) is False
    assert len(log.records()) == 2


def test_a_decision_after_an_outcome_does_not_hide_a_repeat(tmp_path: Path) -> None:
    log = _log(tmp_path)
    log.record_outcome(_held())
    log.append(_decision())

    assert log.record_outcome(_held(at=AT.replace(hour=11))) is False
