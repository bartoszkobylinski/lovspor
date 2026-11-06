"""A published URL never silently disappears between releases (#747, ADR-0013 Decision 6).

Two synthetic snapshots of one throwaway corpus, each built into a release
tree with the real emitter. Every URL the first release served as a page or
a JSON twin must, in the second, be answered by Caddy as one of exactly three
things: a live page, a 301 to a live page proven to be the same entity, or an
explicit 410. A 404 for a URL that was once canonical is a broken citation,
and is the failure these tests exist to catch.

What a 410 is not: an archive. It promises that the URL was retired, never
that the retired text is available anywhere; no test here asks a 410 to carry
a body.

Caddy's answer is modelled from the emitted ``redirects.caddy`` with the
order the snippet's own header relies on: an exact ``redir`` wins over the
``@lovspor_gone`` 410 catch-all, which wins over the file server. The model is
pinned to the snippet's three line shapes by ``publish.check``.
"""

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.publish.check import check_continuity, check_release
from lovspor.publish.emit import emit_site
from lovspor.publish.inventory import PublishError, PublishInventory, build_inventory
from lovspor.publish.pages import SITE_ORIGIN
from lovspor.publish.redirects import RedirectMap, caddy_snippet, validate_redirect_targets
from lovspor.snapshot import CorpusSnapshot
from tests.unit.cli_output import said
from tests.unit.test_publish_redirects import _doc, _git, _head, _record, _repo, _write_manifest

_REDIR = re.compile(r"^redir (\S+) (\S+) 301$")
_GONE = re.compile(r"^@lovspor_gone path (.+)$")
_CANONICAL = re.compile(r'<link rel="canonical" href="([^"]+)">')

LIVE, MOVED, GONE, MISSING = 200, 301, 410, 404

runner = CliRunner()


def _commit(repo: Path, documents: dict[str, dict[str, object]], day: int) -> str:
    _write_manifest(repo, documents)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"day {day}", day=day)
    return _head(repo)


def _build(repo: Path, sha: str, out: Path) -> Path:
    emit_site(repo, sha, out)
    return out


