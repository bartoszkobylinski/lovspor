"""Phase 4B (#861): an intro outside the canonical shapes binds its act only on proof.

Every intro sentence below is quoted from a pinned archive member (``lovtidend-avd1-2001-2025``,
sha256 ``d55f8a38…``); the member is named beside it. The markup around them is synthetic.
The proof is the member's own header: the act an intro cites by day, month, year and number
binds only when ``changesToDocuments`` lists it. Without that, or with a second citation, an
amendment of an amendment, a short title alone or a malformed date, nothing binds.
"""

import pytest

from lovspor.legaltime.document import parse_document
from lovspor.legaltime.instructions import Operation, interpret

STRAFFELOVEN = "lov/2005-05-20-28"
MILITAER = "lov/1902-05-22-13"
STRAFFEPROSESS = "lov/1981-05-22-25"
STRAFFELOV_1902 = "lov/1902-05-22-10"
INTRO_3 = "I lov 20. mai 2005 nr. 28 om straff gjøres følgende endringer:"
INTRO_4 = (  # lov/2015-06-19-65 § 4
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "lov 22. mai 1902 nr. 13 militær straffelov:"
)
INTRO_5 = (  # lov/2015-06-19-65 § 5
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "lov 22. mai 1981 nr. 25 om rettergangsmåten i straffesaker:"
)
INTRO_6 = (
    "Med virkning fra den tid straffeloven 2005 trer i kraft gjøres følgende endringer i "
    "annen lovgivning:"
)


def _law(body: str, changes: tuple[str, ...]) -> bytes:
    items = "".join(f"<li>{refid}</li>" for refid in changes)
    return (
        '<html><body><header><dl><dd class="refid">lov/2015-06-19-65</dd>'
        '<dd class="dateInForce">2015-06-19</dd>'
        '<dd class="dateOfPublication">2015-06-19 15:00</dd>'
        '<dd class="title">Lov om ikraftsetting av straffeloven 2005</dd>'
        f'<dd class="changesToDocuments"><ul>{items}</ul></dd>'
        f"</dl></header><main>{body}</main></body></html>"
    ).encode()


