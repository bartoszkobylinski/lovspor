# ruff: noqa: RUF001, RUF002
"""Document-layer classification: main vs vedlegg vs veileder (RC3).

The fixtures keep the EN DASH the real corpus writes in vedlegg headings
(«Vedlegg 1 – Helsekrav» in førerkortforskriften) — the rule must match
the authentic shape, hence the file-level RUF001 waiver.

Synthetic act text only. The defect: an embedded «Veileder til …» chapter
repeats the act's § headings as commentary, and the parser counted them as
duplicate act sections — LLHB's C5 review caught two such false positives
(byggeforskrift-for-longyearbyen). The fix is purely additive: every
section still parses and keeps its occurrence; a ``layer`` field lets
consumers separate provisions from commentary echoes.
"""

from pathlib import Path

import pytest

from lovspor.mcp import CorpusReader, _layer_for_chapter, _parse_sections
from tests.unit.llhb_fixtures import build_corpus

_LAYERED_BODY = """## Kapittel 1. Alminnelige bestemmelser

### § 1. Krav til byggverk

Byggverk skal prosjekteres forsvarlig etter reglene i denne forskriften.

### § 2. Søknadsplikt

Tiltak krever søknad og tillatelse fra myndigheten før oppstart.

## Vedlegg 1 – Tekniske krav

### § 1. Spesielle tekniske krav

Egne tekniske krav for særskilte byggverk følger av dette vedlegget.

## Veileder til forskriften

### § 2. Søknadsplikt

Denne veiledningsteksten forklarer hvordan § 2 er å forstå i praksis.
"""


@pytest.fixture
def reader(tmp_path: Path) -> CorpusReader:
    return build_corpus(
        tmp_path,
        {"lagdelt-forskriften": ("Forskrift om lagdeling (lagdeltforskriften)", _LAYERED_BODY)},
    )


def test_layer_rule_is_conservative() -> None:
    assert _layer_for_chapter("Kapittel 1. Alminnelige bestemmelser") == "main"
    assert _layer_for_chapter("") == "main"
    assert _layer_for_chapter("Vedlegg 1 – Helsekrav") == "vedlegg"
    assert _layer_for_chapter("VEDLEGG I. Prosedyrer") == "vedlegg"
    assert _layer_for_chapter("Veileder til byggeforskrift") == "veileder"
    # Unclassifiable stays main — mislabelling a provision as commentary
    # is the harmful direction.
    assert _layer_for_chapter("Del A") == "main"
    assert _layer_for_chapter("Overgangsbestemmelser") == "main"


def test_parse_sections_assigns_layers_and_keeps_occurrences() -> None:
    sections = _parse_sections(_LAYERED_BODY)
    rows = [(s["section_id"], s["occurrence"], s["layer"]) for s in sections]
    assert rows == [
        ("1", 1, "main"),
        ("2", 1, "main"),
        ("1", 2, "vedlegg"),
        ("2", 2, "veileder"),
    ]


def test_list_sections_exposes_layer(reader: CorpusReader) -> None:
    rows = reader.list_sections("lagdelt-forskriften")
    assert [(r["section_id"], r["occurrence"], r["layer"]) for r in rows] == [
        ("1", 1, "main"),
        ("2", 1, "main"),
        ("1", 2, "vedlegg"),
        ("2", 2, "veileder"),
    ]


def test_get_section_exposes_layer_per_occurrence(reader: CorpusReader) -> None:
    main = reader.get_section("lagdelt-forskriften", "2", 1)
    echo = reader.get_section("lagdelt-forskriften", "2", 2)
    assert main["layer"] == "main"
    assert echo["layer"] == "veileder"
    # Addressability is unchanged: the commentary echo still resolves.
    assert "veiledningsteksten" in str(echo["body"])
