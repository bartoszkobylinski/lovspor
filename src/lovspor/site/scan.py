"""Post-render scans of a built page (ADR-0014 Decisions 3 and 4).

Every page the generator renders passes through these before a byte is
written; a finding fails the build, because it is a defect in repository
content, never something to patch in the output.

* **Numerals** (ADR:1123-1128): any digit in the page text outside the
  fact mechanism fails, naming the page and the text. Scanned: the
  ``<body>`` text, the ``<title>`` and ``meta[name=description]``.
  Excluded: ``<style>`` and every subtree under a ``data-fact`` (a
  ledgered value) or ``data-literal`` (a marked example or quotation —
  "folketrygdloven § 8-18" is text, not a fact about Lovspor) element.
  Attributes are not scanned; ``<code>`` and ``<pre>`` are not exempt,
  because a typed number in a code block is still a typed number.
* **No script** (ADR:2409-2411): no ``<script>``, no ``on*`` attribute,
  no ``javascript:`` or ``data:`` reference, no external ``<link>``,
  ``<img>`` or ``@import`` — the only markup is what the trusted
  template emits, CSS inline. A ``<link>`` is ``canonical`` or
  ``alternate`` and nothing else.
* **Canonical and links** (ADR:620-641): ``rel=canonical`` names the
  page itself; ``hreflang`` alternates are reciprocal and name emitted
  pages; every internal link resolves to an emitted page or to the
  corpus namespaces the site does not build (``/lov/``, ``/forskrift/``,
  ``/site-manifest.json``). External links in body copy are body copy;
  the chrome adds none (Decision 5, enforced by its own tests).

Parsing is ``html.parser`` from the standard library: it is the output
of our own templates that is scanned, not untrusted markup.
"""

import re
from html.parser import HTMLParser

from lovspor.site.errors import SiteBuildError
from lovspor.site.routes import canonical_url

LINK_ALLOWLIST_PREFIXES = ("/lov/", "/forskrift/")
LINK_ALLOWLIST_EXACT = frozenset({"/site-manifest.json"})

_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_FORBIDDEN_TAGS = frozenset({"script", "iframe", "object", "embed"})
_REFERENCE_ATTRIBUTES = ("href", "src", "srcset", "action", "formaction", "data", "poster")
_ALLOWED_LINK_RELS = frozenset({"canonical", "alternate"})
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//", re.IGNORECASE)
# Every attribute through which an element can fetch a resource. ``srcset`` and
# ``imagesrcset`` carry a comma-separated candidate list, each ``url [descriptor]``.
_ASSET_ATTRIBUTES = ("src", "srcset", "imagesrcset", "data", "poster")
# Inside inline SVG, ``href`` (and the legacy ``xlink:href``) fetches a resource on
# these elements rather than linking — the parser lower-cases tag names.
_SVG_ASSET_ATTRIBUTES = {
    "image": ("href", "xlink:href"),
    "use": ("href", "xlink:href"),
    "feimage": ("href", "xlink:href"),
}


def _asset_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [candidate.strip().split()[0] for candidate in value.split(",") if candidate.strip()]


_FORBIDDEN_SCHEME = re.compile(r"^\s*(?:javascript|data):", re.IGNORECASE)
_DIGIT = re.compile(r"\d")
_STYLE_IMPORT = re.compile(r"@import|url\(\s*['\"]?(?:https?:)?//", re.IGNORECASE)
_EXCLUDING_ATTRIBUTES = ("data-fact", "data-literal")

Attributes = dict[str, str | None]


