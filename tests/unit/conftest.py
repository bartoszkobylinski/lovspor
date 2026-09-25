"""Fixtures shared across the unit modules."""

import locale
import os
import sys
from collections.abc import Iterator

import pytest

NARROW_CONSOLE_COLUMNS = "20"


@pytest.fixture(autouse=True)
def narrow_console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test sees a 20-column console, whatever terminal runs the suite.

    Rich sizes help and usage-error panels from ``COLUMNS`` and folds a token
    that does not fit mid-word, so an assertion on rendered output can pass at
    the width a developer or runner happens to have and fail at another (#295).
    Pinning the width narrow makes every such assertion fail here, on every
    run, instead of whenever a runner's width or a message's length changes —
    the comparison has to go through ``tests.unit.cli_output`` or below the
    renderer to pass at all.
    """
    monkeypatch.setenv("COLUMNS", NARROW_CONSOLE_COLUMNS)


@pytest.fixture
def c_locale() -> Iterator[None]:
    """The C locale, whose codec is ASCII, for the duration of one test.

    A unit started without ``LANG`` runs under it, and every file the
    release procedure reads is UTF-8 whatever the process locale says —
    the test that asks for this fixture is what says so.
    """
    if sys.flags.utf8_mode:
        pytest.skip("UTF-8 mode pins the locale codec to UTF-8")
    previous = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "C")
    assert locale.getencoding().lower() in {"us-ascii", "ansi_x3.4-1968", "ascii"}
    yield
    locale.setlocale(locale.LC_CTYPE, previous)


@pytest.fixture
def strict_umask() -> Iterator[None]:
    """``umask 077`` for one test: every file written inherits ``0600`` unless told otherwise.

    ``docs/mcp.md`` tells the operator to ``umask 077`` in the shell that
    writes the probe credential — the shell the release commands are then
    run from. What Caddy must read has to be world-readable regardless.
    """
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)
