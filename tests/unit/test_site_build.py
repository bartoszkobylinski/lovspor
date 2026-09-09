"""The site generator: git guard, migrated pages, scans and the ledger (ADR-0014).

Built against throwaway repositories: a lovspor-shaped checkout whose HEAD
is the ``lovspor_commit`` the capability document names, and a one-document
lovverk corpus whose ``emit_site`` output supplies ``site-manifest.json``.
The developer's own repository is never a build input here.
"""

import ast
import hashlib
import html.parser
import json
import re
import shutil
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from pydantic import ValidationError

import lovspor
import lovspor.site.build as site_build
from lovspor.publish.emit import emit_site
from lovspor.publish.pages import SITE_ORIGIN
from lovspor.site.build import (
    SiteBuildReport,
    SiteInputs,
    build_site,
    discover_checkout,
    require_clean_work_tree,
)
from lovspor.site.capabilities import (
    Checkout,
    Observation,
    State,
    derive_comparisons,
    derive_hosted_state,
    derive_state,
    state_sha256,
)
from lovspor.site.errors import CapabilityDocumentError, SiteBuildError
from lovspor.site.facts import KindMismatchError
from lovspor.site.fingerprint import release_content_id, toolchain_fingerprint
from lovspor.site.routes import emitted_pages
from lovspor.site.scan import check_links, scan_page
from lovspor.site.templates import TEMPLATES_DIR
from lovspor.tool_surface import ToolSurfaceDescriptor, describe_tool_surface
from tests.unit.site_fixtures import (
    OBSERVED_AT,
    available_observation,
    checkout_for,
    commit_all,
    document_for,
    readyz_503,
    run_git,
    throwaway_checkout,
    throwaway_corpus,
    unobserved_authenticated,
    unobserved_process,
    unobserved_transport,
    with_surface,
)

_REPO = Path(__file__).resolve().parents[2]
_GOLDEN_OBSERVATORY = _REPO / "deploy" / "digitalocean" / "site" / "observatory" / "index.html"
_ROOT_FILES = {"site-facts.json", "sitemap-site.xml", "deployment-capabilities.json"}
_LIVE_CLAIMS = re.compile(r"\b(up now|live now|available now|oppe nå|tilgjengelig nå)\b", re.I)
_VOID_ELEMENTS = frozenset({"br", "hr", "img", "meta", "link", "input"})


class World(NamedTuple):
    checkout: Path
    lovspor_commit: str
    corpus: Path
    corpus_commit: str
    corpus_site: Path
    descriptor: ToolSurfaceDescriptor

    def observation(self, base: dict[str, Any] | None = None) -> dict[str, Any]:
        return with_surface(
            base if base is not None else available_observation(), self.descriptor.schema_sha256
        )

    def document(self, observation: dict[str, Any] | None = None) -> dict[str, Any]:
        """A document for this checkout; ``observation`` is taken as given."""
        checkout = checkout_for(self.lovspor_commit, self.descriptor.schema_sha256)
        payload = observation if observation is not None else self.observation()
        return document_for(payload, checkout)

    def inputs(self, out: Path, document: dict[str, Any] | bytes | None = None) -> SiteInputs:
        capabilities = out.parent / f"{out.name}-capabilities.json"
        payload = document if document is not None else self.document()
        if isinstance(payload, bytes):
            capabilities.write_bytes(payload)
        else:
            capabilities.write_text(json.dumps(payload), encoding="utf-8")
        return SiteInputs(
            checkout=self.checkout,
            corpus=self.corpus,
            corpus_manifest=self.corpus_site / "site-manifest.json",
            capabilities=capabilities,
            out=out,
        )

    def build(self, out: Path, observation: dict[str, Any] | None = None) -> SiteBuildReport:
        document = self.document(observation) if observation is not None else None
        return build_site(self.inputs(out, document))


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    root = tmp_path_factory.mktemp("world")
    checkout, lovspor_commit = throwaway_checkout(root / "lovspor")
    corpus, corpus_commit = throwaway_corpus(root / "lovverk")
    corpus_site = root / "corpus-site"
    emit_site(corpus, corpus_commit, corpus_site)
    return World(
        checkout, lovspor_commit, corpus, corpus_commit, corpus_site, describe_tool_surface(corpus)
    )


@pytest.fixture(scope="module")
def built(world: World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, SiteBuildReport]:
    out = tmp_path_factory.mktemp("built") / "site"
    return out, world.build(out)


def _files(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def _page(out: Path, path: str) -> str:
    return (out / path.lstrip("/") / "index.html").read_text(encoding="utf-8")


def _facts(out: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((out / "site-facts.json").read_text(encoding="utf-8"))
    return payload


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _Text(html.parser.HTMLParser):
    """Visible text of one element subtree, ``data-literal`` spans included as text."""

    def __init__(self, element: str) -> None:
        super().__init__(convert_charrefs=True)
        self._element = element
        self._depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_ELEMENTS:
            return
        if tag == self._element or self._depth:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._depth:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._depth:
            self.parts.append(data)


def _text(markup: str, element: str = "main") -> str:
    parser = _Text(element)
    parser.feed(markup)
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _fact_values(markup: str, fact_id: str) -> list[str]:
    pattern = re.compile(
        rf'<span data-fact="{re.escape(fact_id)}" data-kind="[a-z]+">(.*?)</span>', re.S
    )
    return pattern.findall(markup)


class TestGitGuard:
    def test_outside_a_git_work_tree_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SiteBuildError, match="not a git work tree"):
            require_clean_work_tree(tmp_path)

    def test_a_missing_directory_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SiteBuildError, match="not a git work tree"):
            require_clean_work_tree(tmp_path / "absent")

    def test_an_untracked_file_is_dirty(self, tmp_path: Path) -> None:
        root, _ = throwaway_checkout(tmp_path)
        (root / "scratch.txt").write_text("x", encoding="utf-8")

        with pytest.raises(SiteBuildError, match="dirty"):
            require_clean_work_tree(root)

    def test_a_staged_change_is_dirty(self, tmp_path: Path) -> None:
        root, _ = throwaway_checkout(tmp_path)
        (root / "uv.lock").write_text("version = 2\n", encoding="utf-8")
        run_git(root, "add", "uv.lock")

        with pytest.raises(SiteBuildError, match="dirty"):
            require_clean_work_tree(root)

    def test_a_clean_tree_yields_its_head(self, tmp_path: Path) -> None:
        root, head = throwaway_checkout(tmp_path)

        assert require_clean_work_tree(root) == head
        assert re.fullmatch(r"[0-9a-f]{40}", head)

    def test_a_repository_without_a_commit_is_refused(self, tmp_path: Path) -> None:
        run_git(tmp_path, "init", "-q")

        with pytest.raises(SiteBuildError, match="HEAD"):
            require_clean_work_tree(tmp_path)

    def test_a_dirty_checkout_fails_the_build_before_it_writes(
        self, world: World, tmp_path: Path
    ) -> None:
        checkout, commit = throwaway_checkout(tmp_path / "dirty")
        (checkout / "note.txt").write_text("x", encoding="utf-8")
        out = tmp_path / "site"
        document = document_for(
            world.observation(), checkout_for(commit, world.descriptor.schema_sha256)
        )
        inputs = world.inputs(out, document).model_copy(update={"checkout": checkout})

        with pytest.raises(SiteBuildError, match="dirty"):
            build_site(inputs)
        assert not out.exists() or not _files(out)


