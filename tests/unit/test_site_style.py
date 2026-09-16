"""One stylesheet source for both surfaces (issue #335, ADR-0014 Decision 5).

The design tokens and base rules live in exactly one file. The site build
reads it as a template global and the corpus generator inlines it, so the
question these tests answer is the one the defect was: can the two
surfaces disagree about what the site looks like? They can only if a
second copy exists, so a second copy is what is forbidden here.
"""

import re

from lovspor.site.style import STYLE_PATH, stylesheet

# Anything that would make a page's appearance depend on a second request.
_FETCHES = re.compile(r"@import|url\(|https?:", re.IGNORECASE)


class TestOneSource:
    def test_the_source_is_the_file_beside_the_module(self) -> None:
        assert STYLE_PATH.is_file()
        assert STYLE_PATH.name == "style.css"
        assert stylesheet() == STYLE_PATH.read_text(encoding="utf-8")

    def test_it_is_not_empty(self) -> None:
        assert stylesheet().strip()


class TestDeterminism:
    def test_repeated_reads_give_the_same_bytes(self) -> None:
        """A page is a pure function of its inputs; the stylesheet is a
        constant of the checkout, not a thing read differently per call."""
        assert stylesheet().encode("utf-8") == stylesheet().encode("utf-8")
        assert stylesheet() is stylesheet()


class TestNoExternalRequest:
    def test_the_stylesheet_fetches_nothing(self) -> None:
        """No web font, no CDN, no ``url()``: the pages render offline, and
        ``lovspor.site.scan`` refuses ``@import`` or an external ``url()``
        in a ``<style>`` anyway."""
        assert not _FETCHES.search(stylesheet())
