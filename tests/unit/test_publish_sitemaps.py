"""Sitemap artifacts are byte-exact goldens (ADR-0013 Decision 7).

The split is pinned twice: the protocol ceiling as a value, and the
chunking behaviour under a shrunk limit — a 50k fixture would prove
the same thing three orders of magnitude slower.
"""

import re

import pytest

from lovspor.publish.inventory import DocumentPlan, PublishInventory
from lovspor.publish.sitemaps import (
    SITEMAP_URL_LIMIT,
    SourceRevision,
    robots_txt,
    sitemap_files,
)

_HEADER = '<?xml version="1.0" encoding="UTF-8"?>\n'
_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def _plan(
    slug: str,
    route: str = "lov",
    pids: tuple[str, ...] = (),
    duplicate_pids: dict[str, int] | None = None,
) -> DocumentPlan:
    folder = "lover" if route == "lov" else "forskrifter"
    return DocumentPlan.model_validate(
        {
            "doc_id": f"doc-{slug}",
            "slug": slug,
            "route": route,
            "title": slug.capitalize(),
            "markdown_path": f"{folder}/{slug}.md",
            "source_dataset": "gjeldende-lover",
            "xml_hash": "a" * 64,
            "renderer_version": 8,
            "language": "nb",
            "ref_id": "lov/2020-01-01-1" if route == "lov" else "forskrift/2020-01-01-1",
            "retrieved_at": "2026-01-01T00:00:00+00:00",
            "date_in_force": None,
            "last_change_in_force": None,
            "provisions": tuple({"pid": pid, "heading_id": pid, "title": None} for pid in pids),
            "duplicate_pids": duplicate_pids or {},
        },
    )


def _revision(day: int) -> SourceRevision:
    return SourceRevision(sha="c" * 40, committed_at=f"2026-02-{day:02d}T00:00:00+00:00")


INVENTORY = PublishInventory(
    documents=(
        _plan("testloven", pids=("1",)),
        _plan("dobbeltloven", pids=("1", "1"), duplicate_pids={"1": 2}),
        _plan("testforskriften", route="forskrift", pids=("1",)),
    ),
)

REVISIONS = {
    "lover/testloven.md": _revision(1),
    "lover/dobbeltloven.md": _revision(2),
    "forskrifter/testforskriften.md": _revision(3),
}


def _urlset(rows: str) -> bytes:
    return f'{_HEADER}<urlset xmlns="{_NS}">\n{rows}</urlset>\n'.encode()


def _locs(payload: bytes) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", payload.decode("utf-8"))


def _sitemapindex(*names: str) -> bytes:
    rows = "".join(f"<sitemap><loc>https://lovspor.no/{name}</loc></sitemap>\n" for name in names)
    return f'{_HEADER}<sitemapindex xmlns="{_NS}">\n{rows}</sitemapindex>\n'.encode()


class TestSitemapFiles:
    def test_the_full_artifact_set_is_byte_exact(self) -> None:
        files = sitemap_files(INVENTORY, REVISIONS)
        assert files == {
            "sitemaps/lover-1.xml": _urlset(
                "<url><loc>https://lovspor.no/lov/dobbeltloven/</loc>"
                "<lastmod>2026-02-02T00:00:00+00:00</lastmod></url>\n"
                "<url><loc>https://lovspor.no/lov/testloven/</loc>"
                "<lastmod>2026-02-01T00:00:00+00:00</lastmod></url>\n",
            ),
            "sitemaps/forskrifter-1.xml": _urlset(
                "<url><loc>https://lovspor.no/forskrift/testforskriften/</loc>"
                "<lastmod>2026-02-03T00:00:00+00:00</lastmod></url>\n",
            ),
            "sitemaps/paragrafer-1.xml": _urlset(
                "<url><loc>https://lovspor.no/forskrift/testforskriften/paragraf/1/</loc></url>\n"
                "<url><loc>https://lovspor.no/lov/testloven/paragraf/1/</loc></url>\n",
            ),
            "sitemaps/indexes.xml": _urlset(
                "<url><loc>https://lovspor.no/lov/</loc></url>\n"
                "<url><loc>https://lovspor.no/forskrift/</loc></url>\n",
            ),
            "sitemap.xml": _sitemapindex(
                "sitemaps/lover-1.xml",
                "sitemaps/forskrifter-1.xml",
                "sitemaps/paragrafer-1.xml",
                "sitemaps/indexes.xml",
            ),
            "sitemaps/companions-1.xml": _urlset(
                "<url><loc>https://lovspor.no/lov/dobbeltloven/index.json</loc>"
                "<lastmod>2026-02-02T00:00:00+00:00</lastmod></url>\n"
                "<url><loc>https://lovspor.no/lov/testloven/index.json</loc>"
                "<lastmod>2026-02-01T00:00:00+00:00</lastmod></url>\n"
                "<url><loc>https://lovspor.no/forskrift/testforskriften/index.json</loc>"
                "<lastmod>2026-02-03T00:00:00+00:00</lastmod></url>\n"
                "<url><loc>https://lovspor.no/forskrift/testforskriften/paragraf/1/"
                "index.json</loc></url>\n"
                "<url><loc>https://lovspor.no/lov/testloven/paragraf/1/index.json</loc></url>\n",
            ),
            "sitemaps/companions.xml": _sitemapindex("sitemaps/companions-1.xml"),
        }

    def test_a_duplicate_pid_document_contributes_no_provision_urls(self) -> None:
        files = sitemap_files(INVENTORY, REVISIONS)
        assert b"dobbeltloven/paragraf" not in files["sitemaps/paragrafer-1.xml"]
        assert b"/lov/dobbeltloven/" in files["sitemaps/lover-1.xml"]

    def test_provision_entries_carry_no_lastmod(self) -> None:
        files = sitemap_files(INVENTORY, REVISIONS)
        assert b"<lastmod>" not in files["sitemaps/paragrafer-1.xml"]
        assert b"<lastmod>" not in files["sitemaps/indexes.xml"]

    def test_the_protocol_ceiling_is_50k(self) -> None:
        assert SITEMAP_URL_LIMIT == 50_000

    def test_shards_split_at_the_limit_in_url_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("lovspor.publish.sitemaps.SITEMAP_URL_LIMIT", 1)
        files = sitemap_files(INVENTORY, REVISIONS)
        assert b"/forskrift/testforskriften/paragraf/1/" in files["sitemaps/paragrafer-1.xml"]
        assert b"/lov/testloven/paragraf/1/" in files["sitemaps/paragrafer-2.xml"]
        assert "sitemaps/paragrafer-3.xml" not in files
        index = files["sitemap.xml"].decode()
        assert index.index("paragrafer-1.xml") < index.index("paragrafer-2.xml")

    def test_an_empty_row_set_emits_no_shard(self) -> None:
        inventory = PublishInventory(documents=(_plan("testloven", pids=("1",)),))
        files = sitemap_files(inventory, {"lover/testloven.md": _revision(1)})
        assert "sitemaps/forskrifter-1.xml" not in files
        assert b"forskrifter" not in files["sitemap.xml"]


