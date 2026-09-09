"""``publish-check`` for the envelope (ADR-0014 Decision 6 steps 2 and 5; Validation).

Two entry points, both on a directory that is *not yet* served:

* :func:`check_structure` — step 2, the part that does not depend on
  the id: both trees present, the site's root files present, the
  capability document valid under its closed schema (its ``state``
  recomputes by construction), and the cross-tree assertions —
  ``site/site-facts.json`` names the corpus commit and the manifest hash
  of ``corpus/site-manifest.json`` beside it, and the capability hash of
  the document it carries.
* :func:`check_envelope` — step 5, the final check: the structure again,
  ADR-0013's ``check_release`` on ``corpus/``, the site tree's route
  closure re-scanned from disk, and the id: ``release_content_id``
  recomputed over both trees equals ``site-facts.json``'s, equals
  ``release.json``'s, equals the id in ``release.caddy``'s ``vars``
  directive, whose every path names the directory the envelope will
  occupy under that id.

Every refusal is one named line. A directory under an id name whose
id-carrying files are absent is an incomplete envelope, never a pass.
"""

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.publish.check import ReleaseReport, check_release
from lovspor.publish.inventory import PublishError
from lovspor.release.envelope import (
    CORPUS_DIR,
    SITE_DIR,
    ReleaseRecord,
    fragment_paths,
    fragment_release_id,
    is_complete,
    is_release_id,
    missing_parts,
    read_fragment,
    read_record,
)
from lovspor.release.errors import EnvelopeError, IncompleteEnvelopeError
from lovspor.site.build import CAPABILITIES_NAME, FACTS_NAME, SITEMAP_NAME
from lovspor.site.capabilities import CapabilityDocument, load_capabilities, state_sha256
from lovspor.site.errors import SiteBuildError
from lovspor.site.fingerprint import ReleaseKey, release_content_id
from lovspor.site.routes import emitted_pages
from lovspor.site.scan import check_links, scan_page
from lovspor.site.sitemap import sitemap_site_xml
from lovspor.site.sources import CorpusManifest

MANIFEST_NAME = "site-manifest.json"


class Structure(NamedTuple):
    """What the structural check read, for the steps that follow it."""

    facts: dict[str, object]
    manifest: CorpusManifest
    document: CapabilityDocument
    release_key: ReleaseKey