class _PageScanner(HTMLParser):
    """One pass over a page: text to check, head links, body links, findings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text: list[str] = []
        self.descriptions: list[str] = []
        self.canonical: str | None = None
        self.alternate_links: list[tuple[str, str]] = []
        self.links: list[str] = []
        self.findings: list[str] = []
        self._stack: list[tuple[str, bool]] = []
        self._in_body = False

    @property
    def alternates(self) -> dict[str, str]:
        """``hreflang`` -> href, valid only once ``_check_reciprocity`` has ruled out
        a language named twice (a dict would silently keep the last one)."""
        return dict(self.alternate_links)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: Attributes = dict(attrs)
        self._check_element(tag, attributes)
        if tag == "body":
            self._in_body = True
        elif tag == "meta" and attributes.get("name") == "description":
            self.descriptions.append(attributes.get("content") or "")
        elif tag == "link":
            self._link(attributes)
        elif tag == "a" and attributes.get("href") is not None:
            self.links.append(attributes["href"] or "")
        if tag not in _VOID:
            excluded = tag == "style" or any(name in attributes for name in _EXCLUDING_ATTRIBUTES)
            self._stack.append((tag, excluded))

    def handle_endtag(self, tag: str) -> None:
        while self._stack:
            name, _ = self._stack.pop()
            if name == tag:
                break
        if tag == "body":
            self._in_body = False

    def handle_data(self, data: str) -> None:
        if self._in_style():
            if _STYLE_IMPORT.search(data):
                self.findings.append("@import or external url() in <style>")
            return
        if (self._in_body or self._in_title()) and not self._excluded():
            self.text.append(data)

    def _in_style(self) -> bool:
        return bool(self._stack) and self._stack[-1][0] == "style"

    def _in_title(self) -> bool:
        return any(tag == "title" for tag, _ in self._stack)

    def _excluded(self) -> bool:
        return any(excluded for _, excluded in self._stack)

    def _check_element(self, tag: str, attributes: Attributes) -> None:
        if tag in _FORBIDDEN_TAGS:
            self.findings.append(f"<{tag}> element")
        for name, value in attributes.items():
            if name.startswith("on"):
                self.findings.append(f"{name} handler on <{tag}>")
            if name in _REFERENCE_ATTRIBUTES and value and _FORBIDDEN_SCHEME.match(value):
                self.findings.append(f"{name}={value!r} on <{tag}>")
        for name in (*_ASSET_ATTRIBUTES, *_SVG_ASSET_ATTRIBUTES.get(tag, ())):
            for candidate in _asset_urls(attributes.get(name)):
                if _EXTERNAL.match(candidate):
                    self.findings.append(f"external {name}={candidate!r} on <{tag}>")

    def _link(self, attributes: Attributes) -> None:
        rel, href = attributes.get("rel") or "", attributes.get("href") or ""
        if rel not in _ALLOWED_LINK_RELS:
            self.findings.append(f"<link rel={rel!r}> is not canonical or alternate")
        elif rel == "canonical":
            self.canonical = href
        elif attributes.get("hreflang"):
            self.alternate_links.append((attributes["hreflang"] or "", href))


def _scan(markup: str) -> _PageScanner:
    scanner = _PageScanner()
    scanner.feed(markup)
    scanner.close()
    return scanner


def _numerals(page: str, scanner: _PageScanner) -> None:
    pieces = [*scanner.text, *scanner.descriptions]
    for piece in pieces:
        if _DIGIT.search(piece):
            raise SiteBuildError(
                f"numeral outside the fact mechanism in page {page}: {' '.join(piece.split())!r}"
            )


def scan_page(page: str, markup: str) -> None:
    """Fail on a typed numeral, any script or external asset, or a foreign canonical."""
    scanner = _scan(markup)
    if scanner.findings:
        raise SiteBuildError(f"page {page} is outside the no-script rule: {scanner.findings[0]}")
    _numerals(page, scanner)
    if len(scanner.descriptions) != 1:
        raise SiteBuildError(
            f"page {page}: {len(scanner.descriptions)} meta descriptions, expected exactly one"
        )
    if scanner.canonical != canonical_url(page):
        raise SiteBuildError(f"page {page}: rel=canonical is {scanner.canonical!r}, not itself")


def _internal_link_allowed(href: str, emitted: frozenset[str]) -> bool:
    return (
        href in emitted or href.startswith(LINK_ALLOWLIST_PREFIXES) or href in LINK_ALLOWLIST_EXACT
    )


def _check_page_links(page: str, links: list[str], emitted: frozenset[str]) -> None:
    for href in links:
        if href.startswith("#") or _EXTERNAL.match(href) or re.match(r"^[a-z]+:", href):
            continue
        if not href.startswith("/") or not _internal_link_allowed(href, emitted):
            raise SiteBuildError(f"page {page} links to {href!r}, which the tree does not serve")


def _check_reciprocity(page: str, scanners: dict[str, _PageScanner]) -> None:
    links = scanners[page].alternate_links
    if not links:
        return
    languages = [lang for lang, _ in links]
    if len(set(languages)) != len(languages):
        raise SiteBuildError(f"page {page}: hreflang alternates must name each language once")
    alternates = scanners[page].alternates
    own = [lang for lang, href in alternates.items() if href == canonical_url(page)]
    if len(own) != 1:
        raise SiteBuildError(f"page {page}: hreflang alternates do not name the page itself once")
    for lang, href in alternates.items():
        twin = href.removeprefix(canonical_url(""))
        if twin not in scanners or scanners[twin].alternates.get(own[0]) != canonical_url(page):
            raise SiteBuildError(f"page {page}: hreflang {lang} -> {href} is not reciprocal")


def check_links(pages: dict[str, str]) -> None:
    """Across the whole tree: internal links resolve, hreflang pairs are reciprocal."""
    scanners = {page: _scan(markup) for page, markup in pages.items()}
    emitted = frozenset(pages)
    for page, scanner in scanners.items():
        _check_page_links(page, scanner.links, emitted)
        _check_reciprocity(page, scanners)