def _caddy(root: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    redirects: dict[str, str] = {}
    gone: list[str] = []
    for line in (root / "redirects.caddy").read_text(encoding="utf-8").splitlines():
        if match := _REDIR.match(line):
            redirects[match.group(1)] = match.group(2)
        elif match := _GONE.match(line):
            gone.extend(p for p in match.group(1).split() if not p.endswith("*"))
    return redirects, tuple(gone)


def _file_for(root: Path, path: str) -> Path:
    return root / (path.lstrip("/") + ("index.html" if path.endswith("/") else ""))


def answer(root: Path, path: str) -> tuple[int, str | None]:
    """What Caddy serving ``root`` answers for ``path``."""
    redirects, gone = _caddy(root)
    if path in redirects:
        return MOVED, redirects[path]
    if any(path.startswith(prefix) for prefix in gone):
        return GONE, None
    return (LIVE, None) if _file_for(root, path).is_file() else (MISSING, None)


def published(root: Path) -> set[str]:
    """Every page and JSON-twin URL path a release serves under the corpus routes."""
    urls: set[str] = set()
    for route in ("lov", "forskrift"):
        for page in (root / route).rglob("index.html"):
            if page.parent == root / route:
                continue
            directory = "/" + page.parent.relative_to(root).as_posix() + "/"
            urls.update({directory, directory + "index.json"})
    return urls


def broken(previous: Path, current: Path) -> dict[str, int]:
    """Previously published URLs the current release answers with a 404 or a
    301 that does not land on a live page."""
    found: dict[str, int] = {}
    for url in sorted(published(previous)):
        status, target = answer(current, url)
        if status == MISSING or (
            status == MOVED and (target is None or answer(current, target)[0] != LIVE)
        ):
            found[url] = status
    return found


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _repo(tmp_path)


def _two_releases(
    repo: Path,
    tmp_path: Path,
    snapshots: tuple[dict[str, str], dict[str, str]],
) -> tuple[Path, Path]:
    """Write each snapshot's ``{path: markdown}`` (all lover, slug = stem) and
    build both; files absent from the second snapshot are deleted."""
    roots: list[Path] = []
    for day, files in enumerate(snapshots, start=1):
        for stale in (repo / "lover").glob("*.md"):
            if f"lover/{stale.name}" not in files:
                stale.unlink()
        for path, text in files.items():
            (repo / path).write_text(text, encoding="utf-8")
        records = {
            f"doc-{Path(path).stem}": _record("lov", path, Path(path).stem) for path in files
        }
        sha = _commit(repo, records, day)
        roots.append(_build(repo, sha, tmp_path / f"release-{day}"))
    return roots[0], roots[1]


class TestEveryPublishedUrlSurvives:
    @pytest.mark.parametrize(("folder", "route"), [("lover", "lov"), ("forskrifter", "forskrift")])
    def test_a_restored_pid_is_live_without_a_stale_410(
        self, repo: Path, tmp_path: Path, folder: str, route: str
    ) -> None:
        """A historical retirement must never shadow a provision emitted again (§4)."""
        path = f"{folder}/a.md"
        documents = {"doc-a": _record(route, path, "a")}
        releases = []
        for day, pids in enumerate((("1", "35 a"), ("1",), ("1", "35 a")), start=1):
            (repo / path).write_text(_doc("A", f"{route}/2020-01-01-1", pids), encoding="utf-8")
            sha = _commit(repo, documents, day)
            releases.append(_build(repo, sha, tmp_path / f"snapshot-{day}"))

        original, retired, restored = releases
        url = f"/{route}/a/paragraf/35a/"
        for representation in (url, url + "index.json"):
            assert answer(retired, representation) == (GONE, None)
            assert answer(restored, representation) == (LIVE, None)
        check_release(restored)
        report = check_continuity(original, restored)
        assert report.live == len(published(original))
        assert (report.moved, report.gone) == (0, 0)

    def test_a_content_edit_keeps_every_url_live(self, repo: Path, tmp_path: Path) -> None:
        before, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2")).replace("Tekst", "Ny")},
            ),
        )
        assert broken(before, after) == {}
        assert {answer(after, url)[0] for url in published(before)} == {LIVE}

    def test_a_provision_dropped_from_a_live_document_answers_410(
        self, repo: Path, tmp_path: Path
    ) -> None:
        before, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2", "3"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "3"))},
            ),
        )
        assert broken(before, after) == {}
        assert answer(after, "/lov/a/paragraf/2/") == (GONE, None)
        assert answer(after, "/lov/a/paragraf/2/index.json") == (GONE, None)
        assert answer(after, "/lov/a/paragraf/3/") == (LIVE, None)

    def test_a_document_that_turns_duplicate_pid_retires_its_provision_urls(
        self, repo: Path, tmp_path: Path
    ) -> None:
        before, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2", "1"))},
            ),
        )
        assert broken(before, after) == {}
        assert answer(after, "/lov/a/") == (LIVE, None)
        assert answer(after, "/lov/a/paragraf/1/") == (GONE, None)
        assert answer(after, "/lov/a/paragraf/2/index.json") == (GONE, None)

    def test_a_reused_slug_retires_the_old_documents_pids(self, repo: Path, tmp_path: Path) -> None:
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "9")))
        _commit(repo, {"doc-a": _record("lov", "lover/a.md", "a")}, day=1)
        (repo / "lover/a.md").unlink()
        (repo / "lover/b.md").write_text(_doc("B", "lov/2021-01-01-1", ("1",)))
        _commit(repo, {"doc-b": _record("lov", "lover/b.md", "b")}, day=2)
        (repo / "lover/a.md").write_text(_doc("A2", "lov/2022-01-01-1", ("1",)))
        sha = _commit(
            repo,
            {
                "doc-b": _record("lov", "lover/b.md", "b"),
                "doc-a2": _record("lov", "lover/a.md", "a"),
            },
            day=3,
        )
        after = _build(repo, sha, tmp_path / "after")

        assert answer(after, "/lov/a/") == (LIVE, None)
        assert answer(after, "/lov/a/paragraf/9/") == (GONE, None)
        assert answer(after, "/lov/a/paragraf/1/") == (LIVE, None)

    def test_a_pid_only_ever_in_colliding_versions_is_not_retired(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """Removed-heading evidence alone is not enough: such a pid never had a page."""
        _, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "1", "8"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2"))},
            ),
        )
        assert answer(after, "/lov/a/paragraf/8/") == (MISSING, None)
        assert answer(after, "/lov/a/paragraf/2/") == (LIVE, None)


