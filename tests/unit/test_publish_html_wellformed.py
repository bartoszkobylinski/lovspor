"""Emitted pages read the same to a parser that knows nothing of lovspor (ADR-0013).

The content-test clause asks that document and section HTML "parse with a
generic HTML parser into the expected heading hierarchy". The generic parser
is the stdlib's; the expectation is read from the corpus Markdown by line,
not from the renderer, so a renderer that lost, reordered or re-levelled a
heading is measured against the source rather than against itself.

Strict nesting is the second half: the stdlib parser is forgiving, so an
unclosed or misnested element would still yield headings. The checker keeps
its own element stack and refuses any end tag that does not close the element
on top of it.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from lovspor.publish.emit import emit_site
from lovspor.publish.inventory import DocumentPlan, build_inventory
from lovspor.snapshot import CorpusSnapshot
from tests.unit.test_publish_emit import corpus  # noqa: F401 — fixture reuse

VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

Outline = list[tuple[int, str]]


class StrictOutline(HTMLParser):
    """Headings inside ``<main>``, and every nesting fault on the page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.faults: list[str] = []
        self.outline: Outline = []
        self._heading: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID:
            return
        if tag in HEADINGS and "main" in self.stack:
            self._heading = []
        self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in VOID:
            self.faults.append(f"self-closed <{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            self.faults.append(f"end tag for void <{tag}>")
            return
        if not self.stack or self.stack[-1] != tag:
            self.faults.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
            return
        self.stack.pop()
        if tag in HEADINGS and self._heading is not None:
            self.outline.append((int(tag[1]), " ".join("".join(self._heading).split())))
            self._heading = None

    def handle_data(self, data: str) -> None:
        if self._heading is not None:
            self._heading.append(data)


def _parse(page: Path) -> StrictOutline:
    parser = StrictOutline()
    parser.feed(page.read_text(encoding="utf-8"))
    parser.close()
    if parser.stack:
        parser.faults.append(f"unclosed at end of page: {parser.stack}")
    return parser


_ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")
_LINKED = re.compile(r"\[([^\]]*)\]\([^)]+\)")
_STARRED = re.compile(r"\*{1,3}(.+?)\*{1,3}")


def _visible(text: str) -> str:
    """What a reader sees of a Markdown heading: link labels, not targets;
    emphasised words, not their stars; an escaped character, not its
    backslash. Escapes are set aside first so ``\\*`` never opens emphasis."""
    literals: list[str] = []

    def keep(match: re.Match[str]) -> str:
        literals.append(match.group(1))
        return f"\x00{len(literals) - 1}\x00"

    text = _STARRED.sub(r"\1", _LINKED.sub(r"\1", _ESCAPED.sub(keep, text)))
    return re.sub("\x00(\\d+)\x00", lambda m: literals[int(m.group(1))], text)


def _source_outline(markdown: str) -> Outline:
    """``#``, ``##`` and ``###`` lines of the body, in order, read without the renderer."""
    body = markdown.split("\n---\n", 1)[1]
    outline: Outline = []
    for line in body.splitlines():
        marks, _, text = line.partition(" ")
        if marks in {"#", "##", "###"}:
            outline.append((len(marks), " ".join(_visible(text).split())))
    return outline


def _built(repo_at: tuple[Path, str], tmp_path: Path) -> tuple[Path, tuple[DocumentPlan, ...]]:
    repo, sha = repo_at
    out = tmp_path / "site"
    emit_site(repo, sha, out)
    snapshot = CorpusSnapshot(repo, sha)
    return out, build_inventory(snapshot.manifest, snapshot.read_text).documents


class TestGenericParserReadsEveryPage:
    def test_no_emitted_page_has_a_nesting_fault(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        out, _ = _built(corpus, tmp_path)
        pages = sorted(out.rglob("*.html"))

        faults = {str(p.relative_to(out)): _parse(p).faults for p in pages}

        assert pages
        assert {page: found for page, found in faults.items() if found} == {}

    def test_document_page_outline_is_the_sources_headings(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        out, plans = _built(corpus, tmp_path)
        repo, _ = corpus

        for plan in plans:
            source = (repo / plan.markdown_path).read_text(encoding="utf-8")
            page = out / plan.route / plan.slug / "index.html"

            assert _parse(page).outline == _source_outline(source), page

    def test_section_page_outline_is_its_one_provision_heading(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        out, plans = _built(corpus, tmp_path)
        repo, _ = corpus
        checked = 0

        for plan in plans:
            if plan.duplicate_pids:
                continue
            source = (repo / plan.markdown_path).read_text(encoding="utf-8")
            provisions = [h for h in _source_outline(source) if h[0] == 3]
            for provision, expected in zip(plan.provisions, provisions, strict=True):
                page = out / plan.route / plan.slug / "paragraf" / provision.pid / "index.html"
                assert _parse(page).outline == [expected], page
                checked += 1

        assert checked >= 5


@pytest.mark.parametrize(
    ("html", "fault"),
    [
        ("<main><p>open</main>", "</main> closes ['p']"),
        ("<main><h3>a</h2></main>", "</h2> closes ['h3']"),
        ("<p>tail", "unclosed at end of page: ['p']"),
        ("<div/>", "self-closed <div/>"),
    ],
)
def test_the_checker_names_the_faults_it_exists_to_catch(
    html: str, fault: str, tmp_path: Path
) -> None:
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")

    assert fault in _parse(page).faults
