"""Emit signals to the GitHub Actions runner via its file-based protocol.

GitHub Actions exposes two append-only files through env vars: ``GITHUB_OUTPUT``
(step outputs, later read as ``steps.<id>.outputs.<name>``) and
``GITHUB_STEP_SUMMARY`` (Markdown shown on the run page). Both are absent outside
Actions, so every writer here is a no-op when its env var is unset — the engine
stays runnable locally and under test without special-casing the environment.
"""

import os
from pathlib import Path

_OUTPUT_DELIMITER = "__EOF__"


def set_output(name: str, value: str) -> None:
    """Set a GitHub Actions step output (no-op outside Actions).

    Uses the heredoc form (``name<<DELIM``) so the value cannot collide with the
    ``name=value`` separator and multi-line values stay intact.
    """
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}<<{_OUTPUT_DELIMITER}\n{value}\n{_OUTPUT_DELIMITER}\n")


def append_step_summary(markdown: str) -> None:
    """Append Markdown to the GitHub Actions job summary (no-op outside Actions)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(markdown + "\n")
