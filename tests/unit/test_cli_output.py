"""Regression tests for width-independent CLI-output comparisons."""

import os
from typing import Annotated

import pytest
import typer
from typer.testing import CliRunner

from tests.unit.cli_output import option_help, said, unwrapped


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


_HELP = "A new, empty directory outside every Git checkout and Lovverk."


def _app() -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def capture(cache: Annotated[str, typer.Option("--cache", help=_HELP)] = "") -> None:
        """Capture."""

    @app.command()
    def other() -> None:
        """A second command, so ``capture`` is a subcommand as in the real app."""

    return app


def test_option_help_in_an_options_table_is_truncated_so_said_cannot_see_it() -> None:
    """Issue #801: at 20 columns rich cuts options-table cells with an ellipsis."""
    result = CliRunner().invoke(_app(), ["capture", "--help"])

    assert result.exit_code == 0
    assert "…" in result.output
    assert not said(_HELP, result.output)


def test_option_help_reads_the_click_parameter_at_any_width() -> None:
    assert option_help(_app(), ["capture"], "--cache") == _HELP


def test_option_help_refuses_an_option_the_command_does_not_have() -> None:
    with pytest.raises(LookupError, match="--missing"):
        option_help(_app(), ["capture"], "--missing")


@pytest.mark.parametrize("option", ["--cache", "-c"])
def test_option_help_reads_root_command_option_aliases(option: str) -> None:
    app = typer.Typer()

    @app.command()
    def capture(cache: Annotated[str, typer.Option("--cache", "-c", help=_HELP)] = "") -> None:
        """A single command is the root Click command."""

    assert option_help(app, [], option) == _HELP


def test_option_help_returns_none_when_option_has_no_help() -> None:
    app = typer.Typer()

    @app.command()
    def capture(cache: Annotated[str, typer.Option("--cache")] = "") -> None:
        """Capture."""

    assert option_help(app, (), "--cache") is None


def test_option_help_selects_nested_command_without_using_parent_or_sibling_help() -> None:
    app = _app()
    nested = typer.Typer()
    app.add_typer(nested, name="nested")

    @nested.callback()
    def group(cache: Annotated[str, typer.Option("--cache", help="Group cache")] = "") -> None:
        """Nested group."""

    @nested.command()
    def capture(cache: Annotated[str, typer.Option("--cache", help="Nested cache")] = "") -> None:
        """Nested capture."""

    @nested.command()
    def other() -> None:
        """A sibling without a cache option."""

    assert option_help(app, ("nested",), "--cache") == "Group cache"
    assert option_help(app, ("nested", "capture"), "--cache") == "Nested cache"
    with pytest.raises(LookupError, match="nested other has no option --cache"):
        option_help(app, ("nested", "other"), "--cache")