class TestCompanionSitemaps:
    """The JSON twins are crawlable, and the page index stays a page index (#340).

    ADR-0013 Decision 7 says the sitemap URL set equals the emitted indexable
    *page* set, and ``emit``'s own test asserts that equality. Listing the
    twins inside ``sitemap.xml`` would have broken it, so they get their own
    index beside it. A crawler reaches that index through ``robots.txt``,
    which is the sitemap protocol's own way to declare a sitemap whose URLs
    lie outside its path.
    """

    def test_the_page_index_names_only_page_sitemaps(self) -> None:
        index = sitemap_files(INVENTORY, REVISIONS)["sitemap.xml"]

        assert b"companions" not in index

    def test_every_emitted_page_has_its_twin_listed_once(self) -> None:
        files = sitemap_files(INVENTORY, REVISIONS)
        pages = _locs(files["sitemaps/lover-1.xml"])
        pages += _locs(files["sitemaps/forskrifter-1.xml"])
        pages += _locs(files["sitemaps/paragrafer-1.xml"])
        twins = _locs(files["sitemaps/companions-1.xml"])

        assert sorted(twins) == sorted(f"{page}index.json" for page in pages)

    def test_the_browse_indexes_have_no_twin_and_are_not_listed(self) -> None:
        """They are written by ``_write``, not ``_write_page``: no index.json
        exists beside them, so listing one would advertise an absent file."""
        files = sitemap_files(INVENTORY, REVISIONS)

        assert "https://lovspor.no/lov/index.json" not in _locs(files["sitemaps/companions-1.xml"])

    def test_a_duplicate_pid_document_contributes_no_provision_twin(self) -> None:
        twins = _locs(sitemap_files(INVENTORY, REVISIONS)["sitemaps/companions-1.xml"])

        assert "https://lovspor.no/lov/dobbeltloven/index.json" in twins
        assert not [twin for twin in twins if "dobbeltloven/paragraf" in twin]

    def test_a_twin_carries_exactly_the_lastmod_its_page_carries(self) -> None:
        """The twin changes when its page changes, so it inherits the page's
        answer and invents none: the document's own corpus commit time, and
        nothing at all for a provision (Decision 7's rule, unchanged)."""
        shard = sitemap_files(INVENTORY, REVISIONS)["sitemaps/companions-1.xml"].decode()

        assert (
            "<loc>https://lovspor.no/lov/testloven/index.json</loc>"
            "<lastmod>2026-02-01T00:00:00+00:00</lastmod>"
        ) in shard
        assert "<loc>https://lovspor.no/lov/testloven/paragraf/1/index.json</loc></url>" in shard

    def test_the_companion_index_lists_every_companion_shard(self) -> None:
        files = sitemap_files(INVENTORY, REVISIONS)

        assert files["sitemaps/companions.xml"] == _sitemapindex("sitemaps/companions-1.xml")

    def test_companion_shards_split_at_the_limit_in_url_order(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("lovspor.publish.sitemaps.SITEMAP_URL_LIMIT", 2)
        files = sitemap_files(INVENTORY, REVISIONS)

        assert files["sitemaps/companions.xml"] == _sitemapindex(
            "sitemaps/companions-1.xml",
            "sitemaps/companions-2.xml",
            "sitemaps/companions-3.xml",
        )
        assert len(_locs(files["sitemaps/companions-1.xml"])) == 2

    def test_a_corpus_with_no_documents_emits_no_companion_artifacts(self) -> None:
        files = sitemap_files(PublishInventory(documents=()), {})

        assert "sitemaps/companions-1.xml" not in files
        assert "sitemaps/companions.xml" not in files


class TestRobots:
    def test_robots_txt_is_byte_exact(self) -> None:
        assert robots_txt() == (
            b"User-agent: *\nAllow: /\nDisallow: /mcp\n"
            b"Sitemap: https://lovspor.no/sitemap.xml\n"
            b"Sitemap: https://lovspor.no/sitemaps/companions.xml\n"
        )

    def test_robots_is_a_constant_of_the_engine_not_of_the_corpus(self) -> None:
        """It declares two indexes by name, so a corpus that grows by a
        hundred thousand twins still moves no byte of this file (#340)."""
        assert robots_txt() == robots_txt()
        assert b"companions-1.xml" not in robots_txt()
