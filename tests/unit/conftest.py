"""Fixtures shared across the unit modules."""

import locale
import os
import sys
from collections.abc import Iterator

import pytest


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
