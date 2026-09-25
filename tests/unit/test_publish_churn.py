"""No unrelated evidence-page churn across a corpus update (ADR-0013 Validation).

Two builds from two real commits of one throwaway corpus, diffed file by
file. A content-only update to one document may change only that document's
pages and companions, the sitemap files that list it and the site manifest.
An identity or membership update may also move its deterministic dependants
(browse indexes, redirect maps, sitemap membership), and nothing outside that
set. Each test also asserts the change it made is visible in the output, so an
emitter that silently dropped the update cannot pass by changing nothing.
"""

import json
import os
import subprocess
from pathlib import Path

from lovspor.publish.emit import emit_site
from tests.unit.test_publish_emit import DOC, _record, corpus  # noqa: F401 — fixture reuse

SITE_MANIFEST = Path("site-manifest.json")
SITEMAP_INDEX = Path("sitemap.xml")
REDIRECT_ARTIFACTS = frozenset({Path("redirect-map.json"), Path("redirects.caddy")})


def _commit_later(repo: Path, message: str) -> str:
    """Commit on a later day than the fixture, so lastmod-bearing artifacts can move."""
    later = "2026-03-01T00:00:00Z"
    env = {
        **os.environ,
        "GIT_AUTHOR_DATE": later,
        "GIT_COMMITTER_DATE": later,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=repo, check=True, capture_output=True, env=env
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return head.stdout.strip()


def _tree(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _changed(before: dict[Path, bytes], after: dict[Path, bytes]) -> set[Path]:
    return {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}


def _under(path: Path, prefix: str) -> bool:
    return path.parts[: len(Path(prefix).parts)] == Path(prefix).parts


def _sitemaps_listing(tree: dict[Path, bytes], url_fragment: str) -> set[Path]:
    return {
        path
        for path, data in tree.items()
        if path.suffix == ".xml" and url_fragment.encode("utf-8") in data
    }


def _build_pair(repo: Path, shas: tuple[str, str], tmp_path: Path) -> tuple[dict, dict]:
    emit_site(repo, shas[0], tmp_path / "before")
    emit_site(repo, shas[1], tmp_path / "after")
    return _tree(tmp_path / "before"), _tree(tmp_path / "after")


def _set_manifest_title(repo: Path, doc_id: str, title: str) -> None:
    manifest_path = repo / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"][doc_id]["title"] = title
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


class TestContentOnlyChurn:
    def test_a_content_edit_to_one_document_touches_only_its_own_artifacts(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, pinned = corpus
        (repo / "lover/testloven.md").write_text(
            DOC.replace("Tekst to.", "Tekst tre."),
            encoding="utf-8",
        )
        updated = _commit_later(repo, "content-only update")

        before, after = _build_pair(repo, (pinned, updated), tmp_path)
        changed = _changed(before, after)

        allowed_sitemaps = _sitemaps_listing(before, "/lov/testloven/") | {SITEMAP_INDEX}
        outside = {
            path
            for path in changed
            if not _under(path, "lov/testloven")
            and path != SITE_MANIFEST
            and path not in allowed_sitemaps
        }
        assert outside == set()
        assert Path("lov/testloven/paragraf/2/index.html") in changed
        assert b"Tekst tre." in after[Path("lov/testloven/paragraf/2/index.html")]

    def test_a_content_edit_leaves_the_browse_index_and_redirects_byte_identical(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, pinned = corpus
        (repo / "lover/testloven.md").write_text(
            DOC.replace("Tekst to.", "Tekst tre."),
            encoding="utf-8",
        )
        updated = _commit_later(repo, "content-only update")

        before, after = _build_pair(repo, (pinned, updated), tmp_path)
        changed = _changed(before, after)

        assert Path("lov/index.html") not in changed
        assert changed.isdisjoint(REDIRECT_ARTIFACTS)
        assert Path("sitemaps/forskrifter-1.xml") not in changed


class TestIdentityChurn:
    def test_a_title_change_moves_nothing_outside_its_dependant_set(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, pinned = corpus
        _set_manifest_title(repo, "doc-4", "Vimpel omdøpt")
        updated = _commit_later(repo, "identity update: title")

        before, after = _build_pair(repo, (pinned, updated), tmp_path)
        changed = _changed(before, after)

        dependants = (
            _sitemaps_listing(before, "/lov/vimpel-f")
            | {SITEMAP_INDEX, SITE_MANIFEST, Path("lov/index.html")}
            | REDIRECT_ARTIFACTS
        )
        own = {path for path in changed if _under(path, "lov/vimpel-føring")}
        outside = changed - own - dependants
        assert outside == set()
        assert Path("lov/index.html") in changed
        assert "Vimpel omdøpt".encode() in after[Path("lov/index.html")]

    def test_adding_a_document_leaves_every_existing_evidence_page_byte_identical(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, pinned = corpus
        added = DOC.replace('"Testloven"', '"Nyloven"')
        added = added.replace("lov/2020-01-01-1", "lov/2024-05-05-5")
        (repo / "lover/nyloven.md").write_text(added, encoding="utf-8")
        manifest = json.loads((repo / "manifest.json").read_text(encoding="utf-8"))
        manifest["documents"]["doc-5"] = _record("lov", "lover/nyloven.md", "nyloven", "Nyloven")
        (repo / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        updated = _commit_later(repo, "membership update: new document")

        before, after = _build_pair(repo, (pinned, updated), tmp_path)
        changed = _changed(before, after)

        evidence = {path for path in before if path.name in {"index.html", "index.json"}}
        browse = {path for path in evidence if len(path.parts) == 2}
        touched_evidence = (changed & evidence) - browse
        assert touched_evidence == set()
        assert Path("lov/nyloven/index.html") in after
        assert Path("lov/index.html") in changed

    def test_removing_a_document_leaves_every_surviving_evidence_page_byte_identical(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, pinned = corpus
        (repo / "lover/testloven.md").unlink()
        manifest_path = repo / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["documents"]["doc-1"]["status"] = "removed"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        updated = _commit_later(repo, "membership update: removed document")

        before, after = _build_pair(repo, (pinned, updated), tmp_path)
        changed = _changed(before, after)

        surviving_evidence = {
            path
            for path in before.keys() & after.keys()
            if path.name in {"index.html", "index.json"} and len(path.parts) > 2
        }
        assert changed.isdisjoint(surviving_evidence)
        assert not any(_under(path, "lov/testloven") for path in after)
        assert Path("lov/testloven/index.html") in changed
        assert Path("lov/index.html") in changed
