"""``/llms.txt``: the machine-readable front door of the site (#340).

Built from the fact ledger rather than typed, the way ``/docs/`` and
``/status/`` already get their numbers: a hand-typed count would drift the
moment the corpus moved, and the ledger records which artifact and field
each reading came from. So the assertions here are about *provenance* as
much as wording — that every digit in the file is a ledgered value.
"""

import re

import pytest

from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import FactLedger, FactRegistry, FactSource, KindMismatchError
from lovspor.site.llms import LLMS_NAME, LLMS_PAGE, llms_txt

MANIFEST = "corpus/site-manifest.json"
DESCRIPTOR = "tool-surface@" + "a" * 40
LICENCE = "NLOD 2.0"
"""The one digit-bearing literal the document is allowed to type."""


def _registry(documents: int = 5914, tools: int = 17) -> FactRegistry:
    return FactRegistry(
        sources=(
            FactSource(
                id="corpus.documents",
                kind="corpus",
                artifact=MANIFEST,
                field="documents",
                value=documents,
            ),
            FactSource(
                id="code.tool_surface.tool_count",
                kind="code",
                artifact=DESCRIPTOR,
                field="tool_count",
                value=tools,
            ),
        )
    )


def _text(registry: FactRegistry | None = None) -> str:
    return llms_txt(registry or _registry(), FactLedger()).decode("utf-8")


class TestTheArtifactItself:
    def test_it_is_the_site_root_convention(self) -> None:
        assert LLMS_NAME == "llms.txt"
        assert LLMS_PAGE == "/llms.txt"

    def test_it_is_deterministic(self) -> None:
        """A site-build artifact: same inputs, same bytes, no clock."""
        assert llms_txt(_registry(), FactLedger()) == llms_txt(_registry(), FactLedger())

    def test_it_is_utf_8_ending_in_exactly_one_newline(self) -> None:
        payload = llms_txt(_registry(), FactLedger())

        assert payload.decode("utf-8").endswith("\n")
        assert not payload.decode("utf-8").endswith("\n\n")

    def test_it_opens_as_an_llms_txt_document(self) -> None:
        text = _text()

        assert text.startswith("# lovspor\n")
        assert "\n> " in text


class TestWhatItNames:
    def test_the_source_and_the_licence(self) -> None:
        text = _text()

        assert "Lovdata" in text
        assert LICENCE in text

    def test_the_twin_convention(self) -> None:
        text = _text()

        assert "index.json" in text
        assert "https://lovspor.no/lov/" in text
        assert "https://lovspor.no/forskrift/" in text

    def test_it_does_not_promise_a_twin_for_pages_that_have_none(self) -> None:
        """Whether a page has a twin is declared, not inferred from its URL: the
        browse indexes are pages and are written without one. Telling an agent to
        append index.json to any page URL would send it to a 404 — and the release
        check refuses a companions sitemap that advertises exactly that file, so
        the instruction would promise what the gate forbids."""
        text = _text()

        assert "any\npage URL" not in text
        assert "Every page has a machine-readable twin" not in text
        assert "/lov/ and /forskrift/" in text

    def test_the_sitemaps_and_the_fact_files(self) -> None:
        text = _text()

        for url in (
            "https://lovspor.no/sitemap.xml",
            "https://lovspor.no/sitemaps/companions.xml",
            "https://lovspor.no/site-manifest.json",
            "https://lovspor.no/site-facts.json",
            "https://lovspor.no/robots.txt",
        ):
            assert url in text, url

    def test_it_names_both_sitemaps_and_calls_neither_one_every_page(self) -> None:
        """A release serves two page sets from two trees, each with its own
        validated sitemap: `sitemap.xml` indexes the corpus shards, while the
        site's own pages are in `sitemap-site.xml`. Calling the first "every
        page" both overstates it and hides the second from the inventory this
        file exists to give an agent."""
        text = _text()

        assert "https://lovspor.no/sitemap.xml — every act, regulation and provision" in text
        assert "https://lovspor.no/sitemap-site.xml" in text
        assert "https://lovspor.no/sitemap.xml — every page" not in text

    def test_the_mcp_endpoint_and_that_it_needs_a_token(self) -> None:
        text = _text()

        assert "https://lovspor.no/mcp" in text
        assert "token" in text
        assert "https://lovspor.no/docs/" in text

    def test_the_limits_that_docs_already_states(self) -> None:
        """The same four refusals as ``/docs/``: no case law, no preparatory
        works, no municipal regulations, and history that answers what the
        corpus held at a date rather than what was legally in force."""
        text = _text().lower()

        assert "case law" in text
        assert "preparatory works" in text
        assert "municipal regulations" in text
        assert "in force" in text
        assert "legal advice" in text


class TestEveryNumberIsLedgered:
    def test_the_counts_are_the_registrys_readings(self) -> None:
        assert "5,914" in _text()
        assert "17" in _text()
        assert "1,234" in _text(_registry(documents=1234, tools=9))

    def test_no_digit_is_typed_into_the_document(self) -> None:
        """Strip every ledgered reading and the one marked licence literal;
        a digit left over is a number someone typed, which is exactly what
        the fact mechanism exists to prevent (ADR-0014 Decision 4)."""
        ledger = FactLedger()
        text = llms_txt(_registry(), ledger).decode("utf-8")

        for entry in ledger.entries:
            assert isinstance(entry.value, int)
            text = text.replace(f"{entry.value:,}", "")
        text = text.replace(LICENCE, "")

        assert not re.search(r"\d", text)

    def test_every_reading_is_recorded_against_this_page(self) -> None:
        ledger = FactLedger()

        llms_txt(_registry(), ledger)

        assert {entry.page for entry in ledger.entries} == {LLMS_PAGE}
        assert {entry.field for entry in ledger.entries} == {"documents", "tool_count"}
        assert {entry.artifact for entry in ledger.entries} == {MANIFEST, DESCRIPTOR}
        assert {entry.kind for entry in ledger.entries} == {"corpus", "code"}

    def test_a_count_read_under_the_wrong_kind_fails_the_build(self) -> None:
        """The document count is a reading of the corpus release, never a
        claim about the checkout's code; mislabelling it must fail here
        rather than render a plausible number under the wrong provenance."""
        registry = FactRegistry(
            sources=(
                FactSource(
                    id="corpus.documents",
                    kind="code",
                    artifact=DESCRIPTOR,
                    field="documents",
                    value=5914,
                ),
            )
        )

        with pytest.raises(KindMismatchError, match="corpus.documents"):
            llms_txt(registry, FactLedger())

    def test_an_unregistered_count_fails_the_build(self) -> None:
        with pytest.raises(SiteBuildError, match="unknown fact"):
            llms_txt(FactRegistry(sources=()), FactLedger())
