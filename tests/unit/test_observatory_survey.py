"""Reading what a municipal site offers a crawler, before it is registered.

Pre-registration recon (issue #349): the 2026-08-20 sweep over all 358
municipalities produced the figures quoted in `commands.py:302` and its output
was never persisted, so the population is not re-derivable. This module is the
part of that work that decides *what a probe means*; every fixture here is
hand-written, because ADR-0010 §5 keeps observed material out of this
repository.

The four entries a planner cares about are distinguished deliberately.
"Nothing machine-readable" and "assembled in the browser" look identical from a
sitemap's absence alone, and #194 is the whole reason they must not be folded
together: the second population has an entry point, just not one a sitemap
reader can see.
"""

from lovspor.observatory.survey import (
    RobotsReadout,
    front_page_markers,
    read_site_shape,
)

DOMAIN = "example.invalid"


def _robots(
    *,
    readable: bool = True,
    allows_root: bool = True,
    sitemaps: tuple[str, ...] = (),
) -> RobotsReadout:
    return RobotsReadout(readable=readable, allows_root=allows_root, declared_sitemaps=sitemaps)


class TestWhatTheFrontPageReveals:
    def test_the_two_markers_from_issue_194_are_reported_verbatim(self) -> None:
        payload = (
            b'<html><head><link href="/kunde/grensesnitt/i18n/nb_front.json"></head>'
            b'<body><script>fetch("/api/presentation/page/1")</script></body></html>'
        )

        assert front_page_markers(payload) == ("/api/presentation/", "/kunde/grensesnitt/")

    def test_markers_are_evidence_not_a_vendor_claim_so_only_what_is_present_is_named(
        self,
    ) -> None:
        payload = b'<html><body><script>fetch("/api/presentation/page/1")</script></body></html>'

        assert front_page_markers(payload) == ("/api/presentation/",)

    def test_a_page_with_neither_marker_yields_nothing(self) -> None:
        assert front_page_markers(b"<html><body><h1>Kommune</h1></body></html>") == ()

    def test_undecodable_bytes_are_not_an_error_because_a_probe_must_not_raise(self) -> None:
        """A recon pass over 358 hosts cannot stop on one server's broken encoding."""
        assert front_page_markers(b"\xff\xfe/api/presentation/") == ("/api/presentation/",)


class TestTheEntryAPlannerGets:
    def test_a_declared_sitemap_is_the_entry(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(sitemaps=("https://example.invalid/sitemap.xml",)),
            conventional_sitemap=False,
            front_page=b"",
        )

        assert shape.entry == "declared_sitemap"

    def test_an_undeclared_sitemap_at_the_conventional_path_still_counts(self) -> None:
        """190 of 358 serve one without declaring it (commands.py:302)."""
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(),
            conventional_sitemap=True,
            front_page=b"",
        )

        assert shape.entry == "conventional_sitemap"

    def test_no_sitemap_but_api_markers_is_the_194_population_not_a_dead_end(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(),
            conventional_sitemap=False,
            front_page=b'<html><script>fetch("/api/presentation/x")</script></html>',
        )

        assert shape.entry == "browser_assembled"
        assert shape.front_page_markers == ("/api/presentation/",)

    def test_no_sitemap_and_no_markers_is_reported_as_needing_a_human(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(),
            conventional_sitemap=False,
            front_page=b"<html><body>Velkommen</body></html>",
        )

        assert shape.entry == "no_machine_index"

    def test_a_declared_sitemap_wins_over_markers_because_the_index_is_the_cheaper_route(
        self,
    ) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(sitemaps=("https://example.invalid/sitemap.xml",)),
            conventional_sitemap=True,
            front_page=b'<html><script>fetch("/api/presentation/x")</script></html>',
        )

        assert shape.entry == "declared_sitemap"
        assert shape.front_page_markers == ("/api/presentation/",)


class TestWhenRobotsSaysNo:
    def test_an_unreadable_robots_is_its_own_entry_never_folded_into_the_others(self) -> None:
        """`fetch.py:187`: an unreachable robots.txt denies. So does this."""
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(readable=False),
            conventional_sitemap=True,
            front_page=b'<html><script>fetch("/api/presentation/x")</script></html>',
        )

        assert shape.entry == "robots_unreadable"

    def test_a_disallowed_root_is_recorded_as_a_refusal_not_as_an_absent_index(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(allows_root=False),
            conventional_sitemap=False,
            front_page=b"",
        )

        assert shape.entry == "robots_disallowed"

    def test_a_disallowed_root_still_reports_a_declared_sitemap_as_evidence(self) -> None:
        """What the site declares is a fact about it; the refusal is a separate fact."""
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(allows_root=False, sitemaps=("https://example.invalid/sitemap.xml",)),
            conventional_sitemap=False,
            front_page=b"",
        )

        assert shape.entry == "robots_disallowed"
        assert shape.declared_sitemaps == ("https://example.invalid/sitemap.xml",)


class TestTheRecordItself:
    def test_the_domain_is_carried_so_a_row_is_readable_on_its_own(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(),
            conventional_sitemap=True,
            front_page=b"",
        )

        assert shape.domain == DOMAIN

    def test_a_shape_serialises_for_the_survey_log(self) -> None:
        shape = read_site_shape(
            domain=DOMAIN,
            robots=_robots(sitemaps=("https://example.invalid/sitemap.xml",)),
            conventional_sitemap=False,
            front_page=b"",
        )

        assert shape.model_dump(mode="json") == {
            "domain": DOMAIN,
            "entry": "declared_sitemap",
            "robots_readable": True,
            "robots_allows_root": True,
            "declared_sitemaps": ["https://example.invalid/sitemap.xml"],
            "front_page_markers": [],
        }
