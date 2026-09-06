"""Sitemap artifacts are byte-exact goldens (ADR-0013 Decision 7).

The split is pinned twice: the protocol ceiling as a value, and the
chunking behaviour under a shrunk limit — a 50k fixture would prove
the same thing three orders of magnitude slower.
"""

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
            "sitemap.xml": (
                f'{_HEADER}<sitemapindex xmlns="{_NS}">\n'
                "<sitemap><loc>https://lovspor.no/sitemaps/lover-1.xml</loc></sitemap>\n"
                "<sitemap><loc>https://lovspor.no/sitemaps/forskrifter-1.xml</loc></sitemap>\n"
                "<sitemap><loc>https://lovspor.no/sitemaps/paragrafer-1.xml</loc></sitemap>\n"
                "<sitemap><loc>https://lovspor.no/sitemaps/indexes.xml</loc></sitemap>\n"
                "</sitemapindex>\n"
            ).encode(),
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


class TestRobots:
    def test_robots_txt_is_byte_exact(self) -> None:
        assert robots_txt() == (
            b"User-agent: *\nAllow: /\nDisallow: /mcp\nSitemap: https://lovspor.no/sitemap.xml\n"
        )
