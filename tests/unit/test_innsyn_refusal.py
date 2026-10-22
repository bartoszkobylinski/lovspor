"""Refusal formatting promised by innsyn.app.refuse (#628)."""

import pytest
import typer
from pydantic import ValidationError

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.app import refuse
from lovspor.innsyn.ledger import Sent
from lovspor.innsyn.ledger_commands import event_from


@pytest.mark.parametrize("under", [None, "sent", "outcome"])
def test_direct_validation_keeps_indexed_field_locations_with_or_without_a_tag(
    capsys: pytest.CaptureFixture[str], under: str | None
) -> None:
    data = {
        "request_id": "4646-20261005T074425Z",
        "recorded_at": "2026-10-05T07:44:25Z",
        "authority_id": "4646",
        "recipient": "post@example.no",
        "sent_at": "2026-10-05T07:44:25Z",
        "lf_ids": ["invalid"],
        "general_clause": True,
        "letter_sha256": "a" * 64,
    }
    # Direct model validation has no union tag, even when under is supplied.
    with pytest.raises(ValidationError) as caught:
        Sent.model_validate(data)
    reason = caught.value.errors(include_url=False)[0]["msg"]
    exit_error = refuse(caught.value, under=under)
    assert exit_error.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Refused: lf_ids.0: {reason}\n"


def test_missing_event_field_names_the_field_without_the_union_tag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as caught:
        event_from("4646-20261005T074425Z", "case_number", {})
    assert caught.value.exit_code == 1
    assert capsys.readouterr().err == "Refused: saksnummer: Field required\n"


def test_field_validator_reason_keeps_its_field_and_drops_value_error_prefix(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit) as caught:
        event_from(
            "4646-20261005T074425Z",
            "acknowledged",
            {"acknowledged_at": "2026-10-05T07:44:25"},
        )
    assert caught.value.exit_code == 1
    assert capsys.readouterr().err == (
        "Refused: acknowledged_at: timestamp must be timezone-aware UTC, got a naive datetime\n"
    )


def test_ordinary_refusal_preserves_the_exception_message(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_error = refuse(InnsynRefusedError("Value error, document: not received"))
    assert exit_error.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Refused: Value error, document: not received\n"
