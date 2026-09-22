"""The observatory CLI as the top-level app should import it.

:mod:`lovspor.observatory.commands` owns ``observatory_app`` and most of its
commands. A command defined in any other module registers itself by decorating
that app, which only happens if something imports the module — so importing
``observatory_app`` from :mod:`lovspor.cli` directly would silently ship a CLI
missing every command that lives elsewhere.

This module is that seam: one place that pulls in the command modules and
re-exports the assembled app. It exists rather than a bare import in
:mod:`lovspor.cli` because both that file and ``commands.py`` sit exactly at
their size ratchets, and a ratchet is feedback to work with rather than a
baseline to raise for one's own convenience.

Deliberately not in ``observatory/__init__.py``: the package is imported by the
MCP server and by library code that wants none of typer, and a package init that
drags in a CLI makes every such import pay for it.
"""

from lovspor.observatory import survey_commands as _survey_commands  # noqa: F401
from lovspor.observatory.commands import observatory_app

__all__ = ["observatory_app"]