class EnvelopeReport(BaseModel):
    """What the envelope contained when it passed."""

    model_config = ConfigDict(frozen=True)

    release_content_id: str
    corpus: ReleaseReport
    site_pages: int

    def summary(self) -> str:
        return (
            f"envelope ok: release {self.release_content_id[:12]}, {self.corpus.summary()}, "
            f"{self.site_pages} site pages"
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        return path.read_bytes()
    except OSError as error:
        raise EnvelopeError(f"{relative} is unreadable: {error}") from error


def _facts(root: Path) -> dict[str, object]:
    try:
        facts = json.loads(_read(root, f"{SITE_DIR}/{FACTS_NAME}"))
    except ValueError as error:
        raise EnvelopeError(f"{SITE_DIR}/{FACTS_NAME} is not JSON: {error}") from error
    if not isinstance(facts, dict):
        raise EnvelopeError(f"{SITE_DIR}/{FACTS_NAME} is not a JSON object")
    return facts


def _fact(facts: dict[str, object], key: str) -> str:
    value = facts.get(key)
    if not isinstance(value, str):
        raise EnvelopeError(f"{SITE_DIR}/{FACTS_NAME} carries no {key}")
    return value


def _manifest(root: Path) -> tuple[CorpusManifest, bytes]:
    raw = _read(root, f"{CORPUS_DIR}/{MANIFEST_NAME}")
    try:
        return CorpusManifest.model_validate_json(raw), raw
    except ValidationError as error:
        raise EnvelopeError(f"{CORPUS_DIR}/{MANIFEST_NAME} is not a release manifest") from error


def _require_trees(root: Path) -> None:
    if not (root / CORPUS_DIR).is_dir() or not (root / SITE_DIR).is_dir():
        raise IncompleteEnvelopeError(f"{root.name}: missing {', '.join(missing_parts(root)[:2])}")
    for name in (FACTS_NAME, SITEMAP_NAME, CAPABILITIES_NAME):
        if not (root / SITE_DIR / name).is_file():
            raise EnvelopeError(f"{SITE_DIR}/{name} is missing")


def _document(root: Path, facts: dict[str, object]) -> CapabilityDocument:
    path = root / SITE_DIR / CAPABILITIES_NAME
    try:
        document = load_capabilities(path)
    except SiteBuildError as error:
        raise EnvelopeError(f"{SITE_DIR}/{CAPABILITIES_NAME}: {error}") from error
    recorded = _fact(facts, "capability_sha256")
    actual = _sha256(_read(root, f"{SITE_DIR}/{CAPABILITIES_NAME}"))
    if recorded != actual:
        raise EnvelopeError(
            f"{SITE_DIR}/{FACTS_NAME} records capability_sha256 {recorded}, "
            f"{CAPABILITIES_NAME} hashes to {actual}"
        )
    return document


def _release_key(facts: dict[str, object], document: CapabilityDocument) -> ReleaseKey:
    try:
        key = ReleaseKey.model_validate(facts.get("release_key"))
    except ValidationError as error:
        raise EnvelopeError(f"{SITE_DIR}/{FACTS_NAME} carries no release_key") from error
    if key.state_sha256 != state_sha256(document.state):
        raise EnvelopeError("release_key.state_sha256 is not the capability document's state")
    if key.lovspor_commit != document.state.checkout.lovspor_commit:
        raise EnvelopeError("release_key.lovspor_commit is not the capability document's checkout")
    return key


def check_structure(root: Path) -> Structure:
    """Step 2: trees, root files, the document and the cross-tree assertions; no id."""
    _require_trees(root)
    facts = _facts(root)
    manifest, manifest_bytes = _manifest(root)
    if _fact(facts, "corpus_commit") != manifest.corpus_commit:
        raise EnvelopeError(
            f"{SITE_DIR}/{FACTS_NAME} describes corpus {_fact(facts, 'corpus_commit')[:12]}, "
            f"{CORPUS_DIR}/ is {manifest.corpus_commit[:12]}"
        )
    if _fact(facts, "site_manifest_sha256") != _sha256(manifest_bytes):
        raise EnvelopeError(f"{SITE_DIR}/{FACTS_NAME} records another {MANIFEST_NAME} hash")
    document = _document(root, facts)
    key = _release_key(facts, document)
    if key.corpus_commit != manifest.corpus_commit:
        raise EnvelopeError("release_key.corpus_commit is not the corpus tree's commit")
    return Structure(facts, manifest, document, key)


def _check_site_tree(site: Path) -> int:
    """The route closure, re-scanned from the bytes on disk."""
    pages: dict[str, str] = {}
    for page in emitted_pages():
        path = site / page.path.lstrip("/") / "index.html"
        if not path.is_file():
            raise EnvelopeError(f"{SITE_DIR}/: page {page.path} is not in the tree")
        pages[page.path] = path.read_text(encoding="utf-8")
    try:
        for name, markup in pages.items():
            scan_page(name, markup)
        check_links(pages)
    except SiteBuildError as error:
        raise EnvelopeError(f"{SITE_DIR}/: {error}") from error
    if (site / SITEMAP_NAME).read_bytes() != sitemap_site_xml(emitted_pages()):
        raise EnvelopeError(f"{SITE_DIR}/{SITEMAP_NAME} does not list the emitted page set")
    return len(pages)


def _check_record(record: ReleaseRecord, structure: Structure) -> None:
    facts, manifest, document, key = structure
    if record.release_key != key:
        raise EnvelopeError("release.json release_key differs from site-facts.json")
    if record.capability_sha256 != _fact(facts, "capability_sha256"):
        raise EnvelopeError("release.json capability_sha256 differs from site-facts.json")
    if record.site_manifest_sha256 != _fact(facts, "site_manifest_sha256"):
        raise EnvelopeError("release.json site_manifest_sha256 differs from site-facts.json")
    if record.corpus.corpus_commit != manifest.corpus_commit:
        raise EnvelopeError("release.json corpus summary names another corpus commit")
    if record.observed_at != document.observation.process.observed_at:
        raise EnvelopeError("release.json observed_at is not the capability document's")


def _check_ids(root: Path, record: ReleaseRecord, facts: dict[str, object]) -> str:
    """Recomputed id == site-facts.json == release.json == release.caddy; paths and name."""
    recomputed = release_content_id(root / CORPUS_DIR, root / SITE_DIR)
    fragment = read_fragment(root)
    recorded = {
        f"{SITE_DIR}/{FACTS_NAME}": facts.get("release_content_id"),
        "release.json": record.release_content_id,
        "release.caddy": fragment_release_id(fragment),
    }
    for name, value in recorded.items():
        if value != recomputed:
            raise EnvelopeError(
                f"{name} records release_content_id {value!r}, the trees hash to {recomputed}"
            )
    expected_root = (root.resolve().parent / recomputed).as_posix()
    for path in fragment_paths(fragment):
        if not path.startswith(expected_root + "/"):
            raise EnvelopeError(f"release.caddy names {path}, outside {expected_root}/")
    if is_release_id(root.name) and root.name != recomputed:
        raise EnvelopeError(f"directory {root.name} holds release {recomputed}")
    return recomputed


def check_envelope(root: Path) -> EnvelopeReport:
    """Step 5, the final check; every defect is one :class:`EnvelopeError`."""
    structure = check_structure(root)
    if not is_complete(root):
        raise IncompleteEnvelopeError(f"{root.name}: missing {', '.join(missing_parts(root))}")
    try:
        corpus = check_release(root / CORPUS_DIR)
    except PublishError as error:
        raise EnvelopeError(f"{CORPUS_DIR}/: {error}") from error
    site_pages = _check_site_tree(root / SITE_DIR)
    record = read_record(root)
    _check_record(record, structure)
    content_id = _check_ids(root, record, structure.facts)
    return EnvelopeReport(release_content_id=content_id, corpus=corpus, site_pages=site_pages)
