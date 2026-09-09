"""The site generator: four inputs in, one tree out (ADR-0014 Decisions 1, 3, 4).

``build_site`` is a deterministic function of exactly four inputs
(ADR:645-668): the ``lovspor`` revision the build runs from, the corpus
release it is built beside (its ``site-manifest.json``), the capability
document captured from the running server, and the publication
toolchain named by ``toolchain_fingerprint``. It calls no clock.

**Publication is source-checkout tooling** (ADR:463-490). The builder
runs only inside a clean git work tree of ``lovspor`` — that tree is
where ``lovspor_commit`` comes from, and a ledger naming a commit the
output was not built from is the fabricated provenance the ledger
exists to exclude (ADR:1109-1117). So:

* ``discover_checkout`` finds the work tree from the imported package
  (``git rev-parse --show-toplevel``) and refuses a wheel installed
  inside some other repository: the top's ``src/lovspor`` must be the
  package that was imported.
* ``require_clean_work_tree`` refuses a directory that is not a work
  tree and a tree with any change, staged or not, and returns the
  commit at ``HEAD``.
* The library API takes the checkout explicitly; the CLI resolves it
  itself and offers no option to name another (plan F.2).

The build then asserts the capability document was captured for this
checkout — ``state.checkout.lovspor_commit`` is ``HEAD`` and
``expected_tool_surface_sha256`` is the descriptor recomputed against
the corpus (plan F.3) — renders every route in both languages through
the fact mechanism, scans every page (``lovspor.site.scan``), and
writes the tree: pages, ``sitemap-site.xml``, the capability document
byte for byte, and ``site-facts.json`` with ``release_content_id``
left ``null`` for the release script to fill (``fingerprint.py``).

Subprocesses are fixed argv, never a shell.
"""

import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import lovspor
from lovspor.publish.companion import companion_json_bytes
from lovspor.site.capabilities import (
    CapabilityDocument,
    HostedState,
    derive_comparisons,
    derive_hosted_state,
    load_capabilities,
)
from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import FactLedger
from lovspor.site.fingerprint import ReleaseKey, Toolchain, release_key, toolchain_fingerprint
from lovspor.site.routes import emitted_pages
from lovspor.site.scan import check_links, scan_page
from lovspor.site.sitemap import sitemap_site_xml
from lovspor.site.sources import BuildArtifacts, CorpusManifest, fact_registry
from lovspor.site.templates import page_globals, site_environment
from lovspor.tool_surface import describe_tool_surface

FACTS_SCHEMA_VERSION = "1"
CAPABILITIES_NAME = "deployment-capabilities.json"
FACTS_NAME = "site-facts.json"
SITEMAP_NAME = "sitemap-site.xml"

_PACKAGE_DIR = Path(lovspor.__file__).resolve().parent


class SiteInputs(BaseModel):
    """The four inputs of one build, plus where to write it."""

    model_config = ConfigDict(frozen=True)

    checkout: Path
    corpus: Path
    corpus_manifest: Path
    capabilities: Path
    out: Path


class SiteBuildReport(BaseModel):
    """What one build produced, for the operator and the release script."""

    model_config = ConfigDict(frozen=True)

    lovspor_commit: str
    corpus_commit: str
    pages: tuple[str, ...]
    release_key: ReleaseKey
    hosted_state: HostedState


def _git(root: Path, *args: str) -> str | None:
    """``git -C root args``; ``None`` when git reports failure."""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "-C", str(root), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise SiteBuildError(f"git is not available: {error}") from error
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def discover_checkout() -> Path:
    """The work tree the imported package lives in — refused for a foreign one."""
    top = _git(_PACKAGE_DIR, "rev-parse", "--show-toplevel")
    if top is None:
        raise SiteBuildError("not a git work tree: the imported lovspor package has no HEAD")
    checkout = Path(top).resolve()
    if (checkout / "src" / "lovspor").resolve() != _PACKAGE_DIR:
        raise SiteBuildError(
            f"the imported lovspor package is not {checkout}/src/lovspor; refusing to attest it"
        )
    return checkout