class TestMovedUrlsKeepTheirIdentity:
    @pytest.fixture
    def releases(self, repo: Path, tmp_path: Path) -> tuple[Path, Path]:
        before_files = {"lover/gammel.md": _doc("G", "lov/2020-01-01-1", ("1", "2"))}
        (repo / "lover/gammel.md").write_text(before_files["lover/gammel.md"])
        sha = _commit(repo, {"doc-1": _record("lov", "lover/gammel.md", "gammel")}, day=1)
        before = _build(repo, sha, tmp_path / "before")
        _git(repo, "mv", "lover/gammel.md", "lover/ny.md")
        (repo / "lover/ny.md").write_text(_doc("G", "lov/2020-01-01-1", ("1", "3")))
        sha = _commit(repo, {"doc-1": _record("lov", "lover/ny.md", "ny")}, day=2)
        return before, _build(repo, sha, tmp_path / "after")

    def test_a_rename_leaves_nothing_broken(self, releases: tuple[Path, Path]) -> None:
        before, after = releases
        assert broken(before, after) == {}

    def test_a_surviving_provision_moves_to_the_same_pid(self, releases: tuple[Path, Path]) -> None:
        _, after = releases
        assert answer(after, "/lov/gammel/") == (MOVED, "/lov/ny/")
        assert answer(after, "/lov/gammel/paragraf/1/") == (MOVED, "/lov/ny/paragraf/1/")
        assert answer(after, "/lov/gammel/paragraf/2/") == (GONE, None)

    def test_a_json_twin_shares_its_pages_fate(self, releases: tuple[Path, Path]) -> None:
        """HTML and JSON name one entity: a moved page's twin moves to the
        target's twin, never to 410 while the page answers 301."""
        _, after = releases
        assert answer(after, "/lov/gammel/index.json") == (MOVED, "/lov/ny/index.json")
        assert answer(after, "/lov/gammel/paragraf/1/index.json") == (
            MOVED,
            "/lov/ny/paragraf/1/index.json",
        )
        assert answer(after, "/lov/gammel/paragraf/2/index.json") == (GONE, None)

    def test_every_redirect_lands_on_a_live_page_in_one_hop(
        self, releases: tuple[Path, Path]
    ) -> None:
        _, after = releases
        redirects, gone = _caddy(after)
        for source, target in redirects.items():
            assert answer(after, target) == (LIVE, None), (source, target)
            assert target not in redirects
            assert not any(target.startswith(prefix) for prefix in gone)

    def test_a_provision_redirect_never_changes_the_pid(self, releases: tuple[Path, Path]) -> None:
        _, after = releases
        redirects, _ = _caddy(after)
        for source, target in redirects.items():
            assert source.partition("/paragraf/")[2] == target.partition("/paragraf/")[2]


