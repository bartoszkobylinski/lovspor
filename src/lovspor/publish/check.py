"""Pre-serve validation of one emitted release tree (ADR-0013 Decision 8).

This is the gate the release script runs between "the tree is on disk" and
"the symlink moves". It answers one question: *are the bytes about to be
served a complete, self-consistent release?* — the question a partial build,
a wrong ``--out``, an rsync that stopped halfway, or a hard-link that landed
on the wrong inode would leave open.

It is deliberately not the ADR-0013 validation suite (#242). That suite
proves the *generator's* contracts — determinism across machines, churn
bounds, closure of ``source_revision`` under the pinned commit — on fixtures,
in CI, before a build exists. This runs on the production tree, every
release, and checks only what can be checked from the tree alone. The two
overlap on nothing except the representation hash, which is cheap here and
is exactly the check that catches a corrupted copy.

Every failure is a :class:`~lovspor.publish.inventory.PublishError` with the
first offending path named, so the operator reads one line rather than a
diff of ninety thousand files.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from lovspor.publish.companion import SCHEMA_VERSION
from lovspor.publish.inventory import PublishError
from lovspor.publish.pages import SITE_ORIGIN

# The corpus namespaces the generator owns (emit._clear_previous_build). Anything
# else under the root — the landing page, /observatory — is not this release's.
_ROUTES = ("lov", "forskrift")

_LOC = re.compile(r"<loc>([^<]+)</loc>")
_SHA = re.compile(r"^[0-9a-f]{40}$")

# A document page is ``<route>/<slug>/index.html``: exactly three path parts
# below the root. Provision pages sit deeper (``.../paragraf/<pid>/index.html``).
_DOCUMENT_DEPTH = 3


@dataclass(frozen=True)
class ReleaseReport:
    """What a release tree contained when it passed."""

    corpus_commit: str
    documents: int
    pages: int
    sitemap_urls: int
    redirects: int

    def summary(self) -> str:
        return (
            f"release ok: corpus {self.corpus_commit[:12]}, {self.documents} documents, "
            f"{self.pages} pages, {self.sitemap_urls} sitemap URLs, "
            f"{self.redirects} redirects"
        )


def check_release(root: Path) -> ReleaseReport:
    """Validate the release tree at ``root``; raise :class:`PublishError` on the
    first inconsistency, return a :class:`ReleaseReport` otherwise."""
    corpus_commit, promised = _manifest(root)
    _require(root, "robots.txt")
    _require(root, "sitemap.xml")
    pages = _pages(root)
    documents = [page for page in pages if len(page.relative_to(root).parts) == _DOCUMENT_DEPTH]
    if len(documents) != promised:
        raise PublishError(
            f"site-manifest.json promises {promised} documents, "
            f"the tree has {len(documents)} document pages"
        )
    for page in pages:
        _check_twin(root, page)
    sitemap_urls = _check_sitemaps(root)
    redirects = _check_redirects(root)
    return ReleaseReport(
        corpus_commit=corpus_commit,
        documents=len(documents),
        pages=len(pages),
        sitemap_urls=sitemap_urls,
        redirects=redirects,
    )


def _manifest(root: Path) -> tuple[str, int]:
    """``(corpus_commit, documents)`` from a manifest this engine can serve."""
    path = root / "site-manifest.json"
    if not path.is_file():
        raise PublishError(f"{path} is missing: not a release tree")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"{path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"{path} is not a JSON object")
    if data.get("site_schema_version") != SCHEMA_VERSION:
        raise PublishError(
            f"{path} has site_schema_version {data.get('site_schema_version')!r}; "
            f"this engine serves {SCHEMA_VERSION!r}"
        )
    commit = data.get("corpus_commit")
    if not isinstance(commit, str) or not _SHA.match(commit):
        raise PublishError(f"{path} names no full corpus commit: {commit!r}")
    documents = data.get("documents")
    if not isinstance(documents, int) or documents < 0:
        raise PublishError(f"{path} has no document count: {documents!r}")
    return commit, documents


def _require(root: Path, name: str) -> None:
    if not (root / name).is_file():
        raise PublishError(f"{root / name} is missing")


def _pages(root: Path) -> list[Path]:
    """Every emitted page under the corpus namespaces, browse indexes excluded.

    A page is a directory's ``index.html``. ``lov/index.html`` is the A-to-Å browse
    page, not a document, so only paths at least two levels below a route count.
    """
    found: list[Path] = []
    for route in _ROUTES:
        base = root / route
        if not base.is_dir():
            continue
        for page in sorted(base.rglob("index.html")):
            if page.parent != base:
                found.append(page)
    if not found:
        raise PublishError(f"{root} contains no document pages under {'/'.join(_ROUTES)}")
    return found


def _check_twin(root: Path, page: Path) -> None:
    """The JSON twin must exist and its hash must be of *these* HTML bytes.

    This is the one check shared with the CI suite, kept because it is the
    check that a copy error trips: a twin from one release beside HTML from
    another is exactly the mixed snapshot the atomic switch exists to prevent.
    """
    twin = page.with_name("index.json")
    if not twin.is_file():
        raise PublishError(f"{page.relative_to(root)} has no index.json twin")
    try:
        data = json.loads(twin.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"{twin.relative_to(root)} is unreadable: {exc}") from exc
    provenance = data.get("provenance") if isinstance(data, dict) else None
    recorded = provenance.get("representation_hash") if isinstance(provenance, dict) else None
    actual = hashlib.sha256(page.read_bytes()).hexdigest()
    if recorded != actual:
        raise PublishError(
            f"{page.relative_to(root)}: twin records representation_hash {recorded!r}, "
            f"the HTML hashes to {actual}"
        )


def _path_for(url: str) -> str | None:
    """Map a canonical URL to the tree-relative file that serves it, or ``None``
    if the URL is foreign."""
    if not url.startswith(SITE_ORIGIN + "/"):
        return None
    return _relative_file(url[len(SITE_ORIGIN) :])


def _relative_file(site_path: str) -> str:
    """The file Caddy would serve for a site-relative path."""
    if site_path.endswith("/"):
        return site_path.lstrip("/") + "index.html"
    return site_path.lstrip("/")


def _served_file(root: Path, relative: str | None) -> Path | None:
    """The file inside ``root`` that ``relative`` names, or ``None`` when it does
    not exist *within the tree*.

    A path that resolves outside the release — ``/../outside/`` — is refused
    even when something exists there. The sitemap and the redirect map are
    inputs to Caddy; a release must not be able to point crawlers or clients
    at a file it does not own, and the check must not be satisfiable by a file
    that merely happens to sit beside the tree. Found by the CI test author on
    PR #257.
    """
    if relative is None:
        return None
    candidate = (root / relative).resolve()
    base = root.resolve()
    if candidate != base and base not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _check_sitemaps(root: Path) -> int:
    """Every URL the sitemaps advertise must be served by a file in this tree.

    A sitemap naming a page that is not here is the crawler-facing form of a
    mixed snapshot — the exact thing Decision 8 forbids — and the sitemap is
    the artifact search engines read first.
    """
    index = (root / "sitemap.xml").read_text(encoding="utf-8")
    listed = _LOC.findall(index)
    if not listed:
        raise PublishError("sitemap.xml lists no sitemaps")
    total = 0
    for sitemap_url in listed:
        sitemap = _served_file(root, _path_for(sitemap_url))
        if sitemap is None:
            raise PublishError(f"sitemap.xml lists {sitemap_url}, which is not in the tree")
        name = sitemap.relative_to(root.resolve())
        for url in _LOC.findall(sitemap.read_text(encoding="utf-8")):
            if _served_file(root, _path_for(url)) is None:
                raise PublishError(f"{name} lists {url}, which is not in the tree")
            total += 1
    return total


def _check_redirects(root: Path) -> int:
    """Every 301 must land on a page this release serves.

    Redirect targets are derived from lineage at build time, so a dangling one
    means the map and the pages came from different builds.
    """
    path = root / "redirect-map.json"
    if not path.is_file():
        raise PublishError(f"{path} is missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PublishError(f"{path} is unreadable: {exc}") from exc
    redirects = data.get("redirects", []) if isinstance(data, dict) else []
    for entry in redirects:
        target = entry.get("to") if isinstance(entry, dict) else None
        if not isinstance(target, str):
            raise PublishError(f"redirect-map.json has an entry without a target: {entry!r}")
        if not target.startswith("/"):
            raise PublishError(f"redirect target {target!r} is not a site-relative path")
        if _served_file(root, _relative_file(target)) is None:
            raise PublishError(f"redirect target {target} is not in the tree")
    return len(redirects)
