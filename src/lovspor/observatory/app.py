"""The observatory Typer app, and the option every command spells the same.

Separate from the command modules so that more than one of them can register
commands on the app without importing each other: the app must exist before
any decorator runs, and a module that owns both the app and a share of the
commands cannot be imported by its siblings without a cycle.
"""

from typing import Annotated

import typer

observatory_app = typer.Typer(
    name="observatory",
    help="Register and activate local-law capture sources (ADR-0010).",
    no_args_is_help=True,
)

_AuthorityIdOption = Annotated[
    str,
    typer.Option(
        "--id",
        help="Official authority identifier — kommunenummer or fylkesnummer, never a name.",
    ),
]