class TestDiscoverCheckout:
    def test_the_checkout_is_discovered_from_the_imported_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``git rev-parse --show-toplevel`` from the package directory, and
        that top's ``src/lovspor`` is the package that was imported. Exercised
        on a throwaway checkout: where *this* test process imported lovspor
        from is an environment fact (a mutation run imports it from a copy),
        not the behaviour under test."""
        root, _ = throwaway_checkout(tmp_path / "checkout")
        monkeypatch.setattr(site_build, "_PACKAGE_DIR", (root / "src" / "lovspor").resolve())

        assert discover_checkout() == root.resolve()

    def test_a_package_that_is_not_the_checkouts_own_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A package inside a work tree but not at ``<top>/src/lovspor`` — a
        wheel installed into a venv under some repository, or a mutation
        harness's copy — is refused: its provenance would be that repository's
        HEAD without being that repository's code (ADR-0014 Decision 1)."""
        root, _ = throwaway_checkout(tmp_path / "checkout")
        copy = root / "mutants" / "src" / "lovspor"
        copy.mkdir(parents=True)
        monkeypatch.setattr(site_build, "_PACKAGE_DIR", copy.resolve())

        with pytest.raises(SiteBuildError, match="refusing to attest"):
            discover_checkout()

    def test_a_package_outside_a_work_tree_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An installed wheel has no HEAD to record (ADR-0014 Decision 1)."""
        package = tmp_path / "site-packages" / "lovspor"
        package.mkdir(parents=True)
        monkeypatch.setattr(site_build, "_PACKAGE_DIR", package)

        with pytest.raises(SiteBuildError, match="not a git work tree"):
            discover_checkout()

    def test_a_wheel_inside_a_foreign_repository_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A work tree whose ``src/lovspor`` is not the imported package would
        record a commit the output was not built from."""
        root, _ = throwaway_checkout(tmp_path)
        package = root / ".venv" / "lib" / "lovspor"
        package.mkdir(parents=True)
        monkeypatch.setattr(site_build, "_PACKAGE_DIR", package)

        with pytest.raises(SiteBuildError, match="src/lovspor"):
            discover_checkout()


class TestBuildInputs:
    def test_inputs_are_frozen_paths(self, tmp_path: Path) -> None:
        inputs = SiteInputs(
            checkout=tmp_path,
            corpus=tmp_path,
            corpus_manifest=tmp_path / "m.json",
            capabilities=tmp_path / "c.json",
            out=tmp_path / "out",
        )

        with pytest.raises(ValidationError, match="frozen"):
            inputs.out = tmp_path  # type: ignore[misc]

    def test_refuses_a_non_empty_output_directory(self, world: World, tmp_path: Path) -> None:
        out = tmp_path / "site"
        out.mkdir()
        (out / "stale.html").write_text("x", encoding="utf-8")

        with pytest.raises(SiteBuildError, match="not empty"):
            world.build(out)
        assert _files(out) == {"stale.html"}

    def test_an_absent_document_fails(self, world: World, tmp_path: Path) -> None:
        inputs = world.inputs(tmp_path / "site")
        inputs.capabilities.unlink()

        with pytest.raises(CapabilityDocumentError, match="cannot read"):
            build_site(inputs)

    def test_an_unknown_key_in_the_document_fails(self, world: World, tmp_path: Path) -> None:
        document = world.document()
        document["observation"]["process"]["hostname"] = "droplet"

        with pytest.raises(CapabilityDocumentError, match="hostname"):
            build_site(world.inputs(tmp_path / "site", document))

    def test_a_document_naming_a_foreign_commit_fails(self, world: World, tmp_path: Path) -> None:
        document = document_for(
            world.observation(), checkout_for("f" * 40, world.descriptor.schema_sha256)
        )

        with pytest.raises(SiteBuildError, match="foreign commit"):
            build_site(world.inputs(tmp_path / "site", document))

    def test_a_document_expecting_another_tool_surface_fails(
        self, world: World, tmp_path: Path
    ) -> None:
        """The checkout descriptor is recomputed by the build and must be
        the one the document's checkout part names (plan F.3)."""
        document = document_for(world.observation(), checkout_for(world.lovspor_commit, "e" * 64))

        with pytest.raises(SiteBuildError, match="tool surface"):
            build_site(world.inputs(tmp_path / "site", document))

    def test_a_manifest_without_the_fields_the_site_reads_fails(
        self, world: World, tmp_path: Path
    ) -> None:
        manifest = tmp_path / "site-manifest.json"
        manifest.write_text('{"corpus_commit": "x"}', encoding="utf-8")
        inputs = world.inputs(tmp_path / "site").model_copy(update={"corpus_manifest": manifest})

        with pytest.raises(SiteBuildError, match="site-manifest.json"):
            build_site(inputs)

    def test_an_unreadable_manifest_fails(self, world: World, tmp_path: Path) -> None:
        inputs = world.inputs(tmp_path / "site").model_copy(
            update={"corpus_manifest": tmp_path / "absent.json"}
        )

        with pytest.raises(SiteBuildError, match="site-manifest.json"):
            build_site(inputs)

    def test_a_manifest_with_unknown_keys_is_tolerated(self, world: World, tmp_path: Path) -> None:
        """The file is ADR-0013's; a corpus-side schema bump adding a field the
        site never reads must not fail the site build."""
        payload = json.loads((world.corpus_site / "site-manifest.json").read_text(encoding="utf-8"))
        payload["per_route"] = {"lov": 1}
        manifest = tmp_path / "site-manifest.json"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        inputs = world.inputs(tmp_path / "site").model_copy(update={"corpus_manifest": manifest})

        assert build_site(inputs).corpus_commit == world.corpus_commit