class TestNoRuleShadowsALivePage:
    @pytest.mark.parametrize(
        ("source", "target", "gone"),
        [
            ("/lov/a/", "/lov/a/paragraf/1/", []),
            ("/lov/a/index.json", "/lov/a/paragraf/1/index.json", []),
            (None, None, ["/lov/a/"]),
            (None, None, ["/lov/a/paragraf/1/"]),
        ],
    )
    def test_release_check_refuses_consistent_maps_that_hide_live_files(
        self,
        repo: Path,
        tmp_path: Path,
        source: str | None,
        target: str | None,
        gone: list[str],
    ) -> None:
        """URL contract §4: matching JSON and Caddy rules still cannot hide a page."""
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1",)))
        sha = _commit(repo, {"doc-a": _record("lov", "lover/a.md", "a")}, day=1)
        release = _build(repo, sha, tmp_path / "release")
        check_release(release)
        entries = [] if source is None else [{"from": source, "to": target}]
        _rewrite_map(release, lambda data: data.update(redirects=entries, gone=gone))

        with pytest.raises(PublishError, match="is served, but the redirect map answers it too"):
            check_release(release)

    def test_no_redirect_source_or_gone_prefix_covers_an_emitted_file(
        self, repo: Path, tmp_path: Path
    ) -> None:
        _, after = _two_releases(
            repo,
            tmp_path,
            (
                {
                    "lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2")),
                    "lover/aa.md": _doc("AA", "lov/2020-01-01-2", ("1",)),
                },
                {"lover/aa.md": _doc("AA", "lov/2020-01-01-2", ("1",))},
            ),
        )
        redirects, gone = _caddy(after)
        for url in published(after):
            assert url not in redirects
            assert not any(url.startswith(prefix) for prefix in gone), url
        assert answer(after, "/lov/a/") == (GONE, None)
        assert answer(after, "/lov/aa/") == (LIVE, None)


class TestIdentityAgreesAcrossRepresentations:
    def test_canonical_link_twin_url_and_location_name_one_url(
        self, repo: Path, tmp_path: Path
    ) -> None:
        _, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1",))},
            ),
        )
        for url in published(after):
            if not url.endswith("/"):
                continue
            html = _file_for(after, url).read_text(encoding="utf-8")
            twin = json.loads(_file_for(after, url + "index.json").read_text(encoding="utf-8"))
            assert _CANONICAL.findall(html) == [SITE_ORIGIN + url]
            assert twin["canonical_url"] == SITE_ORIGIN + url


class TestDeterministicAndQuiet:
    def test_the_same_snapshot_builds_byte_identical_maps(self, repo: Path, tmp_path: Path) -> None:
        _, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2"))},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1",))},
            ),
        )
        again = _build(repo, _head(repo), tmp_path / "again")
        for name in ("redirects.caddy", "redirect-map.json", "lov/a/index.html"):
            assert (after / name).read_bytes() == (again / name).read_bytes()

    def test_dropping_a_provision_leaves_an_unrelated_document_byte_identical(
        self, repo: Path, tmp_path: Path
    ) -> None:
        other = _doc("B", "lov/2021-01-01-1", ("1", "2"))
        before, after = _two_releases(
            repo,
            tmp_path,
            (
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1", "2")), "lover/b.md": other},
                {"lover/a.md": _doc("A", "lov/2020-01-01-1", ("1",)), "lover/b.md": other},
            ),
        )
        for path in (before / "lov/b").rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (after / path.relative_to(before)).read_bytes()


