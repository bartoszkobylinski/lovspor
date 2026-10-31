"""Reading a saved regjeringen.no chapter page, and refusing the rest (#738)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.models import Chapter
from lovspor.forarbeider.page import build_record, read_proposition

FIXTURES = Path(__file__).parents[1] / "fixtures" / "forarbeider"
L_URL = "https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"
LS_URL = "https://www.regjeringen.no/no/dokumenter/prop.-900-ls-20992100/id9000002/"
RETRIEVED = datetime(2100, 4, 1, 12, tzinfo=UTC)


def _l_html() -> str:
    return (FIXTURES / "prop-l.html").read_text(encoding="utf-8")


def _refusal(html: str) -> ForarbeiderRefusedError:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(html.encode(), f"{L_URL}?ch=1")
    return caught.value


def test_prop_l_fixture_reads_exactly() -> None:
    facts = read_proposition((FIXTURES / "prop-l.html").read_bytes(), f"{L_URL}?ch=1")
    assert facts.locator.reference.document_type == "prop-l"
    assert facts.locator.reference.session == "2099-2100"
    assert facts.locator.reference.proposition_number == 901
    assert facts.locator.document_id == 9000001
    assert facts.title == "Strukturell testproposisjon om et oppdiktet lovforslag"
    assert facts.ministry == "Testdepartementet"
    assert facts.chapters == (
        Chapter(chapter_number=1, title="1 Innledning", url=f"{L_URL}?ch=1"),
        Chapter(chapter_number=2, title="2 Bakgrunn", url=f"{L_URL}?ch=2"),
        Chapter(chapter_number=3, title="Tilråding", url=f"{L_URL}?ch=3"),
        Chapter(chapter_number=4, title="A Forslag til lov om noe oppdiktet", url=f"{L_URL}?ch=4"),
        Chapter(chapter_number=5, title="1 Oppdiktet vedlegg", url=f"{L_URL}?ch=5"),
    )


def test_prop_ls_fixture_is_a_distinct_type() -> None:
    facts = read_proposition((FIXTURES / "prop-ls.html").read_bytes(), f"{LS_URL}?ch=2")
    assert facts.locator.reference.document_type == "prop-ls"
    assert facts.locator.reference.proposition_number == 900
    assert facts.locator.chapter == 2
    assert facts.title.endswith("og samtykke")


def test_record_carries_the_supplied_inputs_and_fixed_labels() -> None:
    payload = (FIXTURES / "prop-l.html").read_bytes()
    record = build_record(payload, f"{L_URL}?ch=1", RETRIEVED)
    assert record.id == "regjeringen-9000001"
    assert record.source_document_type == "forarbeid"
    assert record.normative is False
    assert record.source_url == L_URL
    assert record.source_page_url == f"{L_URL}?ch=1"
    assert record.retrieved_at == RETRIEVED
    assert record.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert record.parser_version == 1
    assert record.schema_version == 1
    assert record.source_provider == "regjeringen.no"
    assert record.rights_basis == "åndsverkloven § 14"
    assert record.source_license is None
    assert record.title == "Strukturell testproposisjon om et oppdiktet lovforslag"
    assert record.ministry == "Testdepartementet"
    assert record.session == "2099-2100"
    assert record.proposition_number == 901
    assert record.document_type == "prop-l"
    assert len(record.chapters) == 5


def test_dc_date_is_not_a_publication_date() -> None:
    """Both samples show DC.Date is the tilråding date, so it proves no publication."""
    html = _l_html()
    assert 'name="DC.Date"' in html
    assert build_record(html.encode(), f"{L_URL}?ch=1", RETRIEVED).published_at is None


def test_no_date_at_all_is_null() -> None:
    html = _l_html().replace('<meta name="DC.Date" content="2100-03-27">', "")
    assert build_record(html.encode(), f"{L_URL}?ch=1", RETRIEVED).published_at is None


def test_ministry_is_null_when_nothing_states_one() -> None:
    html = (
        _l_html()
        .replace('<meta name="DC.Creator" content="Testdepartementet">', "")
        .replace('<div><a href="/no/dep/test/id1/">Testdepartementet</a></div>', "")
    )
    assert read_proposition(html.encode(), f"{L_URL}?ch=1").ministry is None


def test_ministry_stated_once_is_enough() -> None:
    meta_only = _l_html().replace(
        '<div><a href="/no/dep/test/id1/">Testdepartementet</a></div>', ""
    )
    owner_only = _l_html().replace('<meta name="DC.Creator" content="Testdepartementet">', "")
    assert read_proposition(meta_only.encode(), f"{L_URL}?ch=1").ministry == "Testdepartementet"
    assert read_proposition(owner_only.encode(), f"{L_URL}?ch=1").ministry == "Testdepartementet"


def test_subheader_is_optional_but_may_not_disagree() -> None:
    start = _l_html().index('<h2 class="subheader')
    end = _l_html().index("</h2>", start) + len("</h2>")
    html = _l_html()[:start] + _l_html()[end:]
    title = "Strukturell testproposisjon om et oppdiktet lovforslag"
    assert read_proposition(html.encode(), f"{L_URL}?ch=1").title == title


def test_landing_url_is_refused_because_it_has_no_navigation() -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(_l_html().encode(), L_URL)
    assert caught.value.reason is RefusalReason.CHAPTER_PAGE_REQUIRED
    assert caught.value.detail == L_URL


@pytest.mark.parametrize(
    "old_new",
    [
        (
            'property="og:url" content="https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000001/"',
            'property="og:url" content="https://www.regjeringen.no/no/dokumenter/prop.-901-l-20992100/id9000009/"',
        ),
        (
            'name="DC.Identifier.URL" content="/no/dokumenter/prop.-901-l-20992100/id9000001/"',
            'name="DC.Identifier.URL" content="/no/dokumenter/prop.-901-l-20992100/id9000009/"',
        ),
        ("<h1>Prop. 901 L (2099&#x2013;2100)</h1>", "<h1>Prop. 902 L (2099&#x2013;2100)</h1>"),
        (
            'property="og:title" content="Prop. 901 L (2099&#x2013;2100)"',
            'property="og:title" content="Prop. 902 L (2099&#x2013;2100)"',
        ),
        ('name="DC.Title" content="Prop. 901 L', 'name="DC.Title" content="Prop. 902 L'),
        ('name="DC.Creator" content="Testdepartementet"', 'name="DC.Creator" content="Annet"'),
        (
            '<div><a href="/no/dep/test/id1/">Testdepartementet</a></div>',
            '<div><a href="/a">Testdepartementet</a></div><div><a href="/b">Annet</a></div>',
        ),
        ("Strukturell testproposisjon om et oppdiktet lovforslag</h2>", "Annen tittel</h2>"),
        (
            '<meta name="DC.Type" content="Proposisjon">',
            '<meta name="DC.Type" content="Proposisjon"><meta name="DC.Type" content="NOU">',
        ),
    ],
    ids=[
        "og-url",
        "dc-url",
        "h1",
        "og-title",
        "dc-title",
        "creator",
        "owners",
        "subheader",
        "meta",
    ],
)
def test_contradiction_is_refused(old_new: tuple[str, str]) -> None:
    assert old_new[0] in _l_html()
    assert _refusal(_l_html().replace(*old_new)).reason is RefusalReason.CONFLICTING_IDENTITY


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        ('content="Proposisjon"', 'content="NOU"', "unsupported_document_type"),
        ('<meta name="DC.Type" content="Proposisjon">', "", "dom_changed"),
        ('property="og:title"', 'property="x"', "dom_changed"),
        ('property="og:url"', 'property="x"', "source_identity_missing"),
        ('name="DC.Identifier.URL"', 'name="x"', "source_identity_missing"),
        ('name="DC.Title"', 'name="x"', "title_missing"),
        (
            'content="Prop. 901 L (2099\u20132100) - Strukturell testproposisjon om et '
            'oppdiktet lovforslag"',
            'content="Prop. 901 L (2099\u20132100)"',
            "title_missing",
        ),
        ('class="content-header"', 'class="other"', "dom_changed"),
        ("<h1>Prop. 901 L (2099&#x2013;2100)</h1>", "", "dom_changed"),
        (
            "<h1>Prop. 901 L (2099&#x2013;2100)</h1>",
            "<h1>Prop. 901 L (2099&#x2013;2100)</h1><h1>Prop. 901 L (2099&#x2013;2100)</h1>",
            "dom_changed",
        ),
        ("<h1>Prop. 901 L (2099&#x2013;2100)</h1>", "<h1>Dokumenter</h1>", "reference_missing"),
        (
            "<h1>Prop. 901 L (2099&#x2013;2100)</h1>",
            "<h1>Prop. 901 S (2099&#x2013;2100)</h1>",
            "unsupported_document_type",
        ),
        ("data-longdoc-table-of-content", "data-other", "chapter_navigation_missing"),
    ],
    ids=[
        "type",
        "no-type",
        "og-title",
        "og-url",
        "dc-url",
        "dc-title",
        "bare-title",
        "header",
        "no-h1",
        "two-h1",
        "nav-text-h1",
        "prop-s",
        "no-nav",
    ],
)
def test_structure_refusals(old: str, new: str, reason: str) -> None:
    assert old in _l_html()
    assert _refusal(_l_html().replace(old, new)).reason == RefusalReason(reason)


def _nav(*items: str) -> str:
    start = _l_html().index('<ul class="longdoc-table-of-content"')
    end = _l_html().index("</nav>", start)
    links = "".join(f"<li>{item}</li>" for item in items)
    head = '<ul class="longdoc-table-of-content" data-longdoc-table-of-content>'
    return _l_html()[:start] + head + links + "</ul>" + _l_html()[end:]


def _link(chapter: int, title: str = "Tittel") -> str:
    return f'<a href="/no/dokumenter/prop.-901-l-20992100/id9000001/?ch={chapter}">{title}</a>'


@pytest.mark.parametrize(
    ("items", "reason", "detail"),
    [
        ((), "chapter_navigation_missing", "chapter list"),
        (("Vedlegg",), "chapter_navigation_missing", "chapter list"),
        ((_link(1), _link(1, "Annen")), "chapter_duplicate", "1"),
        ((_link(2),), "chapter_sequence", "2"),
        ((_link(1), _link(3)), "chapter_sequence", "3"),
        ((_link(2), _link(1)), "chapter_sequence", "2"),
        ((_link(1, " "),), "chapter_navigation_missing", "chapter title"),
        (("<a>Tittel</a>",), "chapter_navigation_missing", "chapter link"),
    ],
    ids=["empty", "label", "duplicate", "starts-late", "gap", "disorder", "blank", "no-href"],
)
def test_chapter_inventory_refusals(items: tuple[str, ...], reason: str, detail: str) -> None:
    refusal = _refusal(_nav(*items))
    assert refusal.reason == RefusalReason(reason)
    assert refusal.detail == detail


@pytest.mark.parametrize(
    "href",
    [
        "/no/dokumenter/prop.-901-l-20992100/id9000002/?ch=1",
        "https://evil.test/no/dokumenter/prop.-901-l-20992100/id9000001/?ch=1",
    ],
)
def test_foreign_chapter_link_is_refused(href: str) -> None:
    refusal = _refusal(_nav(f'<a href="{href}">Tittel</a>'))
    assert refusal.reason is RefusalReason.CHAPTER_FOREIGN


def test_nested_section_links_are_not_chapters() -> None:
    facts = read_proposition(_l_html().encode(), f"{L_URL}?ch=2")
    assert [chapter.title for chapter in facts.chapters if chapter.title.startswith("2.")] == []


def test_two_chapter_lists_are_refused() -> None:
    marker = '<nav class="longdoc-nav">'
    extra = "<ul data-longdoc-table-of-content></ul>"
    assert _refusal(_l_html().replace(marker, extra + marker)).reason is RefusalReason.DOM_CHANGED


@pytest.mark.parametrize("payload", [b"", b"   ", b"\xff\xfe", b"<?xml version='1.0'?><html/>"])
def test_unreadable_payload_is_refused(payload: bytes) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        read_proposition(payload, f"{L_URL}?ch=1")
    assert caught.value.reason in {RefusalReason.UNREADABLE_PAGE, RefusalReason.DOM_CHANGED}


def test_refusal_never_quotes_page_text() -> None:
    secret = "Ola Nordmann fra høringen"
    html = _l_html().replace("<h1>Prop. 901 L (2099&#x2013;2100)</h1>", f"<h1>{secret}</h1>")
    assert secret not in str(_refusal(html))


def test_same_input_gives_equal_records() -> None:
    payload = _l_html().encode()
    assert build_record(payload, f"{L_URL}?ch=1", RETRIEVED) == build_record(
        payload, f"{L_URL}?ch=1", RETRIEVED
    )
