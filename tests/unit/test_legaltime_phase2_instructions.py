"""Phase 2 (#744): instruction shapes read from the eight harder acts (research note 2026-11-07)."""

from pathlib import Path

import pytest

from lovspor.legaltime import replay
from lovspor.legaltime.commencement import Timing
from lovspor.legaltime.document import Document, parse_document
from lovspor.legaltime.instructions import Address, Operation, classify, interpret
from lovspor.legaltime.replay import Event
from lovspor.legaltime.text import section_key, split_sentences

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "legaltime"
DATED = Timing(status="dated", effective=date(2022, 1, 1)) if False else None  # noqa: F821
from datetime import date  # noqa: E402

DATED = Timing(status="dated", effective=date(2022, 1, 1))
ENACTED = Timing(status="dated", effective=date(2021, 1, 1))


def _amending(
    *articles: str, intro: str = "I lov 14. juni 2019 nr. 99 om prøving gjøres følgende endringer:"
) -> Document:
    body = (
        '<html><body><header><dl><dd class="refid">lov/2021-06-01-7</dd></dl></header>'
        f'<main><section class="section"><h2>I</h2><article class="defaultP">{intro}</article>'
        f"{''.join(articles)}</section></main></body></html>"
    )
    return parse_document(body.encode(), "lti/2021/x.xml")


def _p(text: str) -> str:
    return f'<article class="defaultP">{text}</article>'


def _t(text: str) -> str:
    return f'<article class="legalP">{text}</article>'


def _ops(*articles: str, **kw: str) -> tuple[Operation, ...]:
    return interpret(_amending(*articles, **kw)).operations


def _state(*articles: str, at: date = date(2023, 1, 1), **kw: str):
    events = [
        Event(
            sequence=i,
            law="lov/2021-06-01-7",
            member="m",
            operation=op,
            timing=DATED,
            published_at=None,
        )
        for i, op in enumerate(_ops(*articles, **kw))
    ]
    baseline = parse_document((FIXTURES / "enacting.xml").read_bytes(), "lti/x/enacting.xml")
    return replay.replay(baseline, ENACTED, events, at)


def _ledd(state, key: str) -> list[str]:
    return [ledd.text for p in state.provisions if p.key == key for ledd in p.ledd]


# ---------------------------------------------------------------- sentence splitting


def test_a_numbered_intro_is_one_sentence() -> None:
    assert split_sentences(
        "23. I lov 19. juni 2009 nr. 100 om mangfold gjøres følgende endringer:"
    ) == ["23. I lov 19. juni 2009 nr. 100 om mangfold gjøres følgende endringer:"]
    assert split_sentences("Første. Andre.") == ["Første.", "Andre."]
    assert split_sentences("Loven trer i kraft 1. juli 2021. (2) Resten senere.") == [
        "Loven trer i kraft 1. juli 2021.",
        "(2) Resten senere.",
    ]


# ---------------------------------------------------------------- intro shapes


@pytest.mark.parametrize(
    "intro, act",
    [
        (
            "74. I lov 13. juni 1980 nr. 35 om fri rettshjelp gjøres følgende endringer:",
            "lov/1980-06-13-35",
        ),
        (
            "I lov av 13. mars 1981 nr. 6 om vern mot forurensninger gjøres følgende endringer:",
            "lov/1981-03-13-6",
        ),
        (
            "I lov 19. juni 2009 nr. 58 om merverdiavgift gjer ein følgjande endringar:",
            "lov/2009-06-19-58",
        ),
        (
            "I lov 19. juni 2009 nr. 58 om merverdiavgift blir det gjort følgjande endringar:",
            "lov/2009-06-19-58",
        ),
    ],
)
def test_intro_variants_name_the_act_and_state_no_operation(intro: str, act: str) -> None:
    ops = _ops(
        _p("§ 1 skal lyde:"),
        '<article class="futureLegalArticle" data-name="§1"><article class="legalP">A.</article></article>',  # noqa: E501
        intro=intro,
    )
    assert [(op.kind, op.act) for op in ops] == [("replace_section", act)]


def test_an_intro_with_an_inline_repeal_yields_that_repeal() -> None:
    ops = _ops(intro="3. I lov 21. februar 1930 om skifte oppheves § 12 annet ledd annet punktum.")
    assert [(op.kind, op.act, op.address) for op in ops] == [
        ("repeal_punktum", "lov/1930-02-21", Address(section="§12", ledd=2, punktum=2))
    ]
    whole = _ops(intro="2. I lov 15. desember 1967 nr. 9 om patenter oppheves § 61.")
    assert [(op.kind, op.address.section) for op in whole] == [("repeal_section", "§61")]


# ---------------------------------------------------------------- headings, chapters, keys


@pytest.mark.parametrize(
    "sentence",
    [
        "Overskriften til § 15-6 skal lyde:",
        "Overskriften i § 15-6 skal lyde:",
        "§ 15-6 overskrifta skal lyde:",
        "§ 15-6 paragrafoverskriften skal lyde:",
    ],
)
def test_heading_variants(sentence: str) -> None:
    kind, groups = classify(sentence)
    assert (kind, section_key(groups["sec"] or "")) == ("replace_heading", "§15-6")


def test_a_new_section_placed_in_a_chapter_is_an_insertion() -> None:
    kind, groups = classify("I kapittel 5 skal ny § 5-9 lyde:")
    assert (kind, section_key(groups["sec"] or "")) == ("insert_section", "§5-9")


