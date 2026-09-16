"""The one stylesheet of both published surfaces (ADR-0014 Decision 5).

The site build and the corpus generator render different markup from
different code, but they are one website, so they share one source of
design tokens and base rules: ``style.css`` beside this module. Before
it existed the CSS was written twice — an inline ``<style>`` in
``_base.html`` and a ``_STYLE`` constant in ``publish/pages.py`` — and
the two drifted into unrelated designs (issue #335).

**Inline, never a linked asset.** ``lovspor.site.scan`` admits a
``<link>`` only as ``canonical`` or ``alternate`` and refuses every
external reference and ``@import``: the published rule is that a page's
only markup is what the trusted template emits, with CSS inline
(ADR-0013 Decision 2). A stylesheet served as its own file would also
make a page's appearance depend on a second request, which the offline
and no-external-request constraints forbid. So the cost is accepted:
the bytes repeat on every page and compress away per response.

The text is read once and cached. It is a constant of the checkout —
the same bytes at the same ``lovspor_commit`` on every machine — so it
leaks nothing into the byte-identical build invariant (ADR-0013
Decision 3).
"""

from functools import cache
from pathlib import Path

STYLE_PATH = Path(__file__).resolve().parent / "style.css"


@cache
def stylesheet() -> str:
    """The shared CSS text, read once from ``style.css``.

    Returned as plain ``str``, never wrapped in ``Markup``. The template
    applies ``| safe`` at the one place it is emitted, so the decision to
    skip escaping is made in the trusted template beside the ``<style>``
    tag rather than manufactured in Python, where both ruff (S704) and
    bandit (B704) would be reading a wrapped value and could no longer
    tell a checkout constant from artifact-sourced text.
    """
    return STYLE_PATH.read_text(encoding="utf-8")
