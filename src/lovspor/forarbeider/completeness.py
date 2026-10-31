"""Whether the bytes of a saved page reach the end of the page.

lxml repairs what it reads: a file cut after the first chapter parses into a
tidy one-item list, so a cut has to be found in the source text, before any
parse. A page that passes is complete as saved. That says the file was not
cut, not that regjeringen.no lists every chapter the proposition has.
"""

import re

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason

_DOCUMENT_END = re.compile(r"</body\s*>\s*</html\s*>\s*\Z", re.IGNORECASE)
_NAVIGATION = re.compile(r"<ul\b[^<>]*\bdata-longdoc-table-of-content\b[^<>]*>", re.IGNORECASE)
_FOOTER = re.compile(r"<footer\b", re.IGNORECASE)
_LIST_TAG = re.compile(r"<(/?)(ul|li)\b[^<>]*>", re.IGNORECASE)


def require_whole_page(text: str) -> None:
    """Refuse a page that stops early, or whose chapter navigation is not closed.

    The footer catches a cut list given well-formed closing tags afterwards.

    A missing navigation passes: :mod:`lovspor.forarbeider.page` names that.

    Raises:
        ForarbeiderRefusedError: ``incomplete_page``.
    """
    if _DOCUMENT_END.search(text) is None:
        raise ForarbeiderRefusedError(RefusalReason.INCOMPLETE_PAGE, "document end")
    for navigation in _NAVIGATION.finditer(text):
        if not _closes(text, navigation.start()):
            raise ForarbeiderRefusedError(RefusalReason.INCOMPLETE_PAGE, "chapter list")
        if _FOOTER.search(text, navigation.end()) is None:
            raise ForarbeiderRefusedError(RefusalReason.INCOMPLETE_PAGE, "page footer")


def _closes(text: str, start: int) -> bool:
    """Every list and item from ``start`` is closed in the source, in order."""
    opened: list[str] = []
    for tag in _LIST_TAG.finditer(text, start):
        name = tag[2].lower()
        if not tag[1]:
            opened.append(name)
        elif not opened or opened.pop() != name:
            return False
        elif not opened:
            return True
    return False