class TestTheContinuityGate:
    """``check_continuity`` and ``publish-check --previous`` on real emitted trees."""

    @pytest.fixture
    def releases(self, repo: Path, tmp_path: Path) -> tuple[Path, Path]:
        (repo / "lover/gammel.md").write_text(_doc("G", "lov/2020-01-01-1", ("1", "2")))
        (repo / "lover/b.md").write_text(_doc("B", "lov/2021-01-01-1", ("1", "2", "3")))
        documents = {
            "doc-1": _record("lov", "lover/gammel.md", "gammel"),
            "doc-b": _record("lov", "lover/b.md", "b"),
        }
        before = _build(repo, _commit(repo, documents, day=1), tmp_path / "before")
        _git(repo, "mv", "lover/gammel.md", "lover/ny.md")
        (repo / "lover/b.md").write_text(_doc("B", "lov/2021-01-01-1", ("1", "3")))
        documents["doc-1"] = _record("lov", "lover/ny.md", "ny")
        return before, _build(repo, _commit(repo, documents, day=2), tmp_path / "after")

    def test_a_rename_and_a_dropped_provision_pass_and_are_counted(
        self, releases: tuple[Path, Path]
    ) -> None:
        before, after = releases
        check_release(after)
        report = check_continuity(before, after)
        assert report.moved == 3 * 2  # gammel/, paragraf/1/, paragraf/2/, each with its twin
        assert report.gone == 1 * 2  # b/paragraf/2/ and its twin
        assert report.live == len(published(before)) - report.moved - report.gone
        assert "none missing" in report.summary()

    def test_a_url_that_would_404_is_refused(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        before, _ = releases
        current = tmp_path / "current"
        shutil.copytree(before, current)
        shutil.rmtree(current / "lov/b/paragraf/3")
        with pytest.raises(PublishError, match=r"/lov/b/paragraf/3/ was served .* 404"):
            check_continuity(before, current)

    def test_a_twin_with_another_fate_than_its_page_is_refused(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        before, after = releases
        current = tmp_path / "current"
        shutil.copytree(after, current)
        _rewrite_map(current, lambda m: m["redirects"].remove(_twin_entry(m)))
        with pytest.raises(PublishError, match="must share one fate"):
            check_continuity(before, current)

    def test_a_previous_redirect_source_must_keep_an_answer(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        _, after = releases
        current = tmp_path / "current"
        shutil.copytree(after, current)
        _rewrite_map(current, lambda m: m.update(redirects=[], gone=[]))
        with pytest.raises(PublishError, match="/lov/gammel/"):
            check_continuity(after, current)

    def test_a_redirect_to_a_page_that_is_not_live_is_refused(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        before, after = releases
        current = tmp_path / "current"
        shutil.copytree(after, current)
        shutil.rmtree(current / "lov/ny/paragraf/1")
        with pytest.raises(PublishError, match="which is not a live page"):
            check_continuity(before, current)

    def test_the_cli_runs_continuity_only_when_asked(self, releases: tuple[Path, Path]) -> None:
        before, after = releases
        plain = runner.invoke(app, ["publish-check", str(after)])
        both = runner.invoke(app, ["publish-check", str(after), "--previous", str(before)])
        assert plain.exit_code == 0
        assert "continuity ok" not in plain.output
        assert both.exit_code == 0, both.output
        assert "continuity ok" in both.output

    def test_the_cli_refuses_a_release_that_loses_a_url(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        before, _ = releases
        current = tmp_path / "current"
        shutil.copytree(before, current)
        shutil.rmtree(current / "lov/b/paragraf/3")
        _rebuild_sitemaps_without(current, "/lov/b/paragraf/3/")
        result = runner.invoke(app, ["publish-check", str(current), "--previous", str(before)])
        assert result.exit_code == 1
        assert said("would answer 404", result.output)

    def test_a_previous_lone_redirect_source_is_still_checked(
        self, releases: tuple[Path, Path], tmp_path: Path
    ) -> None:
        """A source that is neither a page path nor a paired twin is judged alone."""
        _, after = releases
        previous = tmp_path / "previous"
        shutil.copytree(after, previous)
        _rewrite_map(
            previous, lambda m: m["redirects"].append({"from": "/lov/x", "to": "/lov/ny/"})
        )
        with pytest.raises(PublishError, match="/lov/x was served .* 404"):
            check_continuity(previous, after)


class TestTheReleaseCheckRefusesShadowing:
    def test_a_gone_prefix_over_a_served_page_is_refused(self, repo: Path, tmp_path: Path) -> None:
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1",)))
        root = _build(
            repo, _commit(repo, {"doc-a": _record("lov", "lover/a.md", "a")}, 1), tmp_path / "r"
        )
        _rewrite_map(root, lambda m: m["gone"].append("/lov/a/paragraf/1/"))
        with pytest.raises(PublishError, match="is served, but the redirect map answers it"):
            check_release(root)


def _twin_entry(redirect_map: dict[str, list]) -> dict[str, str]:
    return next(e for e in redirect_map["redirects"] if e["from"] == "/lov/gammel/index.json")


def _rewrite_map(root: Path, edit: Callable[[dict[str, list]], object]) -> None:
    """Edit ``redirect-map.json`` and regenerate the Caddy snippet from it, so
    the two still agree and only the edited fact differs."""
    path = root / "redirect-map.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    redirect_map = RedirectMap(
        redirects=tuple((e["from"], e["to"]) for e in data["redirects"]),
        gone=tuple(data["gone"]),
    )
    (root / "redirects.caddy").write_bytes(caddy_snippet(redirect_map))


def _rebuild_sitemaps_without(root: Path, url: str) -> None:
    for sitemap in (root / "sitemaps").glob("*.xml"):
        text = sitemap.read_text(encoding="utf-8")
        for loc in (url, url + "index.json"):
            text = re.sub(rf"<url><loc>{re.escape(SITE_ORIGIN + loc)}</loc>.*?</url>", "", text)
        sitemap.write_text(text, encoding="utf-8")


class TestBuildValidationRefusesWrongClaims:
    """``validate_redirect_targets`` on hand-built maps over a real inventory."""

    @pytest.fixture
    def inventory(self, repo: Path) -> PublishInventory:
        (repo / "lover/a.md").write_text(_doc("A", "lov/2020-01-01-1", ("1", "2")))
        sha = _commit(repo, {"doc-a": _record("lov", "lover/a.md", "a")}, day=1)
        snapshot = CorpusSnapshot(repo, sha)
        return build_inventory(snapshot.manifest, snapshot.read_text)

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            ("/lov/gammel/paragraf/1/", "/lov/a/paragraf/2/"),
            ("/lov/gammel/paragraf/1/", "/lov/a/"),
            ("/lov/gammel/", "/lov/a/index.json"),
        ],
    )
    def test_a_301_that_changes_pid_or_kind_is_refused(
        self, inventory: PublishInventory, source: str, target: str
    ) -> None:
        redirect_map = RedirectMap(redirects=((source, target),), gone=())
        with pytest.raises(PublishError, match="changes the entity it names"):
            validate_redirect_targets(redirect_map, inventory)

    @pytest.mark.parametrize(
        ("redirects", "gone"),
        [
            ((("/lov/a/paragraf/1/", "/lov/a/paragraf/1/"),), ()),
            ((), ("/lov/a/",)),
            ((), ("/lov/a/paragraf/2/",)),
            ((), ("/lov/",)),
        ],
    )
    def test_a_rule_over_an_emitted_url_is_refused(
        self,
        inventory: PublishInventory,
        redirects: tuple[tuple[str, str], ...],
        gone: tuple[str, ...],
    ) -> None:
        redirect_map = RedirectMap(redirects=redirects, gone=gone)
        with pytest.raises(PublishError, match="is an emitted page, but the redirect map"):
            validate_redirect_targets(redirect_map, inventory)

    def test_a_gone_entry_beside_but_not_over_an_emitted_url_is_accepted(
        self, inventory: PublishInventory
    ) -> None:
        redirect_map = RedirectMap(redirects=(), gone=("/lov/a/paragraf/3/", "/lov/aa/"))
        validate_redirect_targets(redirect_map, inventory)


class TestLoneTwinRedirectSources:
    """A twin redirect source whose page is not itself a redirect source."""

    @staticmethod
    def _release(root: Path, redirects: dict[str, str], pages: tuple[str, ...]) -> Path:
        for page in pages:
            (root / page).mkdir(parents=True)
            (root / page / "index.html").write_text(page)
            (root / page / "index.json").write_text("{}")
        entries = [{"from": k, "to": v} for k, v in redirects.items()]
        (root / "redirect-map.json").write_text(
            json.dumps({"redirects": entries, "gone": []}), encoding="utf-8"
        )
        return root

    def test_a_lone_twin_source_is_judged_and_counted_by_its_kind(self, tmp_path: Path) -> None:
        lone = {"/lov/old/index.json": "/lov/ny/index.json"}
        previous = self._release(tmp_path / "previous", lone, ("lov/base",))
        current = self._release(tmp_path / "current", lone, ("lov/base", "lov/ny"))
        report = check_continuity(previous, current)
        assert (report.live, report.moved, report.gone) == (2, 1, 0)

    def test_a_lone_twin_source_that_would_404_is_refused(self, tmp_path: Path) -> None:
        lone = {"/lov/old/index.json": "/lov/ny/index.json"}
        previous = self._release(tmp_path / "previous", lone, ("lov/base",))
        current = self._release(tmp_path / "current", {}, ("lov/base",))
        with pytest.raises(PublishError, match="/lov/old/index.json was served .* 404"):
            check_continuity(previous, current)
