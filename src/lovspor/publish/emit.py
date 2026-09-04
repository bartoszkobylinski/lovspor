"""One pinned corpus commit in, one site tree out (ADR-0013 Decisions 3, 8).

The emitter reads everything through :class:`CorpusSnapshot` at an
explicit commit — never the working tree — and writes a complete site
directory: document and provision pages, their JSON twins, and
``site-manifest.json`` carrying the snapshot-closure proof plus the
machine-readable exclusion list for duplicate-pid documents. No
wall-clock value is written anywhere; the timestamp role is played by
the pinned commit's committer time.
"""

import hashlib
import subprocess
from collections.abc import Callable
from functools import partial
from pathlib import Path

import lovspor
from lovspor.publish.companion import (
    SCHEMA_VERSION,
    companion_json_bytes,
    document_companion,
    provision_companion,
)
from lovspor.publish.html import LinkResolver
from lovspor.publish.inventory import (
    DocumentPlan,
    PublishInventory,
    body_lines,
    build_inventory,
    normalise_pid,
)
from lovspor.publish.pages import (
    PageProvenance,
    document_page_html,
    document_url,
    provision_page_html,
    provision_url,
    section_slices,
)
from lovspor.snapshot import CorpusSnapshot


def emit_site(corpus: Path, sha: str, out: Path) -> None:
    """Build the whole site for the corpus state at ``sha`` into ``out``."""
    snapshot = CorpusSnapshot(corpus, sha)
    inventory = build_inventory(snapshot.manifest, snapshot.read_text)
    revisions = _source_revisions(corpus, sha)
    resolve = _resolver(inventory)
    for plan in inventory.documents:
        _emit_document(plan, (snapshot, revisions), out, resolve)
    _write(out / "site-manifest.json", _site_manifest_bytes(corpus, sha, inventory))


def _emit_document(
    plan: DocumentPlan,
    context: tuple[CorpusSnapshot, dict[str, str]],
    out: Path,
    resolve: LinkResolver,
) -> None:
    snapshot, revisions = context
    body = snapshot.read_text(plan.markdown_path) or ""
    lines = body_lines(body)
    provenance = _provenance_of(plan, revisions)
    html = document_page_html(plan, lines, provenance, resolve)
    _write_page(
        out / plan.route / plan.slug,
        html,
        partial(document_companion, plan, "\n".join(lines), provenance),
    )
    if not plan.duplicate_pids:
        _emit_provisions(plan, (lines, provenance), out, resolve)


def _emit_provisions(
    plan: DocumentPlan,
    context: tuple[list[str], PageProvenance],
    out: Path,
    resolve: LinkResolver,
) -> None:
    lines, provenance = context
    slices = section_slices(lines)
    for provision in plan.provisions:
        section = slices[provision.pid]
        html = provision_page_html(plan, provision, provenance, section, resolve)
        _write_page(
            out / plan.route / plan.slug / "paragraf" / provision.pid,
            html,
            partial(
                provision_companion,
                (plan, provision),
                "\n".join(section),
                provenance,
            ),
        )


def _write_page(
    directory: Path,
    html: str,
    companion: Callable[[str], dict[str, object]],
) -> None:
    """Write ``index.html`` and its twin; the twin gets the HTML's digest."""
    html_bytes = html.encode("utf-8")
    digest = hashlib.sha256(html_bytes).hexdigest()
    _write(directory / "index.html", html_bytes)
    _write(directory / "index.json", companion_json_bytes(companion(digest)))


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _provenance_of(plan: DocumentPlan, revisions: dict[str, str]) -> PageProvenance:
    return PageProvenance(
        source_revision=revisions[plan.markdown_path],
        xml_hash=plan.xml_hash,
        renderer_version=plan.renderer_version,
    )


def _resolver(inventory: PublishInventory) -> LinkResolver:
    """Map body link targets to emitted canonical paths.

    ``lov/<id>`` and ``forskrift/<id>`` refs resolve by the target's
    ``ref_id``; a ``/§N`` deep part resolves to the provision page when
    the target emits one, else to the target's document page. Everything
    unresolvable returns ``None`` and renders as text.
    """
    by_ref = {plan.ref_id: plan for plan in inventory.documents}

    def resolve(target: str) -> str | None:
        parts = target.split("/")
        plan = by_ref.get("/".join(parts[:2]))
        if plan is None:
            return None
        rest = parts[2:]
        if not rest:
            return document_url(plan)
        return _deep_url(plan, rest[0])

    return resolve


def _deep_url(plan: DocumentPlan, deep: str) -> str:
    """A ``§N`` deep part → provision page when emitted, else the document."""
    if deep.startswith("§") and not plan.duplicate_pids:
        pid = normalise_pid(deep[1:])
        if any(provision.pid == pid for provision in plan.provisions):
            return provision_url(plan, pid)
    return document_url(plan)


def _source_revisions(corpus: Path, sha: str) -> dict[str, str]:
    """``path -> last commit touching it`` in one newest-first log walk."""
    # core.quotePath=false: without it git octal-escapes non-ASCII paths
    # (lover/forbud-paa-vimpel-føring.md) and the manifest lookup misses.
    result = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "git",
            "-c",
            "core.quotePath=false",
            "log",
            "--format=%x00%H",
            "--name-only",
            sha,
        ],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=True,
    )
    revisions: dict[str, str] = {}
    current = ""
    for line in result.stdout.splitlines():
        if line.startswith("\x00"):
            current = line[1:]
        elif line:
            revisions.setdefault(line, current)
    return revisions


def _site_manifest_bytes(corpus: Path, sha: str, inventory: PublishInventory) -> bytes:
    manifest = {
        "corpus_commit": sha,
        "corpus_commit_time": _committer_time(corpus, sha),
        "engine_version": lovspor.__version__,
        "site_schema_version": SCHEMA_VERSION,
        "documents": len(inventory.documents),
        "exclusions": [
            {
                "doc_id": plan.doc_id,
                "slug": plan.slug,
                "duplicate_pids": plan.duplicate_pids,
            }
            for plan in inventory.documents
            if plan.duplicate_pids
        ],
    }
    return companion_json_bytes(manifest)


def _committer_time(corpus: Path, sha: str) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", "show", "-s", "--format=%cI", sha],  # noqa: S607
        cwd=corpus,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
