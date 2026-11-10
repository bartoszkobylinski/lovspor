"""Phase 4B (#861): guards the PR-scoped mutation run on PR #873 showed were missing.

Each test fails on one surviving mutant of ``_Reader`` or ``attribution`` (named in its
docstring) and passes on the code. Sentences are real archive sentences or near-matches.
"""

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import Interpretation, interpret

STRAFFELOVEN = "lov/2005-05-20-28"
STRAFFELOV_1902 = "lov/1902-05-22-10"
MILITAER = "lov/1902-05-22-13"
TITLED = "I Almindelig borgerlig Straffelov 22. mai 1902 nr. 10 gjøres følgende endringer:"
INTRO_4 = (
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "lov 22. mai 1902 nr. 13 militær straffelov:"
)
LIST = "Fra den tid loven trer i kraft, gjøres følgende endringer i andre lover:"
HEADING = "1. Lov 25. september 1992 nr. 107 om kommuner og fylkeskommuner:"


def _law(body: str, changes: tuple[str, ...]) -> bytes:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    return (
        '<html><body><header><dl><dd class="refid">lov/2013-06-21-85</dd>'
        '<dd class="dateInForce">2013-06-21</dd>'
        '<dd class="dateOfPublication">2013-06-21 15:00</dd>'
        '<dd class="title">Lov om endringer i straffeloven</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _part(label: str, *children: str) -> str:
    return f'<section class="section"><h2>{label}</h2>{"".join(children)}</section>'


def _article(key: str, *children: str) -> str:
    return f'<article class="legalArticle" data-name="{key}">{"".join(children)}</article>'


def _read(body: str, *changes: str) -> Interpretation:
    return interpret(parse_document(_law(body, changes), "lti/2013/nl-20130621-085.xml"))


def _acts(body: str, *changes: str) -> list[tuple[str, str | None]]:
    return [(op.kind, op.act) for op in _read(body, *changes).operations]


def test_a_part_starts_outside_any_list_intro() -> None:
    """``_read_part``: ``self.in_list = True`` would let a heading bind with no list intro."""
    body = _part("I", _p(HEADING), _p("§ 48 oppheves."))
    assert _acts(body, "lov/1992-09-25-107") == [("unsupported", None), ("repeal_section", None)]


def test_a_paragraph_intro_counts_as_the_instruction_seen_before_loose_text() -> None:
    """``_read_part``: ``seen_instruction = False/None`` makes later prose a commencement clause."""
    body = _part("I", _p(TITLED), _ledd("Endringene i Almindelig borgerlig Straffelov gjelder."))
    assert _read(body, STRAFFELOV_1902).clauses == ()


def test_a_proven_block_does_not_count_prose_at_the_start_of_the_next_part() -> None:
    """``_read_part``: ``unbound = True`` after binding leaks into the next part's opening prose."""
    body = _part("I", _ledd(TITLED), _p("§ 40 oppheves.")) + _part("II", _p("Slutt."))
    assert _acts(body, STRAFFELOV_1902) == [("repeal_section", STRAFFELOV_1902)]


def test_an_inline_item_does_not_count_prose_at_the_start_of_the_next_part() -> None:
    """``_inline``: ``unbound = True`` after an inline item binds leaks into the next part."""
    item = (
        "19. Lov 17. juni 2005 nr. 90 om mekling og rettergang i sivile tvister § 34-4 skal lyde:"
    )
    body = _part("I", _p(item), _ledd("Ny tekst.")) + _part("II", _p("Slutt."))
    assert _acts(body, "lov/2005-06-17-90") == [("replace_section", "lov/2005-06-17-90")]


def test_an_unproven_inline_item_is_one_unsupported_operation_naming_nothing() -> None:
    """``inline``: ``… or True`` returns (None, rest) and reads the bare ``§`` as an instruction."""
    item = "2. Lov 23. mai 1997 nr. 31 om eierseksjoner § 3 a skal lyde:"
    body = _article("§44", _ledd(LIST), _p(item), _ledd("Eierseksjoner."), _p("Slutt."))
    operations = _read(body).operations
    assert [(op.kind, op.act, op.instruction) for op in operations] == [
        ("unsupported", None, item),
        ("unsupported", None, "Slutt."),
    ]


def test_a_proven_intro_inside_a_substitution_list_still_binds() -> None:
    """``_boundary``: ``_binding(text, None)`` treats every intro in a list as one of its items."""
    body = _article(
        "§1",
        _p("I følgende bestemmelser skal ordet «fengsel» endres til «straff»:"),
        _ledd(TITLED), _p("§ 40 oppheves."),
    )  # fmt: skip
    assert _acts(body, STRAFFELOV_1902)[-1] == ("repeal_section", STRAFFELOV_1902)


def test_a_commencement_shaped_paragraph_intro_is_kept_as_its_clause() -> None:
    """``_bound`` / ``bind``: ``clauses.append(None)`` or a dropped ``intro`` lose the sentence."""
    body = _article("§4", _p(INTRO_4), _p("§ 40 oppheves."))
    reading = _read(body, MILITAER)
    assert INTRO_4 in reading.clauses
    assert [(op.kind, op.act) for op in reading.operations] == [("repeal_section", MILITAER)]


def test_every_sentence_before_a_fused_paragraph_intro_is_read_in_order() -> None:
    """``bind``: joining the head with ``"XX XX"`` or keeping only ``sentences[:1]`` loses one."""
    fused = f"§ 5 oppheves. § 6 oppheves. {TITLED}"
    operations = _read(_article("§1", _p(fused), _p("§ 40 oppheves.")), STRAFFELOV_1902).operations
    assert [(op.address.section, op.act) for op in operations] == [
        ("§5", None),
        ("§6", None),
        ("§40", STRAFFELOV_1902),
    ]


def test_an_unproven_shaped_intro_the_block_grammar_misses_still_closes_the_block() -> None:
    """``bind``: ``opens=False`` (or omitted) leaves the open block's act on what follows.

    ``tolltariffen`` is no act word and ``skal desse endringane gjerast`` is no
    ``_UNBOUND_INTRO`` shape, so only the Phase 4B shape closes this block.
    """
    intro = "I tolltariffen 1. januar 2000 nr. 1 skal desse endringane gjerast:"
    body = _article(
        "§3", _ledd("I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"),
        _p("§ 40 oppheves."), _ledd(intro), _p("§ 41 oppheves."),
    )  # fmt: skip
    assert _acts(body, STRAFFELOVEN) == [
        ("repeal_section", STRAFFELOVEN),
        ("repeal_section", None),
    ]
