"""The ADR-0009 T0 serving path: not-in-force notices on get_law / get_section.

Composition happens in the MCP tool wrappers, never in CorpusReader — the
corpus bytes and the reader are untouched, per the owner ruling (serving =
``g(temporal_layer, evaluation_time)``). Fixtures use pending-commencement
notes on purpose: their classification cannot flip with wall-clock time, so
the tool-level tests stay deterministic without freezing the clock.
"""

import asyncio
from datetime import date
from pathlib import Path

from lovspor.mcp import CorpusReader, _with_section_notice, build_server
from tests.unit.test_mcp import _record, _seed_corpus

EVAL = date(2026, 8, 14)

PENDING_NOTE = (
    "> **Vert endra** ved lov [19 juni 2026 nr. 48](lov/2026-06-19-48) "
    "(i kraft frå den tid Kongen bestemmer).\n"
)

LAW_WITH_PENDING = (
    "# Testloven\n\n"
    "## Kapittel 1. Innledning\n\n"
    "### § 4-2. Arbeidsmiljø\n\n"
    "Lovtekst her.\n\n" + PENDING_NOTE
)

LAW_CLEAN = (
    "# Testloven\n\n"
    "## Kapittel 1. Innledning\n\n"
    "### § 4-2. Arbeidsmiljø\n\n"
    "Lovtekst her.\n\n"
    "> Endret ved lov [21 juni 2002 nr. 41](lov/2002-06-21-41) (ikr. 1 jan 2003).\n"
)


def _corpus(tmp_path: Path, body: str) -> None:
    _seed_corpus(
        tmp_path,
        {"nl-1": _record(slug="testloven", title="Testloven")},
        body_for={"testloven": body},
    )


def _call_tool(tmp_path: Path, name: str, args: dict[str, object]) -> object:
    server = build_server(tmp_path)

    async def run() -> object:
        return await server._tool_manager.call_tool(name, args, context=None, convert_result=False)

    return asyncio.run(run())


class TestWithSectionNotice:
    def test_attaches_events_with_default_provision(self) -> None:
        result = {"section_id": "4-2", "body": f"Lovtekst.\n\n{PENDING_NOTE}"}
        out = _with_section_notice(result, EVAL)
        notice = out["temporal_notice"]
        assert notice is not None
        assert notice["evaluation_date"] == "2026-08-14"
        assert len(notice["events"]) == 1
        assert notice["events"][0]["provision"] == "§ 4-2"
        assert notice["events"][0]["marker_class"] == "pending_indeterminate"

    def test_key_present_and_null_when_clean(self) -> None:
        body = (
            "Lovtekst.\n\n"
            "> Endret ved lov [21 juni 2002 nr. 41](lov/2002-06-21-41) (ikr. 1 jan 2003).\n"
        )
        out = _with_section_notice({"section_id": "4-2", "body": body}, EVAL)
        assert "temporal_notice" in out
        assert out["temporal_notice"] is None


class TestGetLawTool:
    def test_appends_notice_after_the_law_text(self, tmp_path: Path) -> None:
        _corpus(tmp_path, LAW_WITH_PENDING)
        raw = CorpusReader(tmp_path).get_law("testloven")
        served = _call_tool(tmp_path, "get_law", {"slug": "testloven"})
        assert isinstance(served, str)
        assert "Temporal notice" not in raw
        assert served.startswith(raw.rstrip())
        assert "Temporal notice" in served
        assert "Kongen bestemmer" in served.split("Temporal notice")[1]

    def test_clean_law_is_served_byte_identical(self, tmp_path: Path) -> None:
        _corpus(tmp_path, LAW_CLEAN)
        raw = CorpusReader(tmp_path).get_law("testloven")
        served = _call_tool(tmp_path, "get_law", {"slug": "testloven"})
        assert served == raw


class TestGetSectionTool:
    def test_carries_notice_and_leaves_body_untouched(self, tmp_path: Path) -> None:
        _corpus(tmp_path, LAW_WITH_PENDING)
        served = _call_tool(tmp_path, "get_section", {"slug": "testloven", "section_id": "4-2"})
        assert isinstance(served, dict)
        notice = served["temporal_notice"]
        assert notice is not None
        assert notice["events"][0]["provision"] == "§ 4-2"
        assert notice["events"][0]["announced"] is True
        assert "Temporal notice" not in served["body"]

    def test_clean_section_has_null_notice(self, tmp_path: Path) -> None:
        _corpus(tmp_path, LAW_CLEAN)
        served = _call_tool(tmp_path, "get_section", {"slug": "testloven", "section_id": "4-2"})
        assert isinstance(served, dict)
        assert served["temporal_notice"] is None

    def test_notice_does_not_leak_from_the_following_section(self, tmp_path: Path) -> None:
        law = (
            "# Testloven\n\n"
            "## Kapittel 1. Innledning\n\n"
            "### § 1. Clean section\n\n"
            "Gjeldende lovtekst.\n\n"
            "### § 2. Pending section\n\n"
            f"Fremtidig lovtekst.\n\n{PENDING_NOTE}"
        )
        _corpus(tmp_path, law)

        clean = _call_tool(tmp_path, "get_section", {"slug": "testloven", "section_id": "1"})
        pending = _call_tool(tmp_path, "get_section", {"slug": "testloven", "section_id": "2"})

        assert isinstance(clean, dict)
        assert clean["temporal_notice"] is None
        assert isinstance(pending, dict)
        assert pending["temporal_notice"] is not None
        assert pending["temporal_notice"]["events"][0]["provision"] == "§ 2"

    def test_carries_never_in_force_marker_for_headingless_section_body(
        self, tmp_path: Path
    ) -> None:
        law = (
            "# Testloven\n\n"
            "## Kapittel 1. Innledning\n\n"
            "### § 4-2. Arbeidsmiljø\n\n"
            "Første ledd gjelder.\n\n"
            "Tredje ledd er ikke satt i kraft.\n"
        )
        _corpus(tmp_path, law)

        served = _call_tool(tmp_path, "get_section", {"slug": "testloven", "section_id": "4-2"})

        assert isinstance(served, dict)
        notice = served["temporal_notice"]
        assert notice is not None
        assert notice["events"] == []
        assert notice["never_in_force"][0]["provision"] == "§ 4-2"