def _ledd(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _article(key: str, *children: str) -> str:
    return f'<article class="legalArticle" data-name="{key}">{"".join(children)}</article>'


def _operations(body: str, *changes: str) -> tuple[Operation, ...]:
    document = parse_document(_law(body, changes), "lti/2015/nl-20150619-065.xml")
    return interpret(document).operations


def _acts(body: str, *changes: str) -> list[tuple[str | None, str | None]]:
    return [(op.address.section, op.act) for op in _operations(body, *changes)]


SECTION_3 = _article("§3", _ledd(INTRO_3), _p("§ 40 oppheves."))
SECTION_4 = _article(
    "§4", _ledd(INTRO_4), _p("§ 13 første ledd skal lyde:"), _ledd("Straffelovens første Del."),
    _p("§ 40 oppheves."),
)  # fmt: skip
SECTION_5 = _article("§5", _ledd(INTRO_5), _p("§ 273 oppheves."))
PROOF = (STRAFFELOVEN, MILITAER, STRAFFEPROSESS)


def test_militaer_straffelov_and_straffeprosessloven_bind_their_own_act_not_straffeloven() -> None:
    """lov/2015-06-19-65 §§ 4-5: the cited act binds, never the act § 3 named before it."""
    acts = _acts(SECTION_3 + SECTION_4 + SECTION_5, *PROOF)
    assert acts == [
        ("§40", STRAFFELOVEN),
        ("§13", MILITAER),
        ("§40", MILITAER),
        ("§273", STRAFFEPROSESS),
    ]


def test_the_section_4_intro_itself_is_not_an_operation() -> None:
    operations = _operations(SECTION_3 + SECTION_4, *PROOF)
    assert all(op.instruction != INTRO_4 for op in operations)


@pytest.mark.parametrize(
    "changes",
    [(), (STRAFFELOVEN,), (STRAFFELOVEN, STRAFFEPROSESS)],
    ids=["no-header", "only-straffeloven", "other-act-only"],
)
def test_without_the_header_proof_section_4_stays_unattributed(changes: tuple[str, ...]) -> None:
    acts = _acts(SECTION_3 + SECTION_4, *changes)
    assert acts[1:] == [("§13", None), ("§40", None)]


@pytest.mark.parametrize(
    ("intro", "act"),
    [
        # lov/2013-06-21-85: a title before the citation
        ("I Almindelig borgerlig Straffelov 22. mai 1902 nr. 10 gjøres følgende endringer:",
         STRAFFELOV_1902),
        # lov/2007-06-29-83: a short title before the citation
        ("I straffegjennomføringsloven 18. mai 2001 nr. 21 gjøres følgende endringer:",
         "lov/2001-05-18-21"),
        # lov/2005-12-21-131: nynorsk, title-led, ``vert endra slik``
        ("Straffelova 22. mai 1902 nr. 10 vert endra slik:", STRAFFELOV_1902),
        # lov/2007-04-13-14: full title, citation, short title in brackets
        ("Lov om rettergangsmåten i straffesaker 22. mai 1981 nr. 25 (straffeprosessloven) "
         "endres slik:", STRAFFEPROSESS),
        # lov/2007-06-15-21: reordered nynorsk intro
        ("Frå det tidspunktet lova tek til å gjelde, blir desse endringane i lov 8. november "
         "1991 nr. 76 om kommunale og fylkeskommunale eldreråd gjort gjeldande:",
         "lov/1991-11-08-76"),
        # lov/2025-04-25-12: a commencement clause before the verb
        ("Fra den tid Kongen fastsetter, gjøres følgende endringer i lov 26. juni 1992 nr. 86 "
         "om tvangsfullbyrdelse:", "lov/1992-06-26-86"),
        # lov/2016-05-27-14: capitalised ``Lov`` after an item number
        ("33. I Lov 29. juni 2007 nr. 75 om verdipapirhandel gjøres følgende endringer:",
         "lov/2007-06-29-75"),
        # lov/2009-06-19-95: an item number closed by a bracket
        ("3) I lov 4. juli 2003 nr. 84 om private skolar med rett til statstilskot gjøres "
         "følgende endringer:", "lov/2003-07-04-84"),
        # lov/2012-06-22-49: nynorsk ``skal desse endringane gjerast``
        ("I almindelig borgerlig straffelov 22. mai 1902 nr. 10 skal desse endringane gjerast:",
         STRAFFELOV_1902),
        # lov/2007-06-29-83: ``lov om straff`` before the citation, one amendment
        ("I lov om straff 20. mai 2005 nr. 28 (straffeloven) gjøres følgende endring:",
         STRAFFELOVEN),
    ],
)  # fmt: skip
def test_a_proven_intro_outside_the_canonical_shapes_binds_its_act(intro: str, act: str) -> None:
    body = SECTION_3 + _article("§4", _ledd(intro), _p("§ 41 oppheves."))
    assert _acts(body, STRAFFELOVEN, act) == [("§40", STRAFFELOVEN), ("§41", act)]
    assert _acts(body) == [("§40", STRAFFELOVEN), ("§41", None)]  # § 3's intro needs no proof


@pytest.mark.parametrize(
    ("intro", "changes"),
    [
        # lov/2008-06-20-47: the source misprints the date; lov/2005-05-22-28 is not proven
        ("I straffeloven 22. mai 2005 nr. 28 gjøres følgende endringer:", (STRAFFELOVEN,)),
        # lov/2010-12-10-76: an amendment of an amending act's part II, not of domstolloven
        ("I romertall II om endringer i lov 13. august 1915 nr. 5 om domstolene gjøres "
         "følgende endringer:", ("lov/1915-08-13-5",)),
        # lov/2020-03-27-16: no full stop after the day, so no citation
        ("I lov 15 mai 2008 nr. 35 om utlendingers adgang til riket og deres opphold her gjøres "
         "følgende endringer:", ("lov/2008-05-15-35",)),
        # lov/2008-03-07-4: a short title with a year is not an identity
        ("I straffeloven 2005 tredje del gjøres følgende endringer:", (STRAFFELOVEN,)),
        # lov/2003-07-04-85: full and short title, no citation
        ("Lov om frittståande skolar (friskolelova) endres slik:", ("lov/2003-07-04-84",)),
        # lov/2022-12-20-110: an amendment to another amendment list, short title only
        ("§ 13-3 nr. 6 om endringer i merverdiavgiftsloven:", ("lov/2009-06-19-58",)),
        # two citations: which act is amended is not unique
        ("I tvisteloven 17. juni 2005 nr. 90, slik den lyder etter lov 1. januar 2020 nr. 1, "
         "gjøres følgende endringer:", ("lov/2005-06-17-90", "lov/2020-01-01-1")),
        # independent review round 3: the one citation is the source of a wording, not the act
        ("I straffeloven, slik den lyder etter lov 20. desember 2023 nr. 110, gjøres følgende "
         "endringer:", ("lov/2023-12-20-110",)),
        ("I straffeloven (jf. lov 20. desember 2023 nr. 110) gjøres følgende endringer:",
         ("lov/2023-12-20-110",)),
        ("Straffeloven som endret ved lov 20. desember 2023 nr. 110 endres slik:",
         ("lov/2023-12-20-110",)),
        # a unit of the cited act leads: Phase 4A closes it, Phase 4B does not bind it
        ("I kapittel 3 i lov 22. mai 1902 nr. 13 gjøres følgende endringer:", (MILITAER,)),
    ],
)  # fmt: skip
def test_an_unproven_or_ambiguous_intro_binds_nothing(intro: str, changes: tuple[str, ...]) -> None:
    body = SECTION_3 + _article("§3b", _ledd(intro), _p("§ 41 oppheves."))
    assert _acts(body, STRAFFELOVEN, *changes) == [("§40", STRAFFELOVEN), ("§41", None)]


def test_an_embedded_amendment_of_another_act_stays_closed_with_the_header_proof() -> None:
    """lov/2007-04-13-14 IV: straffeprosessloven text inside straffeloven § 102 nr. 4."""
    body = _article(
        "§3", _ledd("Lov 20. mai 2005 nr. 28 om straff endres slik:"),
        _ledd("I § 102 nr. 4 (endringer i lov 22. mai 1981 nr. 25 om rettegangsmåten i "
              "straffesaker) tilføyes:"),
        _p("§ 68 sjette ledd skal lyde:"), _ledd("Anke over tingrettens avgjørelse."),
    )  # fmt: skip
    assert [op.act for op in _operations(body, STRAFFELOVEN, STRAFFEPROSESS)] == [None]


FUSED = (  # lov/2014-05-09-16: the intro is printed at the end of the previous payload ledd
    "(1) Utdeling til aksjeeierne kan ikke finne sted før selskapets forpliktelser er dekket. "
    "17. I lov 13. juni 1997 nr. 45 om allmennaksjeselskaper gjøres følgende endringer:"
)


def test_an_intro_fused_into_a_payload_ledd_binds_its_own_act() -> None:
    body = _article(
        "§1", _ledd("I lov 13. juni 1997 nr. 44 om aksjeselskaper gjøres følgende endringer:"),
        _p("§ 8-1 skal lyde:"), _ledd(FUSED), _p("§ 3-8 første ledd nr. 6 skal lyde:"),
        _ledd("Ny."),
    )  # fmt: skip
    assert _acts(body, "lov/1997-06-13-44", "lov/1997-06-13-45") == [
        ("§8-1", "lov/1997-06-13-44"),
        ("§3-8", "lov/1997-06-13-45"),
    ]
    assert _acts(body, "lov/1997-06-13-44") == [("§8-1", "lov/1997-06-13-44"), ("§3-8", None)]


MULTI = (  # lov/2007-12-21-119 § 18-2: a list intro, a whole-act repeal and an intro in one ledd
    "Fra den tid loven trer i kraft, gjøres følgende endringer i andre lover: Lov 10. juni 1966 "
    "nr. 5 om toll (tolloven) oppheves. I lov 19. juni 1969 nr. 66 om merverdiavgift "
    "(merverdiavgiftsloven) gjøres følgende endringer:"
)


def test_several_acts_in_one_ledd_bind_only_the_last_intro_never_the_repealed_act() -> None:
    body = _article("§18-2", _ledd(MULTI), _p("§ 62 første ledd annet punktum skal lyde:"))
    body += _ledd("Ny.")
    proof = ("lov/1966-06-10-5", "lov/1969-06-19-66")
    assert [op.act for op in _operations(body, *proof)] == ["lov/1969-06-19-66"]


def test_the_sentences_before_a_fused_intro_in_a_paragraph_are_read_as_before() -> None:
    """The head is a commencement sentence: a clause, as Phase 4A read it, never an op of mval."""
    body = _article("§18-2", _p(MULTI), _p("§ 62 første ledd annet punktum skal lyde:"))
    document = parse_document(
        _law(body, ("lov/1966-06-10-5", "lov/1969-06-19-66")), "lti/2015/nl-20150619-065.xml"
    )
    reading = interpret(document)
    assert [(op.kind, op.act) for op in reading.operations] == [
        ("replace_punktum", "lov/1969-06-19-66")
    ]
    assert any("tolloven" in clause for clause in reading.clauses)


LIST = "Fra den tid loven trer i kraft, gjøres følgende endringer i andre lover:"
ITEMS = (  # lov/2017-06-16-51 § 44
    _p("1. Lov 25. september 1992 nr. 107 om kommuner og fylkeskommuner:"),
    _p("§ 48 nr. 5 tredje og fjerde punktum oppheves."),
    _p("2. Lov 23. mai 1997 nr. 31 om eierseksjoner § 3 a skal lyde:"),
    _ledd("Eierseksjoner kan ikke kjøpes."),
    _p("7. Lov 17. juni 2005 nr. 62 om arbeidsmiljø-, arbeidstid og stillingsvern"),
    _p("§ 13-1 fjerde ledd skal lyde:"),
    _ledd("(4) Ved diskriminering."),
)
LIST_PROOF = ("lov/1992-09-25-107", "lov/1997-05-23-31", "lov/2005-06-17-62")


def test_numbered_act_headings_under_a_list_intro_bind_each_their_own_act() -> None:
    body = _article("§44", _ledd(LIST), *ITEMS)
    assert [(op.address.section, op.act) for op in _operations(body, *LIST_PROOF)] == [
        (None, "lov/1992-09-25-107"),  # ``nr. 5 … punktum`` is outside the grammar: unsupported
        ("§3a", "lov/1997-05-23-31"),  # the item's own act, named inline before its instruction
        ("§13-1", "lov/2005-06-17-62"),
    ]


def test_an_item_naming_its_act_inline_never_inherits_the_previous_items_act() -> None:
    """Intro fused with an instruction: without proof it is unattributed, never item 1's act."""
    body = _article("§44", _ledd(LIST), *ITEMS[:4])
    proven = [op.act for op in _operations(body, *LIST_PROOF)]
    assert proven == ["lov/1992-09-25-107", "lov/1997-05-23-31"]
    unproven = [op.act for op in _operations(body, "lov/1992-09-25-107")]
    assert unproven == ["lov/1992-09-25-107", None]


@pytest.mark.parametrize(
    ("item", "act"),
    [
        # lov/2019-03-15-6 kap. 12: tvisteloven, named and instructed in one sentence
        ("19. Lov 17. juni 2005 nr. 90 om mekling og rettergang i sivile tvister § 34-4 fjerde "
         "ledd annet punktum skal lyde:", "lov/2005-06-17-90"),
        # lov/2013-06-21-100: lower-case ``lov`` after the item number
        ("23. lov 26. mars 2010 nr. 9 om vergemål skal § 39 første ledd bokstav e lyde:",
         "lov/2010-03-26-9"),
    ],
)  # fmt: skip
def test_a_numbered_item_naming_and_instructing_its_act_binds_it_on_proof(
    item: str, act: str
) -> None:
    body = SECTION_3 + _article("§3", _p(item), _ledd("Ny tekst."))
    assert [op.act for op in _operations(body, STRAFFELOVEN, act)][-1] == act
    assert [op.act for op in _operations(body, STRAFFELOVEN)][-1] is None


@pytest.mark.parametrize(
    "item",
    [
        # lov/2016-04-22-3: a verb in the title part, so the § that follows is not the instruction
        "28. I lov 28. mai 2010 nr. 16 om behandling av opplysninger i politiet og "
        "påtalemyndigheten erstattes i § 40 nr. 7 bokstav e ordet «tvunget» av «tvungent».",
        # lov/2023-12-20-110: an amendment of an amending act
        "13. Lov 18. juni 2010 nr. 25 om endring i lov 16. juli 1999 nr. 66 § 3 oppheves.",
        # unnumbered: a payload or a title, never an instruction heading
        "Lov 17. juni 2005 nr. 90 om mekling § 34-4 skal lyde:",
    ],
)
def test_an_inline_item_outside_the_shape_binds_nothing(item: str) -> None:
    proof = ("lov/2010-05-28-16", "lov/2010-06-18-25", "lov/1999-07-16-66", "lov/2005-06-17-90")
    body = _article("§1", _ledd(LIST), _p(item), _p("§ 41 oppheves."))
    assert {op.act for op in _operations(body, *proof)} == {None}


def test_a_new_title_payload_naming_the_act_keeps_the_block() -> None:
    """lov/2007-06-29-92: ``Ny tittel på lova skal lyde:`` + ``Lov 4. juli 2003 nr. 84 …``."""
    friskolelova = "lov/2003-07-04-84"
    body = _article(
        "§1",
        _ledd("I lov 4. juli 2003 nr. 84 om frittståande skolar (friskolelova) blir det gjort "
              "følgjande endringar:"),
        _p("Ny tittel på lova skal lyde:"),
        _p("Lov 4. juli 2003 nr. 84 om private skolar med rett til statstilskot (privatskolelova)"),
        _p("§ 1-1 første ledd skal lyde:"), _ledd("Formålet med denne lova."),
    )  # fmt: skip
    operations = _operations(body, friskolelova)
    assert {op.act for op in operations} == {friskolelova}
    assert operations[-1].address.section == "§1-1"


def test_a_numbered_act_heading_outside_a_list_intro_binds_nothing() -> None:
    body = SECTION_3 + _article("§4", *ITEMS[:2])
    assert _acts(body, STRAFFELOVEN, *LIST_PROOF)[1:] == [(None, None), (None, None)]


def test_a_numbered_heading_that_repeals_its_act_is_not_a_heading() -> None:
    """lov/2005-06-17-62 § 20-2: ``1. Lov … oppheves.`` repeals; what follows is not that act's."""
    body = _article(
        "§20-2", _ledd("Fra lovens ikrafttredelse gjøres følgende endringer i andre lover:"),
        _p("1. Lov 4. februar 1977 nr. 4 om arbeidervern og arbeidsmiljø m.v. oppheves."),
        _p("§ 5 oppheves."),
    )  # fmt: skip
    assert {op.act for op in _operations(body, "lov/1977-02-04-4")} == {None}


def test_an_article_boundary_then_an_unknown_intro_leaves_everything_after_unattributed() -> None:
    body = _article("§4", _ledd(INTRO_4), _p("§ 40 oppheves."))
    body += _article("§5", _ledd(INTRO_6), _p("§ 41 oppheves."))
    body += _article("§6", _p("§ 42 oppheves."))
    assert _acts(body, *PROOF) == [("§40", MILITAER), ("§41", None), ("§42", None)]


def test_a_unit_only_intro_inside_a_proven_block_keeps_its_act() -> None:
    body = _article("§4", _ledd(INTRO_4), _p("§ 102 nr. 1 vert endra slik:"), _p("§ 40 oppheves."))
    assert {op.act for op in _operations(body, *PROOF) if op.kind != "unsupported"} == {MILITAER}


@pytest.mark.parametrize(
    "payload",
    [
        "Overtredelse straffes etter lov 22. mai 1981 nr. 25 om rettergangsmåten i straffesaker.",
        "Lov 22. mai 1981 nr. 25 om rettergangsmåten i straffesaker gjelder tilsvarende.",
        "Endringene i lov 22. mai 1981 nr. 25 gjelder ikke for saker reist før ikrafttredelsen.",
    ],
)
def test_a_payload_naming_another_act_does_not_rebind_the_block(payload: str) -> None:
    body = _article(
        "§4", _ledd(INTRO_4), _p("§ 13 første ledd skal lyde:"), _ledd(payload),
        _p("§ 40 oppheves."),
    )  # fmt: skip
    operations = _operations(body, *PROOF)
    assert [op.act for op in operations] == [MILITAER, MILITAER]
    assert [ledd.text for ledd in operations[0].ledd] == [payload]


def test_a_transitional_sentence_naming_an_act_is_not_an_intro() -> None:
    body = _article(
        "§4", _ledd(INTRO_4), _p("§ 40 oppheves."),
        _ledd("Endringene i lov 22. mai 1981 nr. 25 gjelder fra samme tid."), _p("§ 41 oppheves."),
    )  # fmt: skip
    assert _acts(body, *PROOF) == [("§40", MILITAER), ("§41", MILITAER)]


@pytest.mark.parametrize(
    "item",
    [
        # lov/2009-06-19-74: credited to straffeloven 2005 at Phase 4A
        "2. Lov 13. desember 1946 nr. 14 om straff for utlendske krigsbrotsmenn oppheves.",
        # lov/2008-03-07-4: an amending act's parts repealed, credited to the previous block
        "12. I lov 13. april 2007 nr. 14 oppheves del IV og V annet punktum.",
        # lov/2013-04-05-12: a date-only citation of an 1898 act
        "1. Lov 28. november 1898 om Umyndiggjørelse oppheves.",
    ],
)
def test_a_numbered_item_naming_another_act_never_inherits_the_open_block(item: str) -> None:
    body = _article("§3", _ledd(INTRO_3), _p("§ 40 oppheves."), _p(item), _p("§ 41 oppheves."))
    assert [op.act for op in _operations(body, STRAFFELOVEN)] == [STRAFFELOVEN, None, None]