def test_section_keys_may_carry_a_capital_letter_inside_the_number() -> None:
    assert section_key("§ 6A-4") == "§6A-4"
    kind, groups = classify("§ 6A-4 tredje ledd skal lyde:")
    assert kind == "replace_ledd" and section_key(groups["sec"] or "") == "§6A-4"


# ---------------------------------------------------------------- repeal and renumber synonyms


@pytest.mark.parametrize(
    "sentence, kind, address",
    [
        (
            "§ 156 første ledd annet punktum strykes.",
            "repeal_punktum",
            Address(section="§156", ledd=1, punktum=2),
        ),
        ("I § 7-2 blir tredje ledd sletta.", "repeal_ledd", Address(section="§7-2", ledd=3)),
        ("§ 7-2 tredje ledd vert sletta.", "repeal_ledd", Address(section="§7-2", ledd=3)),
        ("Tidlegare tredje ledd blir nytt fjerde ledd.", "renumber", Address()),
    ],
)
def test_repeal_and_renumber_synonyms(sentence: str, kind: str, address: Address) -> None:
    ops = _ops(_p(sentence))
    assert [(op.kind, op.address) for op in ops] == [(kind, address)]


def test_a_renumbering_with_tidlegare_is_absorbed_like_naavaerende() -> None:
    ops = _ops(
        _p("§ 1 nytt annet ledd skal lyde:"),
        _t("Nytt."),
        _p("Tidlegare andre ledd blir nytt tredje ledd."),
    )
    assert ops[1].absorbed_by == ops[0].id


# ---------------------------------------------------------------- multi-section repeal


@pytest.mark.parametrize(
    "sentence, keys",
    [
        ("§§ 14 og 15 oppheves.", ["§14", "§15"]),
        ("§§ 14 til 16 oppheves.", ["§14", "§15", "§16"]),
        ("§ 17 til 19 oppheves.", ["§17", "§18", "§19"]),
        ("§§ 2 og 2 a oppheves.", ["§2", "§2a"]),
    ],
)
def test_a_multi_section_repeal_is_one_repeal_per_section(sentence: str, keys: list[str]) -> None:
    ops = _ops(_p(sentence))
    assert [op.kind for op in ops] == ["repeal_section"] * len(keys)
    assert [op.address.section for op in ops] == keys
    assert len({op.id for op in ops}) == len(keys)


def test_a_lettered_range_of_sections_is_refused() -> None:
    assert [op.kind for op in _ops(_p("§§ 2 a til 5 oppheves."))] == ["unsupported"]


def test_multi_section_repeal_applies_to_every_section() -> None:
    state = _state(_p("§§ 2 og 4 oppheves."))
    assert state.unresolved == () and len(state.applied) == 2
    assert all(p.repealed for p in state.provisions if p.key in ("§2", "§4"))


# ---------------------------------------------------------------- siste punktum


def test_siste_punktum_addresses_the_last_sentence() -> None:
    ops = _ops(_p("§ 1 første ledd siste punktum oppheves."))
    assert ops[0].kind == "repeal_punktum" and ops[0].address.last_punktum is True
    state = _state(_p("§ 1 første ledd siste punktum oppheves."))
    assert (
        state.unresolved == () and _ledd(state, "§1")[0] == "Loven gjelder prøving av fylkesmannen."
    )
    replaced = _state(_p("§ 1 første ledd siste punktum skal lyde:"), _t("Den gjelder også klage."))
    assert (
        _ledd(replaced, "§1")[0] == "Loven gjelder prøving av fylkesmannen. Den gjelder også klage."
    )


def test_siste_punktum_on_a_ledd_without_sentences_is_refused() -> None:
    ops = _ops(_p("§ 1 tredje ledd siste punktum oppheves."))
    state = _state(_p("§ 1 tredje ledd siste punktum oppheves."))
    assert ops[0].kind == "repeal_punktum" and len(state.unresolved) == 1


# ---------------------------------------------------------------- arbeidsmiljøloven's (1) ledd


@pytest.mark.parametrize(
    "sentence, kind, address",
    [
        ("§ 12-5 (1) skal lyde:", "replace_ledd", Address(section="§12-5", ledd=1)),
        ("§ 12-5 (1) lyde:", "replace_ledd", Address(section="§12-5", ledd=1)),
        (
            "§ 14-9 (1) bokstav f skal lyde:",
            "replace_bokstav",
            Address(section="§14-9", ledd=1, bokstav="f"),
        ),
        ("§ 1-8 nytt (3) skal lyde:", "insert_ledd", Address(section="§1-8", ledd=3)),
        ("§ 1-8 (3) oppheves.", "repeal_ledd", Address(section="§1-8", ledd=3)),
        (
            "§ 10-4 (2) og (3) skal lyde:",
            "replace_ledd",
            Address(section="§10-4", ledd=2, ledd_end=3),
        ),
        (
            "§ 10-4 (2) annet punktum skal lyde:",
            "replace_punktum",
            Address(section="§10-4", ledd=2, punktum=2),
        ),
    ],
)
def test_numbered_ledd_in_parentheses(sentence: str, kind: str, address: Address) -> None:
    ops = _ops(_p(sentence), _t("x."))
    assert [(op.kind, op.address) for op in ops] == [(kind, address)]
