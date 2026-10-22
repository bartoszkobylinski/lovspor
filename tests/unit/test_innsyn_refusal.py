"""Refusal formatting promised by innsyn.app.refuse (#628)."""

import pytest
import typer
from pydantic import BaseModel, ValidationError

from lovspor.errors import InnsynRefusedError
from lovspor.innsyn.app import refuse
from lovspor.innsyn.ledger import Sent
from lovspor.innsyn.ledger_commands import event_from


def test_multiple_builtin_validation_errors_refuse_without_documentation_urls(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Input(BaseModel):
        count: int
        enabled: bool

    with pytest.raises(ValidationError) as caught:
        Input.model_validate({"count": "invalid", "enabled": "invalid"})

    # Exercise errors for which pydantic really supplies documentation URLs.
    errors = caught.value.errors(include_url=True)
    assert len(errors) == 2
    assert all(error["url"].startswith("https://") for error in errors)
    exit_error = refuse(caught.value)
    assert exit_error.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "Refused: count: Input should be a valid integer, "
        "unable to parse string as an integer; "
        "enabled: Input should be a valid boolean, unable to interpret input\n"
    )


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


def test_field_named_like_the_union_tag_is_preserved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Outcome's location is ("outcome", "outcome"): only the tag is redundant.
    with pytest.raises(typer.Exit) as caught:
        event_from("4646-20261005T074425Z", "outcome", {"at": None})

    assert caught.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Refused: outcome: Field required\n"


def test_tagged_event_preserves_every_indexed_validation_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fields = {
        "authority_id": "4646",
        "recipient": "post@example.no",
        "sent_at": "2026-10-05T07:44:25Z",
        "lf_ids": ["invalid-first", "invalid-second"],
        "general_clause": True,
        "letter_sha256": "a" * 64,
    }
    # Obtain pydantic's messages independently of the refusal formatter.
    with pytest.raises(ValidationError) as validation:
        Sent.model_validate(
            {"request_id": "4646-20261005T074425Z", "recorded_at": "2026-10-05T07:44:25Z", **fields}
        )
    errors = validation.value.errors()
    assert [error["loc"] for error in errors] == [("lf_ids", 0), ("lf_ids", 1)]

    with pytest.raises(typer.Exit) as caught:
        event_from("4646-20261005T074425Z", "sent", fields)

    assert caught.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        f"Refused: lf_ids.0: {errors[0]['msg']}; lf_ids.1: {errors[1]['msg']}\n"
    )