class TestTree:
    def test_report_names_the_commit_the_pages_and_the_key(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        _, report = built

        assert report.lovspor_commit == world.lovspor_commit
        assert report.corpus_commit == world.corpus_commit
        assert report.pages == tuple(page.path for page in emitted_pages())
        assert report.hosted_state == "available"
        assert report.release_key.lovspor_commit == world.lovspor_commit

    def test_route_closure(self, built: tuple[Path, SiteBuildReport]) -> None:
        """Every Decision-2 route in both languages where the mirror rule
        applies, nothing outside the route set plus the three root files."""
        out, _ = built
        expected = {f"{page.path.lstrip('/')}index.html" for page in emitted_pages()}

        assert _files(out) == expected | _ROOT_FILES
        assert not [
            name for name in _files(out) if name.startswith("connect/") and name.count("/") > 1
        ]

    def test_sitemap_lists_exactly_the_emitted_pages(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        sitemap = (out / "sitemap-site.xml").read_text(encoding="utf-8")

        assert re.findall(r"<loc>(.*?)</loc>", sitemap) == [
            f"{SITE_ORIGIN}{page.path}" for page in emitted_pages()
        ]

    def test_capability_document_is_copied_byte_for_byte(
        self, built: tuple[Path, SiteBuildReport], world: World, tmp_path: Path
    ) -> None:
        out, _ = built
        source = out.parent / "site-capabilities.json"

        assert (out / "deployment-capabilities.json").read_bytes() == source.read_bytes()

    def test_every_page_has_one_h1_a_title_a_description_and_self_canonical(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        for page in emitted_pages():
            markup = _page(out, page.path)

            assert markup.count("<h1>") == 1, page.path
            assert f'<html lang="{page.lang}">' in markup
            assert "<title>" in markup and 'name="description"' in markup
            assert f'<link rel="canonical" href="{SITE_ORIGIN}{page.path}">' in markup

    def test_hreflang_pairs_are_reciprocal(self, built: tuple[Path, SiteBuildReport]) -> None:
        out, _ = built
        for page in emitted_pages():
            markup = _page(out, page.path)
            alternates = dict(
                re.findall(r'<link rel="alternate" hreflang="(\w+)" href="([^"]+)">', markup)
            )
            if not page.route.twin:
                assert alternates == {}, page.path
                continue
            assert alternates[page.lang] == f"{SITE_ORIGIN}{page.path}"
            other = "en" if page.lang == "nb" else "nb"
            twin = alternates[other].removeprefix(SITE_ORIGIN)
            twin_alternates = dict(
                re.findall(
                    r'<link rel="alternate" hreflang="(\w+)" href="([^"]+)">', _page(out, twin)
                )
            )
            assert twin_alternates[page.lang] == f"{SITE_ORIGIN}{page.path}", page.path

    def test_no_script_no_handler_no_external_asset_on_any_page(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        for page in emitted_pages():
            markup = _page(out, page.path)

            assert "<script" not in markup, page.path
            assert not re.search(r"\son\w+=", markup), page.path
            assert not re.search(r"(?:href|src)=\"(?:javascript|data):", markup, re.I), page.path
            assert "<img" not in markup, page.path
            for tag in re.findall(r"<link[^>]+>", markup):
                assert 'rel="canonical"' in tag or 'rel="alternate"' in tag, (page.path, tag)
            assert "@import" not in markup

    def test_internal_links_resolve_to_emitted_pages_or_the_corpus_allowlist(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        emitted = {page.path for page in emitted_pages()}
        for page in emitted_pages():
            for href in re.findall(r'href="(/[^"]*)"', _page(out, page.path)):
                assert (
                    href in emitted
                    or href.startswith(("/lov/", "/forskrift/"))
                    or href == "/site-manifest.json"
                ), (page.path, href)

    def test_placeholder_pages_carry_the_status_vocabulary(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        for page in emitted_pages():
            markup = _page(out, page.path)
            badges = set(re.findall(r'data-status="(\w+)"', markup))

            assert badges <= {"current", "planned", "research", "early_access"}, page.path
            if page.route.template == "placeholder":
                assert page.route.status in badges, page.path


class TestLanding:
    def test_norwegian_landing_reads_the_two_facts_and_marks_its_literals(
        self, built: tuple[Path, SiteBuildReport], world: World
    ) -> None:
        out, _ = built
        markup = _page(out, "/")

        assert _fact_values(markup, "corpus.documents") == ["1"]
        assert _fact_values(markup, "code.tool_surface.tool_count") == [
            str(world.descriptor.tool_count)
        ]
        assert "Seksten" not in markup and "~5" not in markup
        assert "<span data-literal>folketrygdloven § 8-18</span>" in markup
        assert "<span data-literal>AGPL-3.0</span>" in markup
        assert "(<span data-literal>NLOD 2.0</span>)" in markup
        assert (
            '<em><span class="tag" data-status="planned">Planlagt</span> — slik de fleste vil '
            "bruke det.</em>"
        ) in markup
        assert "Kommer snart" not in markup

    def test_english_landing_mirrors_the_substitutions(
        self, built: tuple[Path, SiteBuildReport], world: World
    ) -> None:
        out, _ = built
        markup = _page(out, "/en/")

        assert _fact_values(markup, "corpus.documents") == ["1"]
        assert "Sixteen" not in markup and "~5" not in markup
        assert (
            '<em><span class="tag" data-status="planned">Planned</span> — the way most people '
            "will use it.</em>"
        ) in markup
        assert "Coming shortly" not in markup
        assert 'href="https://modelcontextprotocol.io"' in markup
        assert 'href="mailto:bartosz.kobylinski@gmail.com?subject=lovspor%20access"' in markup

    def test_the_rest_of_the_landing_copy_is_verbatim(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        for path, source in (("/", "index.html"), ("/en/", "en/index.html")):
            golden = (_REPO / "deploy" / "digitalocean" / "site" / source).read_text(
                encoding="utf-8"
            )
            golden_main = _text(golden)
            built_main = _text(_page(out, path))
            for original, replacement in (
                ("~5 900", "1"),
                ("~5,900", "1"),
                ("Seksten", "17"),
                ("Sixteen", "17"),
                ("Kommer snart", "Planlagt"),
                ("Coming shortly", "Planned"),
            ):
                golden_main = golden_main.replace(original, replacement)

            assert built_main == golden_main, path

    def test_the_tool_count_is_a_code_fact_never_the_hosted_count(
        self, world: World, tmp_path: Path
    ) -> None:
        """Owner decision (2026-09-09): the landing renders the checkout
        descriptor's count. A served count that differs must not leak in."""
        observation = world.observation()
        observation["transport"]["authenticated"]["served_tool_count"] = 3
        observation["transport"]["authenticated"]["served_tool_surface_sha256"] = "e" * 64
        out = tmp_path / "site"
        world.build(out, observation)

        assert _fact_values(_page(out, "/"), "code.tool_surface.tool_count") == ["17"]
        assert 'data-kind="hosted"' not in _page(out, "/")


class TestObservatory:
    def test_main_text_is_the_hand_written_page_verbatim(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        golden = _text(_GOLDEN_OBSERVATORY.read_text(encoding="utf-8"))

        assert _text(_page(out, "/observatory/")) == golden

    def test_has_no_twin_and_carries_the_norwegian_chrome(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        markup = _page(out, "/observatory/")

        assert not (out / "en" / "observatory").exists()
        assert "hreflang" not in markup
        assert "Om roboten vår" in markup


class TestStatusPage:
    def test_shows_corpus_facts_hosted_state_and_the_five_comparisons_verbatim(
        self, built: tuple[Path, SiteBuildReport], world: World
    ) -> None:
        out, _ = built
        for path in ("/status/", "/en/status/"):
            markup = _page(out, path)

            assert _fact_values(markup, "corpus.commit") == [world.corpus_commit]
            assert _fact_values(markup, "corpus.commit_time") == ["2026-02-01T00:00:00+00:00"]
            assert _fact_values(markup, "corpus.documents") == ["1"]
            assert _fact_values(markup, "code.lovspor_commit") == [world.lovspor_commit]
            assert _fact_values(markup, "hosted.state") == ["available"]
            for name in (
                "runtime_tree_match",
                "environment_match",
                "tool_surface_match",
                "transport_surface_match",
                "oauth_discovery_consistent",
            ):
                assert _fact_values(markup, f"hosted.comparisons.{name}") == ["true"], name
            assert _fact_values(markup, "hosted.process.status") == ["observed"]
            assert _fact_values(markup, "hosted.transport.status") == ["observed"]
            assert _fact_values(markup, "hosted.process.observed_at") == [OBSERVED_AT]
            assert _fact_values(markup, "hosted.transport.observed_at") == [OBSERVED_AT]

    def test_shows_the_three_tool_counts_under_their_kinds(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        markup = _page(out, "/status/")

        assert (
            '<span data-fact="hosted.transport.authenticated.served_tool_count" '
            'data-kind="hosted">17</span>'
        ) in markup
        assert '<span data-fact="hosted.process.tool_count" data-kind="hosted">17</span>' in markup
        assert '<span data-fact="code.tool_surface.tool_count" data-kind="code">17</span>' in markup

    def test_manifest_hash_is_shown_and_not_linked(
        self, built: tuple[Path, SiteBuildReport], world: World
    ) -> None:
        out, _ = built
        markup = _page(out, "/status/")
        manifest_sha = _sha((world.corpus_site / "site-manifest.json").read_bytes())

        assert _fact_values(markup, "corpus.manifest_sha256") == [manifest_sha]
        assert "/site-manifest.json" not in markup

    def test_served_and_process_counts_differ_and_both_are_shown(
        self, world: World, tmp_path: Path
    ) -> None:
        observation = world.observation()
        observation["transport"]["authenticated"]["served_tool_count"] = 16
        observation["transport"]["authenticated"]["served_tool_surface_sha256"] = "e" * 64
        out = tmp_path / "site"

        report = world.build(out, observation)

        markup = _page(out, "/status/")
        assert report.hosted_state == "unavailable"
        assert _fact_values(markup, "hosted.transport.authenticated.served_tool_count") == ["16"]
        assert _fact_values(markup, "hosted.process.tool_count") == ["17"]
        assert _fact_values(markup, "code.tool_surface.tool_count") == ["17"]
        assert _fact_values(markup, "hosted.comparisons.transport_surface_match") == ["false"]
        assert _fact_values(markup, "hosted.state") == ["unavailable"]

    def test_process_and_checkout_surfaces_differ(self, world: World, tmp_path: Path) -> None:
        observation = world.observation()
        observation["process"]["tool_surface_sha256"] = "e" * 64
        observation["process"]["tool_count"] = 16
        observation["transport"]["authenticated"]["served_tool_surface_sha256"] = "e" * 64
        observation["transport"]["authenticated"]["served_tool_count"] = 16
        out = tmp_path / "site"
        world.build(out, observation)
        markup = _page(out, "/status/")

        assert _fact_values(markup, "hosted.comparisons.tool_surface_match") == ["false"]
        assert _fact_values(markup, "hosted.comparisons.transport_surface_match") == ["true"]
        assert _fact_values(markup, "hosted.process.tool_count") == ["16"]
        assert _fact_values(markup, "code.tool_surface.tool_count") == ["17"]


_DEGRADED = {
    "process unobserved": (unobserved_process("timeout"), "timeout", "unknown"),
    "transport unobserved": (unobserved_transport("network"), "network", "unknown"),
    "credential rejected": (
        unobserved_authenticated("probe_credential_rejected"),
        "probe_credential_rejected",
        "unknown",
    ),
    "readyz 503": (readyz_503(), "not_ready", "unavailable"),
}


class TestDegradation:
    @pytest.mark.parametrize("case", sorted(_DEGRADED))
    def test_unobserved_hosted_values_degrade_with_reason_and_time(
        self, world: World, tmp_path: Path, case: str
    ) -> None:
        base, reason, hosted_state = _DEGRADED[case]
        out = tmp_path / "site"

        report = world.build(out, world.observation(base))

        assert report.hosted_state == hosted_state
        for path, wording in (
            ("/status/", "ikke attestert ved denne utgivelsen — uobservert"),
            ("/en/status/", "not attested at this release — unobserved"),
        ):
            markup = _page(out, path)
            assert _fact_values(markup, "hosted.state") == [hosted_state]
            degraded = [
                value
                for value in re.findall(r'data-kind="hosted">(.*?)</span>', markup)
                if wording in value
            ]
            assert degraded, (case, path)
            assert all(f"({reason})" in value and OBSERVED_AT in value for value in degraded)
            assert not _LIVE_CLAIMS.search(markup), (case, path)
            assert ">0</span>" not in markup
            assert 'data-kind="hosted"></span>' not in markup

    def test_a_missing_credential_is_unknown_never_unavailable(
        self, world: World, tmp_path: Path
    ) -> None:
        out = tmp_path / "site"
        observation = world.observation(unobserved_authenticated("probe_credential_missing"))
        report = world.build(out, observation)

        assert report.hosted_state == "unknown"
        assert _fact_values(
            _page(out, "/status/"), "hosted.comparisons.transport_surface_match"
        ) == ["unknown"]

    def test_the_corpus_and_landing_do_not_move_with_the_observation(
        self, world: World, tmp_path: Path
    ) -> None:
        one, two = tmp_path / "one", tmp_path / "two"
        world.build(one)
        world.build(two, world.observation(unobserved_transport("timeout")))

        for path in ("/", "/en/", "/observatory/", "/docs/"):
            assert _page(one, path) == _page(two, path), path
        assert _page(one, "/status/") != _page(two, "/status/")


class TestDeterminism:
    def test_two_builds_of_the_same_inputs_are_byte_identical(
        self, world: World, tmp_path: Path
    ) -> None:
        one, two = tmp_path / "one", tmp_path / "two"
        report_one = world.build(one)
        report_two = world.build(two)

        assert report_one.release_key == report_two.release_key
        assert _files(one) == _files(two)
        for name in sorted(_files(one)):
            assert (one / name).read_bytes() == (two / name).read_bytes(), name
        assert release_content_id(world.corpus_site, one) == release_content_id(
            world.corpus_site, two
        )

    def test_an_observed_at_only_change_moves_the_bytes_but_not_the_key(
        self, world: World, tmp_path: Path
    ) -> None:
        """The two identifiers answer different questions (ADR:2329-2340)."""
        one, two = tmp_path / "one", tmp_path / "two"
        observation = world.observation()
        observation["process"]["observed_at"] = "2026-01-02T00:00:00Z"
        report_one = world.build(one)
        report_two = world.build(two, observation)

        assert report_one.release_key == report_two.release_key
        assert _page(one, "/status/") != _page(two, "/status/")
        assert release_content_id(world.corpus_site, one) != release_content_id(
            world.corpus_site, two
        )
        assert _facts(one)["release_key"] == _facts(two)["release_key"]

    def test_the_fingerprint_is_recomputable_and_recorded_with_its_components(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        toolchain = toolchain_fingerprint(world.checkout)
        recorded = _facts(out)["toolchain"]

        assert recorded["fingerprint"] == toolchain.fingerprint
        assert recorded["interpreter"] == toolchain.interpreter.model_dump()
        assert recorded["jinja2_version"] == toolchain.jinja2_version
        assert recorded["uv_lock_sha256"] == _sha((world.checkout / "uv.lock").read_bytes())


class TestSiteFacts:
    def test_provenance_is_complete(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, report = built
        facts = _facts(out)
        document = json.loads((out / "deployment-capabilities.json").read_text(encoding="utf-8"))
        observation = Observation.model_validate(document["observation"])
        comparisons = derive_comparisons(
            observation, Checkout.model_validate(document["state"]["checkout"])
        )

        assert facts["schema_version"] == "1"
        assert facts["lovspor_commit"] == world.lovspor_commit
        assert facts["engine_version"] == lovspor.__version__
        assert facts["corpus_commit"] == world.corpus_commit
        assert facts["corpus_commit_time"] == "2026-02-01T00:00:00+00:00"
        assert facts["site_manifest_sha256"] == _sha(
            (world.corpus_site / "site-manifest.json").read_bytes()
        )
        assert facts["release_content_id"] is None
        assert facts["capability_sha256"] == _sha(
            (out / "deployment-capabilities.json").read_bytes()
        )
        assert facts["release_key"] == report.release_key.model_dump()
        assert facts["release_key"]["state_sha256"] == state_sha256(_state_of(document))
        assert facts["capability"]["comparisons"] == comparisons.model_dump()
        assert facts["capability"]["comparisons"] == document["state"]["comparisons"]
        assert facts["capability"]["hosted_state"] == derive_hosted_state(observation, comparisons)
        assert facts["capability"]["hosted_state"] == document["state"]["hosted_state"]
        assert (
            facts["capability"]["expected_runtime_identity"]
            == document["state"]["checkout"]["expected_runtime_identity"]
        )
        assert (
            facts["capability"]["observed_runtime_identity"]
            == document["observation"]["process"]["runtime_identity"]
        )
        assert facts["capability"]["process"] == {
            "status": "observed",
            "reason": None,
            "observed_at": OBSERVED_AT,
        }
        assert facts["capability"]["transport"] == {
            "status": "observed",
            "reason": None,
            "observed_at": OBSERVED_AT,
            "authenticated": {"status": "observed", "reason": None},
        }

    def test_artifacts_are_named_by_logical_identity_and_hash(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        artifacts = {entry["id"]: entry["sha256"] for entry in _facts(out)["artifacts"]}

        assert artifacts == {
            "corpus/site-manifest.json": _sha(
                (world.corpus_site / "site-manifest.json").read_bytes()
            ),
            "deployment-capabilities.json": _sha(
                (out / "deployment-capabilities.json").read_bytes()
            ),
            f"tool-surface@{world.lovspor_commit}": world.descriptor.schema_sha256,
        }

    def test_no_string_is_an_absolute_path_or_a_hostname(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        pages = {page.path for page in emitted_pages()}
        strings = [
            value
            for value in _strings(_facts(out))
            if value not in pages and not re.fullmatch(r"[0-9a-f]{40,64}", value)
        ]

        for value in strings:
            assert not value.startswith(("/", "\\", "~")), value
            assert not re.search(r"://|\b[a-z0-9-]+\.(?:no|com|org|net|io|local)\b", value), value
            assert ":\\" not in value and "/Users/" not in value and "/home/" not in value, value

    def test_the_ledger_is_proven_against_the_artifacts(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        """Every entry's field, read back from its artifact, equals the recorded
        value — the ledger is proven, not populated (ADR:2412-2420)."""
        out, _ = built
        facts = _facts(out)
        document = json.loads((out / "deployment-capabilities.json").read_text(encoding="utf-8"))
        manifest_bytes = (world.corpus_site / "site-manifest.json").read_bytes()

        assert facts["facts"]
        for entry in facts["facts"]:
            resolved = _resolve(entry, world, document, manifest_bytes)
            if entry["unobserved"] is None:
                assert entry["value"] == resolved, entry
            else:
                assert resolved is None, entry
                assert entry["value"] is None

    def test_every_hosted_entry_names_the_document_and_every_code_entry_the_checkout(
        self, world: World, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        for entry in _facts(out)["facts"]:
            if entry["kind"] == "hosted":
                assert entry["artifact"] == "deployment-capabilities.json", entry
            elif entry["kind"] == "corpus":
                assert entry["artifact"] == "corpus/site-manifest.json", entry
            else:
                assert entry["artifact"] == f"tool-surface@{world.lovspor_commit}", entry

    def test_every_page_that_renders_a_fact_is_in_the_ledger(
        self, built: tuple[Path, SiteBuildReport]
    ) -> None:
        out, _ = built
        pages = {entry["page"] for entry in _facts(out)["facts"]}

        assert pages == {"/", "/en/", "/status/", "/en/status/"}

    def test_facts_json_is_companion_bytes(self, built: tuple[Path, SiteBuildReport]) -> None:
        out, _ = built
        raw = (out / "site-facts.json").read_bytes()

        assert raw == (
            json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False, indent=1) + "\n"
        ).encode("utf-8")


def _state_of(document: dict[str, Any]) -> State:
    return derive_state(
        Observation.model_validate(document["observation"]),
        Checkout.model_validate(document["state"]["checkout"]),
    )


def _strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for key, value in node.items() for s in (_strings(key) + _strings(value))]
    if isinstance(node, list):
        return [s for value in node for s in _strings(value)]
    return []


def _resolve(
    entry: dict[str, Any], world: World, document: dict[str, Any], manifest_bytes: bytes
) -> object:
    artifact, field = entry["artifact"], entry["field"]
    if artifact == "corpus/site-manifest.json":
        if field == "sha256":
            return _sha(manifest_bytes)
        return json.loads(manifest_bytes)[field]
    if artifact == "deployment-capabilities.json":
        node: Any = document
        for part in field.split("."):
            node = node[part]
        return node
    commit = artifact.removeprefix("tool-surface@")
    assert commit == world.lovspor_commit
    if field == "lovspor_commit":
        return commit
    return getattr(world.descriptor, field)


class TestScans:
    """The post-render scans, on hand-made pages (plan C.3)."""

    def _page(self, body: str, head: str = "") -> str:
        return (
            f'<!doctype html>\n<html lang="nb"><head><title>T</title>'
            f'<meta name="description" content="d"><link rel="canonical" href="{SITE_ORIGIN}/x/">'
            f"<style>.a{{width:10px}}</style>{head}</head><body>{body}</body></html>"
        )

    def test_a_typed_numeral_fails_naming_the_page_and_the_text(self) -> None:
        with pytest.raises(SiteBuildError, match=r"/x/.*17 tools"):
            scan_page("/x/", self._page("<p>17 tools</p>"))

    def test_a_numeral_in_the_title_or_description_fails(self) -> None:
        with pytest.raises(SiteBuildError, match="numeral"):
            scan_page(
                "/x/", self._page("<p>ok</p>").replace("<title>T</title>", "<title>T 2</title>")
            )
        with pytest.raises(SiteBuildError, match="numeral"):
            scan_page("/x/", self._page("<p>ok</p>").replace('content="d"', 'content="d 2"'))

    def test_every_description_is_scanned_for_a_typed_numeral(self) -> None:
        markup = self._page("<p>ok</p>").replace(
            '<meta name="description" content="d">',
            '<meta name="description" content="unledgered 2"><meta name="description" content="d">',
        )

        with pytest.raises(SiteBuildError, match=r"numeral.*unledgered 2"):
            scan_page("/x/", markup)

    def test_a_numeral_in_code_or_pre_is_not_exempt(self) -> None:
        with pytest.raises(SiteBuildError, match="numeral"):
            scan_page("/x/", self._page("<pre>x 1</pre>"))
        with pytest.raises(SiteBuildError, match="numeral"):
            scan_page("/x/", self._page("<code>v2</code>"))

    def test_a_marked_literal_a_fact_and_the_stylesheet_pass(self) -> None:
        scan_page(
            "/x/",
            self._page(
                "<p><span data-literal>§ 8-18</span> and "
                '<span data-fact="corpus.documents" data-kind="corpus">5 900</span>'
                "<code><span data-literal>v0.1<br>x2</span></code></p>"
            ),
        )

    def test_attributes_are_not_scanned(self) -> None:
        scan_page("/x/", self._page('<a href="/lov/2020-01-01-1/" title="a1">act</a>'))

    @pytest.mark.parametrize(
        "body",
        [
            "<script>1</script>",
            '<p onclick="x()">a</p>',
            '<a href="javascript:void(0)">a</a>',
            '<a href="data:text/html,x">a</a>',
            '<img src="https://example.com/a.png">',
            '<img src="/local.png" srcset="https://example.com/a.png 2x">',
            '<picture><source srcset="/a.png 1x, //cdn.example.com/a.png 2x"></picture>',
            '<video poster="https://example.com/p.jpg"></video>',
            '<object data="https://example.com/x.svg"></object>',
            '<iframe src="/x/"></iframe>',
        ],
    )
    def test_script_handlers_and_external_assets_fail(self, body: str) -> None:
        with pytest.raises(SiteBuildError):
            scan_page("/x/", self._page(body))

    @pytest.mark.parametrize(
        ("body", "attribute"),
        [
            ('<audio src="https://example.com/a.mp3"></audio>', "src"),
            ('<source srcset="/a.webp 1x, https://example.com/a.webp 2x">', "srcset"),
            ('<input imagesrcset="https://example.com/a.png 2x">', "imagesrcset"),
            ('<div data="https://example.com/a.bin"></div>', "data"),
            ('<video poster="https://example.com/a.jpg"></video>', "poster"),
        ],
    )
    def test_each_fetching_attribute_refuses_an_external_asset(
        self, body: str, attribute: str
    ) -> None:
        """Exercise the attribute scan itself, including elements that are otherwise allowed."""
        with pytest.raises(SiteBuildError, match=rf"external {attribute}="):
            scan_page("/x/", self._page(body))

    def test_svg_image_href_refuses_an_external_asset(self) -> None:
        body = '<svg><image href="https://example.com/a.svg"></image></svg>'

        with pytest.raises(SiteBuildError, match=r"external href="):
            scan_page("/x/", self._page(body))

    @pytest.mark.parametrize(
        "body",
        [
            '<svg><use xlink:href="https://example.com/s.svg#i"></use></svg>',
            '<svg><filter><feImage href="//cdn.example.com/f.png"></feImage></filter></svg>',
        ],
    )
    def test_svg_use_and_feimage_refuse_an_external_asset(self, body: str) -> None:
        with pytest.raises(SiteBuildError, match=r"external (?:xlink:)?href="):
            scan_page("/x/", self._page(body))

    def test_a_page_has_exactly_one_meta_description(self) -> None:
        doubled = self._page("<p>ok</p>").replace(
            '<meta name="description" content="d">',
            '<meta name="description" content="d"><meta name="description" content="e">',
        )
        with pytest.raises(SiteBuildError, match="meta descriptions"):
            scan_page("/x/", doubled)
        with pytest.raises(SiteBuildError, match="meta descriptions"):
            scan_page(
                "/x/", self._page("<p>ok</p>").replace('<meta name="description" content="d">', "")
            )

    def test_an_external_stylesheet_or_import_fails(self) -> None:
        with pytest.raises(SiteBuildError, match="link"):
            scan_page(
                "/x/", self._page("<p>a</p>", '<link rel="stylesheet" href="https://x.no/a.css">')
            )
        with pytest.raises(SiteBuildError, match="@import"):
            scan_page(
                "/x/",
                self._page("<p>a</p>").replace("<style>", '<style>@import url("https://x.no");'),
            )

    def test_an_external_asset_in_a_style_attribute_fails(self) -> None:
        with pytest.raises(SiteBuildError, match="external|url"):
            scan_page(
                "/x/",
                self._page('<p style="background-image:url(https://example.com/a.png)">a</p>'),
            )

    @pytest.mark.parametrize(
        "style",
        [
            "background:url('//cdn.example.com/a.png')",
            "background:url(HTTPS://example.com/a.png)",
            "@import url(https://example.com/a.css)",
        ],
    )
    def test_style_attribute_variants_fail(self, style: str) -> None:
        with pytest.raises(SiteBuildError, match="style attribute"):
            scan_page("/x/", self._page(f'<p style="{style}">a</p>'))

    def test_a_local_url_in_a_style_attribute_passes(self) -> None:
        scan_page("/x/", self._page('<p style="background:url(/a.png)">a</p>'))

    def test_canonical_must_be_the_page_itself(self) -> None:
        with pytest.raises(SiteBuildError, match="canonical"):
            scan_page("/y/", self._page("<p>a</p>"))

    def test_links_must_resolve_to_emitted_pages_or_the_allowlist(self) -> None:
        pages = {
            "/x/": self._page(
                '<a href="/y/">y</a><a href="/lov/a/">a</a><a href="/site-manifest.json">m</a>'
                '<a href="https://example.com/">e</a><a href="mailto:a@b">m</a>'
            ),
            "/y/": self._page("<p>a</p>").replace(f"{SITE_ORIGIN}/x/", f"{SITE_ORIGIN}/y/"),
        }
        check_links(pages)

        pages["/y/"] = pages["/y/"].replace("<p>a</p>", '<a href="/z/">z</a>')
        with pytest.raises(SiteBuildError, match="/z/"):
            check_links(pages)
        pages["/y/"] = pages["/y/"].replace('href="/z/"', 'href="relative.html"')
        with pytest.raises(SiteBuildError, match="relative.html"):
            check_links(pages)

    def test_hreflang_pairs_must_be_reciprocal(self) -> None:
        pages = {
            "/x/": self._page(
                "<p>a</p>",
                f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/x/">'
                f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/x/">',
            ),
            "/en/x/": self._page(
                "<p>a</p>",
                f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/x/">'
                f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/x/">',
            ).replace(
                f'canonical" href="{SITE_ORIGIN}/x/"', f'canonical" href="{SITE_ORIGIN}/en/x/"'
            ),
        }
        check_links(pages)

        pages["/en/x/"] = pages["/en/x/"].replace(
            f'hreflang="nb" href="{SITE_ORIGIN}/x/"', f'hreflang="nb" href="{SITE_ORIGIN}/"'
        )
        with pytest.raises(SiteBuildError, match="hreflang"):
            check_links(pages)

    def test_hreflang_must_name_the_page_itself_exactly_once(self) -> None:
        pages = {
            "/x/": self._page(
                "<p>a</p>",
                f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/x/">'
                f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/x/">'
                f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/x/">',
            ),
            "/en/x/": self._page(
                "<p>a</p>",
                f'<link rel="alternate" hreflang="nb" href="{SITE_ORIGIN}/x/">'
                f'<link rel="alternate" hreflang="en" href="{SITE_ORIGIN}/en/x/">',
            ).replace(
                f'canonical" href="{SITE_ORIGIN}/x/"', f'canonical" href="{SITE_ORIGIN}/en/x/"'
            ),
        }

        with pytest.raises(SiteBuildError, match="hreflang.*once"):
            check_links(pages)


class TestTemplateContract:
    """A template outside the fact contract fails the build."""

    @pytest.fixture
    def templates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        copy = tmp_path / "templates"
        shutil.copytree(TEMPLATES_DIR, copy)
        monkeypatch.setattr("lovspor.site.templates.TEMPLATES_DIR", copy)
        return copy

    def _placeholder(self, templates: Path, extra: str) -> None:
        page = templates / "pages" / "placeholder.nb.html"
        page.write_text(
            page.read_text(encoding="utf-8").replace(
                "{% endblock %}", f"<p>{extra}</p>\n{{% endblock %}}"
            ),
            encoding="utf-8",
        )

    def test_a_typed_numeral_fails_the_build(
        self, world: World, tmp_path: Path, templates: Path
    ) -> None:
        self._placeholder(templates, "Seksten verktøy, 16 stykker")

        with pytest.raises(SiteBuildError, match=r"/connect/.*16 stykker"):
            world.build(tmp_path / "site")

    def test_a_marked_literal_passes(self, world: World, tmp_path: Path, templates: Path) -> None:
        self._placeholder(templates, "<span data-literal>§ 8-18</span>")

        assert world.build(tmp_path / "site").pages

    def test_a_fact_passes_and_is_ledgered(
        self, world: World, tmp_path: Path, templates: Path
    ) -> None:
        self._placeholder(templates, '{{ fact("corpus.documents", kind="corpus") }}')
        out = tmp_path / "site"

        world.build(out)

        assert "/connect/" in {entry["page"] for entry in _facts(out)["facts"]}

    def test_a_kind_mismatch_fails_the_build(
        self, world: World, tmp_path: Path, templates: Path
    ) -> None:
        self._placeholder(templates, '{{ fact("corpus.documents", kind="hosted") }}')

        with pytest.raises(KindMismatchError, match="corpus.documents"):
            world.build(tmp_path / "site")

    def test_an_unknown_fact_fails_the_build(
        self, world: World, tmp_path: Path, templates: Path
    ) -> None:
        self._placeholder(templates, '{{ fact("hosted.made_up", kind="hosted") }}')

        with pytest.raises(SiteBuildError, match="hosted.made_up"):
            world.build(tmp_path / "site")

    def test_a_script_in_a_template_fails_the_build(
        self, world: World, tmp_path: Path, templates: Path
    ) -> None:
        self._placeholder(templates, "<script>x()</script>")

        with pytest.raises(SiteBuildError, match="script"):
            world.build(tmp_path / "site")


class TestLayering:
    def test_the_site_build_imports_no_benchmark_module(self) -> None:
        """Production publication never imports ``lovspor.llhb.*`` (ADR:725-727)."""
        sources = [
            *sorted((_REPO / "src" / "lovspor" / "site").rglob("*.py")),
            _REPO / "src" / "lovspor" / "runtime_identity.py",
            _REPO / "src" / "lovspor" / "tool_surface.py",
        ]
        offenders = [
            (path.name, module)
            for path in sources
            for module in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
            if module == "lovspor.llhb" or module.startswith("lovspor.llhb.")
        ]

        assert offenders == []

    def test_the_build_opens_only_its_declared_inputs(
        self, world: World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Source-checkout boundary: every file the builder reads is under the
        checkout, the corpus, the manifest or the capability document."""
        opened: list[Path] = []
        original = Path.read_bytes

        def spy(self: Path) -> bytes:
            opened.append(self.resolve())
            return original(self)

        monkeypatch.setattr(Path, "read_bytes", spy)
        inputs = world.inputs(tmp_path / "site")

        build_site(inputs)

        allowed = (
            world.checkout.resolve(),
            world.corpus.resolve(),
            inputs.corpus_manifest.resolve(),
            inputs.capabilities.resolve(),
            (tmp_path / "site").resolve(),
        )
        assert opened
        for path in opened:
            assert any(path == root or root in path.parents for root in allowed), path


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


class TestCommitAllHelper:
    def test_fixture_commit_is_pinned(self, tmp_path: Path) -> None:
        run_git(tmp_path, "init", "-q")
        (tmp_path / "a").write_text("a", encoding="utf-8")

        first = commit_all(tmp_path)

        assert re.fullmatch(r"[0-9a-f]{40}", first)
