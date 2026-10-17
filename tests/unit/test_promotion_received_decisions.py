"""Received documents in the decision log and the audit (ADR-0016 Amendment 1, A1-A3).

A record names its source by an ``ArtifactKey`` (observed at a URL) or a
``ReceivedKey`` (received in answer to an innsyn request, no URL). The two
channels never answer each other's lookups, even on the same bytes. Every
line the log already holds parses and re-dumps byte-identically: the golden
lines below were dumped by the code before the received channel existed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.decisions import (
    ArtifactKey,
    Decision,
    DecisionLog,
    DecisionLogRecord,
    HeldRecord,
    HumanDecision,
    IdentityAudit,
    InnsynAudit,
    ObservationUsed,
    PromotedRecord,
    PromotionAudit,
    ReceivedKey,
    SourceKey,
    WithdrawalRecord,
)
from lovspor.promotion.models import IdScheme, RemovedReason
from lovspor.promotion.withdraw import refuse_withdrawn

SHA = "b" * 64
URL = "https://fjaler.kommune.invalid/forskrift.pdf"
REQUEST = "4646-20261005T074425Z"
AT = datetime(2026, 10, 7, 12, 45, 6, tzinfo=UTC)
OBSERVED = ArtifactKey(authority_id="4646", sha256=SHA, source_url=URL)
RECEIVED = ReceivedKey(authority_id="4646", innsyn_request=REQUEST, sha256=SHA)
DOC_ID = "lk-4646-aa8ae774921d"
_KEY = '{"authority_id":"4646","sha256":"' + SHA + '","source_url":"' + URL + '"}'
_C = "c" * 64
GOLDEN_LINES = (
    '{"kind":"decision","artifact":' + _KEY + ',"decision":"approve",'
    '"decided_by":"Ola Nordmann","reviewer_role":"project owner",'
    '"decided_at":"2026-10-07T12:45:06Z","reason":"Vedtatt forskrift.",'
    '"content_hash":"' + _C + '","extractor_version":7,"classifier":null}',
    '{"kind":"promoted","artifact":' + _KEY + ',"recorded_at":"2026-10-07T12:45:06Z",'
    '"doc_id":"lk-4646-aa8ae774921d","version":1,"markdown_path":"lokale-forskrifter/4646/x.md",'
    '"content_hash":"' + _C + '","commit_subject":"promote(lokal-forskrift): 4646/x",'
    '"audit":{"decision":"approve","reviewed_by_role":"project owner",'
    '"decided_at":"2026-10-07T12:45:06Z","reason":"Vedtatt forskrift.",'
    '"reviewed_in_sample":true,"classifier":null,"extractor_version":7,"renderer_version":1,'
    '"source_form":"pdf","identity":{"scheme":"lk","doc_id":"lk-4646-aa8ae774921d",'
    '"ref_id":null,"candidates":[]},"observations_through":"2026-10-07T12:45:06Z",'
    '"observations":[{"observed_at":"2026-10-07T12:45:06Z","url":"' + URL + '",'
    '"sha256":"' + SHA + '"}]}}',
    '{"kind":"held","artifact":' + _KEY + ',"recorded_at":"2026-10-07T12:45:06Z",'
    '"stage":"extraction","reason":"empty_text","detail":"no text layer","personal_data":[]}',
    '{"kind":"withdrawal","doc_id":"lk-4646-aa8ae774921d","authority_id":"4646","slug":"x",'
    '"removed_reason":"withdrawn_misclassified","artifacts":[' + _KEY + "],"
    '"decided_by":"Ola Nordmann","reviewer_role":"project owner",'
    '"decided_at":"2026-10-07T12:45:06Z","reason":"Klassifisert feil."}',
)
_RECORD: TypeAdapter[DecisionLogRecord] = TypeAdapter(DecisionLogRecord)
_SOURCE: TypeAdapter[SourceKey] = TypeAdapter(SourceKey)
INNSYN = {
    "innsyn_request": REQUEST,
    "received_at": "2026-10-07T12:45:06Z",
    "kind": "regulation_text",
    "answers": "2009-02-16-1234",
}


def _log(tmp_path: Path) -> DecisionLog:
    return DecisionLog(ObservatoryRoot(tmp_path, []))


def _decision(key: ArtifactKey | ReceivedKey, decision: Decision) -> HumanDecision:
    return HumanDecision(
        artifact=key,
        decision=decision,
        decided_by="Ola Nordmann",
        reviewer_role="project owner",
        decided_at=AT,
        reason="Vedtatt forskrift.",
        content_hash=_C,
        extractor_version=7,
    )


def _audit(innsyn: InnsynAudit | None = None) -> PromotionAudit:
    return PromotionAudit(
        decision=Decision.APPROVE,
        reviewed_by_role="project owner",
        decided_at="2026-10-07T12:45:06Z",
        reason="Vedtatt forskrift.",
        reviewed_in_sample=True,
        classifier=None,
        extractor_version=7,
        renderer_version=1,
        source_form="pdf",
        identity=IdentityAudit(scheme=IdScheme.LK, doc_id=DOC_ID, ref_id=None, candidates=()),
        observations_through="2026-10-07T12:45:06Z",
        observations=(ObservationUsed(observed_at="2026-10-07T12:45:06Z", url=URL, sha256=SHA),),
        innsyn=innsyn,
    )


def _held(key: ArtifactKey | ReceivedKey) -> HeldRecord:
    return HeldRecord(
        artifact=key, recorded_at=AT, stage="extraction", reason="empty_text", detail="no text"
    )


def _withdrawal(*keys: ArtifactKey | ReceivedKey) -> WithdrawalRecord:
    return WithdrawalRecord(
        doc_id=DOC_ID,
        authority_id="4646",
        slug="x",
        removed_reason=RemovedReason.WITHDRAWN_MISCLASSIFIED,
        artifacts=keys,
        decided_by="Ola Nordmann",
        reviewer_role="project owner",
        decided_at=AT,
        reason="Klassifisert feil.",
    )


class TestExistingLinesKeepTheirBytes:
    @pytest.mark.parametrize(
        "line", GOLDEN_LINES, ids=["decision", "promoted", "held", "withdrawal"]
    )
    def test_an_existing_line_parses_to_an_artifact_key_and_redumps_identically(
        self, line: str
    ) -> None:
        record = _RECORD.validate_json(line)
        keys = record.artifacts if isinstance(record, WithdrawalRecord) else (record.artifact,)

        assert record.model_dump_json() == line
        assert keys == (OBSERVED,)
        assert all(type(key) is ArtifactKey for key in keys)

    def test_an_existing_line_appended_and_read_back_keeps_its_bytes(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        for line in GOLDEN_LINES:
            log.append(_RECORD.validate_json(line))

        assert log.path.read_text(encoding="utf-8") == "".join(f"{x}\n" for x in GOLDEN_LINES)
        assert [r.model_dump_json() for r in log.records()] == list(GOLDEN_LINES)


class TestReceivedKey:
    def test_a_received_key_round_trips_through_the_union_as_itself(self) -> None:
        text = RECEIVED.model_dump_json()

        assert (
            text
            == '{"authority_id":"4646","innsyn_request":"' + REQUEST + '","sha256":"' + SHA + '"}'
        )
        assert _SOURCE.validate_json(text) == RECEIVED
        assert type(_SOURCE.validate_json(text)) is ReceivedKey
        assert type(_SOURCE.validate_json(OBSERVED.model_dump_json())) is ArtifactKey

    def test_a_model_passes_through_the_union_as_its_own_channel(self) -> None:
        assert _SOURCE.validate_python(RECEIVED) is RECEIVED
        assert _SOURCE.validate_python(OBSERVED) is OBSERVED

    def test_a_received_key_has_no_url_and_refuses_one(self) -> None:
        with pytest.raises(ValidationError):
            _SOURCE.validate_python({**RECEIVED.model_dump(), "source_url": URL})

    @pytest.mark.parametrize(
        ("field", "value"),
        [("innsyn_request", "4646-2026-10-05"), ("sha256", "B" * 64), ("authority_id", "464")],
    )
    def test_a_malformed_field_is_refused(self, field: str, value: str) -> None:
        with pytest.raises(ValidationError):
            ReceivedKey.model_validate({**RECEIVED.model_dump(), field: value})

    def test_a_received_key_is_frozen(self) -> None:
        with pytest.raises(ValidationError):
            RECEIVED.sha256 = "c" * 64  # type: ignore[misc]


class TestChannelSeparation:
    def test_a_decision_answers_only_its_own_channel(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        log.append(_decision(RECEIVED, Decision.APPROVE))

        assert log.latest_decision(OBSERVED) is None
        assert log.latest_decision(RECEIVED) == _decision(RECEIVED, Decision.APPROVE)
        log.append(_decision(OBSERVED, Decision.REJECT))
        assert log.latest_decision(RECEIVED) == _decision(RECEIVED, Decision.APPROVE)
        assert log.latest_decision(OBSERVED) == _decision(OBSERVED, Decision.REJECT)

    def test_an_outcome_on_one_channel_never_repeats_the_other(self, tmp_path: Path) -> None:
        log = _log(tmp_path)

        assert log.record_outcome(_held(OBSERVED))
        assert log.record_outcome(_held(RECEIVED))
        assert not log.record_outcome(_held(RECEIVED))
        assert [r.artifact for r in log.records() if isinstance(r, HeldRecord)] == [
            OBSERVED,
            RECEIVED,
        ]

    def test_a_withdrawal_covers_only_the_channel_it_names(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        log.append(_withdrawal(RECEIVED))

        assert log.withdrawal_of(RECEIVED, None) == _withdrawal(RECEIVED)
        assert log.withdrawal_of(OBSERVED, None) is None
        assert _withdrawal(OBSERVED).withdraws(RECEIVED, None) is False

    def test_a_rerun_on_a_withdrawn_received_document_is_refused(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        log.append(_withdrawal(OBSERVED, RECEIVED))

        with pytest.raises(PromotionRefusedError, match="never promoted again"):
            refuse_withdrawn(log, RECEIVED, None)


class TestReceivedRecords:
    def test_a_received_decision_round_trips_through_the_log(self, tmp_path: Path) -> None:
        log = _log(tmp_path)
        log.append(_decision(RECEIVED, Decision.APPROVE))

        [record] = log.records()
        assert record == _decision(RECEIVED, Decision.APPROVE)
        assert json.loads(log.path.read_text(encoding="utf-8"))["artifact"] == RECEIVED.model_dump()

    def test_held_and_promoted_records_with_a_received_key_round_trip(self) -> None:
        promoted = PromotedRecord(
            artifact=RECEIVED,
            recorded_at=AT,
            doc_id=DOC_ID,
            version=1,
            markdown_path="lokale-forskrifter/4646/x.md",
            content_hash=_C,
            commit_subject="promote(lokal-forskrift): 4646/x",
            audit=_audit(InnsynAudit.model_validate(INNSYN)),
        )
        for record in (promoted, _held(RECEIVED)):
            parsed = _RECORD.validate_json(record.model_dump_json())

            assert parsed == record
            assert type(parsed.artifact) is ReceivedKey  # type: ignore[union-attr]
            assert parsed.model_dump_json() == record.model_dump_json()

    def test_a_withdrawal_naming_both_channels_round_trips_in_order(self) -> None:
        record = _withdrawal(OBSERVED, RECEIVED)
        parsed = _RECORD.validate_json(record.model_dump_json())

        assert isinstance(parsed, WithdrawalRecord)
        assert [type(k) for k in parsed.artifacts] == [ArtifactKey, ReceivedKey]
        assert parsed == record


class TestInnsynAudit:
    def test_an_audit_without_innsyn_keeps_its_bytes(self) -> None:
        [line] = [x for x in GOLDEN_LINES if '"kind":"promoted"' in x]
        golden = json.dumps(json.loads(line)["audit"], separators=(",", ":"), ensure_ascii=False)

        assert _audit().model_dump_json() == golden
        assert "innsyn" not in _audit().model_dump()

    def test_an_audit_with_innsyn_dumps_it_last(self) -> None:
        audit = _audit(InnsynAudit.model_validate(INNSYN))
        dumped = json.loads(audit.model_dump_json())

        assert list(dumped)[-1] == "innsyn"
        assert dumped["innsyn"] == INNSYN
        assert PromotionAudit.model_validate_json(audit.model_dump_json()) == audit

    def test_answers_may_be_null(self) -> None:
        audit = InnsynAudit.model_validate({**INNSYN, "answers": None})

        assert audit.answers is None
        assert json.loads(audit.model_dump_json())["answers"] is None

    @pytest.mark.parametrize("kind", ["vedtak", "minutes", "cover_letter", "other"])
    def test_a_kind_other_than_regulation_text_is_refused(self, kind: str) -> None:
        with pytest.raises(ValidationError):
            InnsynAudit.model_validate({**INNSYN, "kind": kind})

    @pytest.mark.parametrize("answers", ["lf-2009-02-16-1234", "2009-02-16", "16.02.09", ""])
    def test_a_malformed_answers_is_refused(self, answers: str) -> None:
        with pytest.raises(ValidationError):
            InnsynAudit.model_validate({**INNSYN, "answers": answers})

    def test_a_malformed_request_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            InnsynAudit.model_validate({**INNSYN, "innsyn_request": "4646"})
