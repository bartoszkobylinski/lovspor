"""Regression tests for width-independent CLI-output comparisons."""

import os

from tests.unit.cli_output import said, unwrapped


def test_every_unit_test_runs_with_the_narrow_console() -> None:
    """The autouse fixture must exercise Rich's mid-word wrapping path."""
    assert os.environ["COLUMNS"] == "20"


def test_unwrapped_removes_rich_styling_frame_and_mid_word_wrap() -> None:
    rendered = (
        "\x1b[31m\u256d\u2500 Error \u2500\u256e\x1b[0m\n"
        "\u2502 release_content_i \u2502\n"
        "\u2502 d is invalid      \u2502\n"
        "\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f\n"
    )

    assert unwrapped(rendered) == "Errorrelease_content_idisinvalid"
    assert said("release_content_id is invalid", rendered)


def test_said_does_not_match_characters_that_were_not_printed() -> None:
    rendered = "\u2502 release_content_i \u2502\n\u2502 d is invalid \u2502"

    assert not said("release_content_uuid is invalid", rendered)
