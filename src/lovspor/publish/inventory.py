"""The publish inventory: what one corpus snapshot allows the site to emit.

ADR-0013 Decision 1: the inventory comes from the manifest's current
records — never a directory glob — and identity is fail-closed. A
duplicate slug, an unknown route, a slugless or bodyless current record
each block the build outright. Intra-document duplicate provision ids are
*measured* instead: the document still publishes its document page, but
the generator withholds its provision pages, so the count lives here for
`site-manifest.json` to record.
"""

from collections import Counter
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError
from lovspor.headings import parse_section_heading
from lovspor.storage.manifest import Manifest, ManifestRecord

Route = Literal["lov", "forskrift"]

_ROUTES: dict[str, Route] = {"lov": "lov", "forskrift": "forskrift"}


class PublishError(LovsporError):
    """The snapshot cannot be published as-is; nothing may be emitted."""


class ProvisionRef(BaseModel):
    """One provision heading, in document order."""

    model_config = ConfigDict(frozen=True)

    pid: str
    heading_id: str
    title: str | None


class DocumentPlan(BaseModel):
    """One current document and the provision surface it may publish."""

    model_config = ConfigDict(frozen=True)

    doc_id: str
    slug: str
    route: Route
    title: str | None
    markdown_path: str
    provisions: tuple[ProvisionRef, ...]
    duplicate_pids: dict[str, int]


class PublishInventory(BaseModel):
    """Every page the snapshot allows, in manifest order."""

    model_config = ConfigDict(frozen=True)

    documents: tuple[DocumentPlan, ...]


def normalise_pid(heading_id: str) -> str:
    """ASCII-safe path form of a section id: lowercased, spaces removed.

    Collisions introduced by this mapping (``35 a`` vs ``35a``) are real
    URL collisions and are counted as duplicates by the inventory.
    """
    return heading_id.replace(" ", "").lower()


def build_inventory(
    manifest: Manifest,
    read_text: Callable[[str], str | None],
) -> PublishInventory:
    """Plan the publishable page set for one snapshot, failing closed.

    ``read_text`` resolves a manifest ``markdown_path`` to the body at the
    pinned snapshot (``CorpusSnapshot.read_text`` in production).
    """
    plans: list[DocumentPlan] = []
    seen: set[tuple[str, str]] = set()
    for doc_id, record in manifest.documents.items():
        if record.status != "current":
            continue
        plan = _plan_document(doc_id, record, read_text)
        # Identity is (route, slug): the URL grammar puts the document type
        # in the path prefix, so the corpus's one cross-type duplicate slug
        # (the 1925 Svalbard bergverksordning, lov + forskrift) collides
        # nowhere and must not block publication.
        if (plan.route, plan.slug) in seen:
            raise PublishError(
                f"duplicate slug '{plan.slug}' among current {plan.route} "
                f"records: a URL is an irreversible public contract, "
                f"first-wins would make the shadowed document unreachable "
                f"({doc_id})",
            )
        seen.add((plan.route, plan.slug))
        plans.append(plan)
    return PublishInventory(documents=tuple(plans))


def _plan_document(
    doc_id: str,
    record: ManifestRecord,
    read_text: Callable[[str], str | None],
) -> DocumentPlan:
    """Plan one current record, refusing every unpublishable shape."""
    route = _ROUTES.get(record.doc_type)
    if route is None:
        raise PublishError(
            f"current record {doc_id} has doc_type {record.doc_type!r}: "
            f"no publication route exists for it (ADR-0013 Decision 1)",
        )
    if record.slug is None:
        raise PublishError(f"current record {doc_id} has no slug")
    body = read_text(record.markdown_path)
    if body is None:
        raise PublishError(
            f"current record {doc_id} names {record.markdown_path}, "
            f"which cannot be read from the snapshot",
        )
    provisions = _provisions_of(body)
    counts = Counter(provision.pid for provision in provisions)
    return DocumentPlan(
        doc_id=doc_id,
        slug=record.slug,
        route=route,
        title=record.title,
        markdown_path=record.markdown_path,
        provisions=provisions,
        duplicate_pids={pid: n for pid, n in counts.items() if n > 1},
    )


def _provisions_of(body: str) -> tuple[ProvisionRef, ...]:
    """Section headings of a rendered body, in document order."""
    refs: list[ProvisionRef] = []
    for line in _body_lines(body):
        parsed = parse_section_heading(line)
        if parsed is not None:
            heading_id, title = parsed
            refs.append(
                ProvisionRef(
                    pid=normalise_pid(heading_id),
                    heading_id=heading_id,
                    title=title,
                ),
            )
    return tuple(refs)


def _body_lines(body: str) -> list[str]:
    """Lines of the body with the YAML front matter block removed."""
    lines = body.split("\n")
    if lines and lines[0] == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                return lines[index + 1 :]
    return lines
