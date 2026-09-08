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

from lxml import etree

from lovspor.parsing.xml_normalizer import safe_parser
from lovspor.publish.companion import SCHEMA_VERSION
from lovspor.publish.inventory import PublishError
from lovspor.publish.pages import SITE_ORIGIN

# The corpus namespaces the generator owns (emit._clear_previous_build). Anything
# else under the root — the landing page, /observatory — is not this release's.
_ROUTES = ("lov", "forskrift")

_SHA = re.compile(r"^[0-9a-f]{40}$")
# The generator writes every <loc> under the sitemaps.org namespace; the check
# accepts <loc> in any namespace or none, because a crawler would too and the
# question here is whether what would be crawled is served.
_LOC_LOCALNAME = "loc"
# The two line shapes redirects.caddy_snippet emits. Anything else in the
# file is a comment or the 410 `respond`, which carries no path of its own.
_REDIR = re.compile(r"^redir (\S+) (\S+) 301$")
_GONE = re.compile(r"^@lovspor_gone path (.+)$")
_GONE_RESPOND = re.compile(r"^respond @lovspor_gone 410$")

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
    sitemap_urls = _check_sitemaps(root, pages)
    redirects = _check_redirects(root)
    _check_caddy_map(root)
    return ReleaseReport(
        corpus_commit=corpus_commit,
        documents=len(documents),
        pages=len(pages),
        sitemap_urls=sitemap_urls,
        redirects=redirects,
    )


