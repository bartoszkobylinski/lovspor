"""Enforce record_events' check-against-preceding-events and all-or-none contract."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.ledger import Acknowledged, Sent, ledger_journal, record_events, requests
from lovspor.innsyn.workspace import InnsynWorkspace


@pytest.fixture
def sent() -> Sent:
    moment = datetime(2026, 10, 13, 8, tzinfo=UTC)
    return Sent(
        request_id="4646-20261013T080000Z",
        recorded_at=moment,
        authority_id="4646",
        recipient="postmottak@fjaler.kommune.no",
        sent_at=moment,
        lf_ids=(),
        general_clause=True,
        letter_sha256="a" * 64,
    )


def test_batch_transition_can_reference_its_preceding_send(tmp_path: Path, sent: Sent) -> None:
    target = InnsynWorkspace(tmp_path / "innsyn", [])
    ack = Acknowledged(
        request_id=sent.request_id,
        recorded_at=sent.recorded_at,
        acknowledged_at=sent.sent_at,
    )

    record_events(target, [sent, ack])

    assert ledger_journal(target).records() == [sent, ack]
    state = requests(target)[sent.request_id]
    assert state.sent == sent
    assert state.acknowledged_at == sent.sent_at


@pytest.mark.parametrize("existing", [False, True])
def test_duplicate_send_within_batch_preserves_all_existing_evidence(
    tmp_path: Path, sent: Sent, existing: bool
) -> None:
    target = InnsynWorkspace(tmp_path / "innsyn", [])
    if existing:
        target.ensure()
        # A valid empty journal is still evidence whose bytes must be preserved.
        target.requests_log.write_bytes(b"")

    with pytest.raises(InnsynRefusedError, match="already in the ledger"):
        record_events(target, [sent, sent])

    if existing:
        assert target.requests_log.read_bytes() == b""
    else:
        assert not target.path.exists()


def test_invalid_transition_after_new_send_does_not_create_partial_ledger(
    tmp_path: Path, sent: Sent
) -> None:
    target = InnsynWorkspace(tmp_path / "innsyn", [])
    ack = Acknowledged(
        request_id=sent.request_id,
        recorded_at=sent.recorded_at,
        acknowledged_at=sent.sent_at - timedelta(seconds=1),
    )

    with pytest.raises(InnsynRefusedError, match="before it was sent"):
        record_events(target, [sent, ack])

    assert not target.path.exists()
    assert requests(target) == {}