def require_clean_work_tree(root: Path) -> str:
    """The commit at ``HEAD`` of a clean work tree at ``root``; anything else is refused."""
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise SiteBuildError(f"not a git work tree: {root}")
    status = _git(root, "status", "--porcelain")
    if status is None:
        raise SiteBuildError(f"not a git work tree: {root}")
    if status:
        first = status.splitlines()[0]
        raise SiteBuildError(f"dirty work tree at {root}: {first}")
    head = _git(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head is None:
        raise SiteBuildError(f"no commit at HEAD in {root}")
    return head


def _read_manifest(path: Path) -> tuple[CorpusManifest, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SiteBuildError(f"cannot read site-manifest.json: {error}") from error
    try:
        return CorpusManifest.model_validate_json(raw), raw
    except ValueError as error:
        raise SiteBuildError(
            f"site-manifest.json is not a corpus release manifest: {error}"
        ) from error


def _document_for_this_checkout(
    document: CapabilityDocument, lovspor_commit: str, inputs: SiteInputs
) -> BuildArtifacts:
    checkout = document.state.checkout
    if checkout.lovspor_commit != lovspor_commit:
        raise SiteBuildError(
            f"capability document names a foreign commit {checkout.lovspor_commit[:12]}, "
            f"the work tree is at {lovspor_commit[:12]}"
        )
    descriptor = describe_tool_surface(inputs.corpus)
    if descriptor.schema_sha256 != checkout.expected_tool_surface_sha256:
        raise SiteBuildError(
            "capability document expects another tool surface than the checkout describes"
        )
    manifest, manifest_bytes = _read_manifest(inputs.corpus_manifest)
    return BuildArtifacts(
        lovspor_commit=lovspor_commit,
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        document=document,
        capability_bytes=inputs.capabilities.read_bytes(),
        descriptor=descriptor,
    )


def _render_pages(artifacts: BuildArtifacts) -> tuple[dict[str, str], FactLedger]:
    """Every page through the fact mechanism, scanned before anything is written."""
    environment = site_environment()
    registry = fact_registry(artifacts)
    ledger = FactLedger()
    pages: dict[str, str] = {}
    for page in emitted_pages():
        context = page.head_context() | page_globals(page.path, page.lang, registry, ledger)
        markup = environment.get_template(page.template).render(context)
        scan_page(page.path, markup)
        pages[page.path] = markup
    check_links(pages)
    return pages, ledger


def _capability_block(document: CapabilityDocument) -> dict[str, object]:
    """The identities, comparisons and state — recomputed, and asserted equal to the document's."""
    observation, state = document.observation, document.state
    comparisons = derive_comparisons(observation, state.checkout)
    hosted_state = derive_hosted_state(observation, comparisons)
    if comparisons != state.comparisons or hosted_state != state.hosted_state:
        raise SiteBuildError("capability document state is not its own derivation")
    process, transport = observation.process, observation.transport
    identity = process.runtime_identity
    return {
        "expected_runtime_identity": state.checkout.expected_runtime_identity.model_dump(),
        "observed_runtime_identity": None if identity is None else identity.model_dump(),
        "comparisons": comparisons.model_dump(),
        "hosted_state": hosted_state,
        "process": {
            "status": process.status,
            "reason": process.reason,
            "observed_at": process.observed_at,
        },
        "transport": {
            "status": transport.status,
            "reason": transport.reason,
            "observed_at": transport.observed_at,
            "authenticated": {
                "status": transport.authenticated.status,
                "reason": transport.authenticated.reason,
            },
        },
    }


def _site_facts(
    artifacts: BuildArtifacts, toolchain: Toolchain, key: ReleaseKey, ledger: FactLedger
) -> dict[str, object]:
    manifest = artifacts.manifest
    return {
        "schema_version": FACTS_SCHEMA_VERSION,
        "lovspor_commit": artifacts.lovspor_commit,
        "engine_version": manifest.engine_version,
        "corpus_commit": manifest.corpus_commit,
        "corpus_commit_time": manifest.corpus_commit_time,
        "site_manifest_sha256": artifacts.manifest_sha256,
        "toolchain": toolchain.model_dump(),
        "release_key": key.model_dump(),
        "release_content_id": None,
        "capability_sha256": artifacts.capability_sha256,
        "capability": _capability_block(artifacts.document),
        "artifacts": [
            {"id": name, "sha256": digest} for name, digest in artifacts.artifact_hashes()
        ],
        "facts": [entry.model_dump() for entry in ledger.entries],
    }


def _refuse_non_empty(out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise SiteBuildError(f"output directory is not empty: {out}")


def _write_tree(inputs: SiteInputs, pages: dict[str, str], facts: dict[str, object]) -> None:
    out = inputs.out
    out.mkdir(parents=True, exist_ok=True)
    for path, markup in pages.items():
        target = out / path.lstrip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(markup.encode("utf-8"))
    (out / SITEMAP_NAME).write_bytes(sitemap_site_xml(emitted_pages()))
    shutil.copyfile(inputs.capabilities, out / CAPABILITIES_NAME)
    (out / FACTS_NAME).write_bytes(companion_json_bytes(facts))


def build_site(inputs: SiteInputs) -> SiteBuildReport:
    """Build the site tree into ``inputs.out``; refuse what the ledger could not stand behind."""
    lovspor_commit = require_clean_work_tree(inputs.checkout)
    _refuse_non_empty(inputs.out)
    document = load_capabilities(inputs.capabilities)
    artifacts = _document_for_this_checkout(document, lovspor_commit, inputs)
    toolchain = toolchain_fingerprint(inputs.checkout)
    key = release_key(artifacts.manifest.corpus_commit, lovspor_commit, document.state, toolchain)
    pages, ledger = _render_pages(artifacts)
    _write_tree(inputs, pages, _site_facts(artifacts, toolchain, key, ledger))
    return SiteBuildReport(
        lovspor_commit=lovspor_commit,
        corpus_commit=artifacts.manifest.corpus_commit,
        pages=tuple(pages),
        release_key=key,
        hosted_state=document.state.hosted_state,
    )