def _read_text(root: Path, path: Path) -> str:
    """UTF-8 text of ``path``, or a :class:`PublishError` naming it.

    Every artifact the generator writes is UTF-8; a byte that is not is a
    corrupted copy, and the check's contract is one named refusal, never a
    traceback from inside the codec.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PublishError(f"{path.relative_to(root)} is unreadable: {exc}") from exc


def _read_object(root: Path, path: Path) -> dict[str, object]:
    """``path`` parsed as a JSON object, or a :class:`PublishError` naming it."""
    try:
        data = json.loads(_read_text(root, path))
    except ValueError as exc:
        raise PublishError(f"{path.relative_to(root)} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise PublishError(f"{path.relative_to(root)} is not a JSON object")
    return data


def _manifest(root: Path) -> tuple[str, int]:
    """``(corpus_commit, documents)`` from a manifest this engine can serve."""
    path = root / "site-manifest.json"
    if not path.is_file():
        raise PublishError(f"{path} is missing: not a release tree")
    data = _read_object(root, path)
    if data.get("site_schema_version") != SCHEMA_VERSION:
        raise PublishError(
            f"{path} has site_schema_version {data.get('site_schema_version')!r}; "
            f"this engine serves {SCHEMA_VERSION!r}"
        )
    commit = data.get("corpus_commit")
    if not isinstance(commit, str) or not _SHA.match(commit):
        raise PublishError(f"{path} names no full corpus commit: {commit!r}")
    documents = data.get("documents")
    # bool is a subclass of int, so `True` would pass an isinstance check and
    # then equal a one-document tree. A release size is not a truth value.
    if isinstance(documents, bool) or not isinstance(documents, int) or documents < 0:
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
    data = _read_object(root, twin)
    provenance = data.get("provenance")
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


def _locs(root: Path, path: Path) -> list[str]:
    """Every ``<loc>`` in a sitemap document, parsed as XML.

    Parsed rather than pattern-matched so a truncated file fails as malformed
    XML instead of passing on whichever ``<loc>`` elements survived the cut —
    a partial copy can end after a complete element. The project's hardened
    parser is used: a sitemap is release data, not trusted input.
    """
    try:
        tree = etree.fromstring(path.read_bytes(), parser=safe_parser())
    except (OSError, etree.XMLSyntaxError) as exc:
        raise PublishError(f"{path.relative_to(root)} is unreadable: {exc}") from exc
    return [
        (element.text or "").strip()
        for element in tree.iter()
        if isinstance(element.tag, str) and etree.QName(element).localname == _LOC_LOCALNAME
    ]


def _check_sitemaps(root: Path, pages: list[Path]) -> int:
    """The sitemaps and the tree must name the same pages — in both directions.

    Listed-but-absent is the crawler-facing form of a mixed snapshot; emitted-
    but-unlisted is a sitemap from a smaller build beside pages from a larger
    one, which a listed-only check would bless as long as every surviving URL
    still resolved. The sitemap is the artifact search engines read first, so
    it is compared as a set against every page under the corpus namespaces,
    browse indexes included.
    """
    listed = _locs(root, root / "sitemap.xml")
    if not listed:
        raise PublishError("sitemap.xml lists no sitemaps")
    advertised: set[Path] = set()
    for sitemap_url in listed:
        sitemap = _served_file(root, _path_for(sitemap_url))
        if sitemap is None:
            raise PublishError(f"sitemap.xml lists {sitemap_url}, which is not in the tree")
        name = sitemap.relative_to(root.resolve())
        for url in _locs(root, sitemap):
            served = _served_file(root, _path_for(url))
            if served is None:
                raise PublishError(f"{name} lists {url}, which is not in the tree")
            advertised.add(served)
    base = root.resolve()
    emitted = {page.resolve() for page in pages}
    emitted.update(base / route / "index.html" for route in _ROUTES if (base / route).is_dir())
    unlisted = sorted(emitted - advertised)
    if unlisted:
        raise PublishError(
            f"{len(unlisted)} emitted page(s) appear in no sitemap, first: "
            f"{unlisted[0].relative_to(base)}"
        )
    return len(advertised)


def _redirect_map(root: Path) -> tuple[set[tuple[str, str]], set[str]]:
    """``(redirects, gone)`` from ``redirect-map.json``, fully shape-checked.

    Every field is untrusted release data. The shape is validated here, once,
    so that neither consumer below can meet a malformed entry as a KeyError or
    an unhashable value — each such escape on this PR was one more field the
    consumers had been trusting.
    """
    path = root / "redirect-map.json"
    if not path.is_file():
        raise PublishError(f"{path} is missing")
    data = _read_object(root, path)
    entries = data.get("redirects", [])
    gone = data.get("gone", [])
    if not isinstance(entries, list) or not isinstance(gone, list):
        raise PublishError("redirect-map.json: 'redirects' and 'gone' must be lists")
    redirects: set[tuple[str, str]] = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("from"), str)
            or not isinstance(entry.get("to"), str)
        ):
            raise PublishError(
                f"redirect-map.json: redirect entry is not {{from: str, to: str}}: {entry!r}"
            )
        redirects.add((entry["from"], entry["to"]))
    for prefix in gone:
        if not isinstance(prefix, str):
            raise PublishError(f"redirect-map.json: gone prefix is not a string: {prefix!r}")
    return redirects, set(gone)


def _check_redirects(root: Path) -> int:
    """Every 301 must land on a page this release serves.

    Redirect targets are derived from lineage at build time, so a dangling one
    means the map and the pages came from different builds.
    """
    redirects, _ = _redirect_map(root)
    for _source, target in sorted(redirects):
        if not target.startswith("/"):
            raise PublishError(f"redirect target {target!r} is not a site-relative path")
        if _served_file(root, _relative_file(target)) is None:
            raise PublishError(f"redirect target {target} is not in the tree")
    return len(redirects)


def _check_caddy_map(root: Path) -> None:
    """``redirects.caddy`` must exist and say what ``redirect-map.json`` says.

    Caddy imports the snippet; the JSON is what the checks above read. Blessing
    the JSON while the snippet is absent or differs would validate an artifact
    Caddy never sees and serve pages from one build under redirects from
    another — the mixed snapshot the switch exists to prevent. Found by the CI
    test author on PR #257.
    """
    snippet = root / "redirects.caddy"
    if not snippet.is_file():
        raise PublishError(f"{snippet} is missing: Caddy would serve no redirects")
    served_redirects: set[tuple[str, str]] = set()
    served_gone: set[str] = set()
    for number, raw in enumerate(_read_text(root, snippet).splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if match := _REDIR.match(line):
            served_redirects.add((match.group(1), match.group(2)))
        elif match := _GONE.match(line):
            # "prefix prefix*" pairs; the bare prefix is the namespace.
            served_gone.update(p for p in match.group(1).split() if not p.endswith("*"))
        elif not _GONE_RESPOND.match(line):
            # Caddy `import`s this file, so any directive in it runs with Caddy's
            # authority. The generator writes exactly three shapes; a fourth is
            # not a redirect map any more, whatever `caddy validate` says of it.
            raise PublishError(
                f"redirects.caddy line {number} is not a redirect-map directive: {line!r}"
            )
    expected_redirects, expected_gone = _redirect_map(root)
    if served_redirects != expected_redirects or served_gone != expected_gone:
        raise PublishError(
            "redirects.caddy disagrees with redirect-map.json: "
            f"{len(served_redirects)} vs {len(expected_redirects)} redirects, "
            f"{len(served_gone)} vs {len(expected_gone)} gone prefixes"
        )
