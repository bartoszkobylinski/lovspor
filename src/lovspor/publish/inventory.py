"""The publish inventory: what one corpus snapshot allows the site to emit.

ADR-0013 Decision 1: the inventory comes from the manifest's current
records — never a directory glob — and identity is fail-closed. A
duplicate slug, an unknown route, a slugless or bodyless current record
each block the build outright. Intra-document duplicate provision ids are
*measured* instead: the document still publishes its document page, but
the generator withholds its provision pages, so the count lives here for
`site-manifest.json` to record.
"""

import re
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict

from lovspor.errors import LovsporError
from lovspor.headings import parse_section_heading
from lovspor.storage.manifest import Manifest, ManifestRecord

Route = Literal["lov", "forskrift"]

_ROUTES: dict[str, Route] = {"lov": "lov", "forskrift": "forskrift"}

_LANGUAGES = frozenset({"nb", "nn", "no"})
"""Every language value the corpus carries today (measured 2026-09-04:
nb 1,504 / nn 134 / no 4,240, none missing). A value outside this set is
a schema change to notice, not a default to guess (ADR-0013 Decision 4)."""

_FRONT_MATTER_FIELD = re.compile(r'^([a-z_]+):\s*(?:"([^"]*)"|null)\s*$')

# fullmatch, never match-with-$: `$` matches before a trailing newline,
# so "abortloven\n" would pass an anchored match.
_CANONICAL_SLUG = re.compile(r"[a-z0-9æøåü]+(?:-[a-z0-9æøåü]+)*")

_CANONICAL_REF_ID = re.compile(r"(?:lov|forskrift)/\d{4}-\d{2}-\d{2}(?:-[\dx]+)?")
"""Both attested ref_id shapes: type/date-number, and type/date alone for
the eight pre-1850 acts (norske lov 1687 through vekselloven 1845)."""
"""The slug grammar the corpus actually uses, measured 2026-09-05 over
every current record: lowercase Latin plus the attested æ ø å ü, digit
groups, single hyphens between groups. Anything else — uppercase,
whitespace, underscores, query characters, slashes, leading/trailing
hyphens — is not a canonical URL segment and fails the build."""

_PROVENANCE_KEYS = frozenset(
    {"language", "ref_id", "retrieved_at", "date_in_force", "last_change_in_force"},
)
"""Front-matter keys this layer consumes; a conflicting repeat of any of
them is malformed provenance, never a first-wins choice."""


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
    language: str
    ref_id: str
    retrieved_at: str
    date_in_force: str | None
    last_change_in_force: str | None
    provisions: tuple[ProvisionRef, ...]
    duplicate_pids: dict[str, int]


class PublishInventory(BaseModel):
    """Every page the snapshot allows, in manifest order."""

    model_config = ConfigDict(frozen=True)

    documents: tuple[DocumentPlan, ...]


def normalise_pid(heading_id: str) -> str:
    """ASCII-safe path form of a section id: lowercased, spaces removed.

    Collisions introduced by this mapping (``35 a`` vs ``35a``) are real
    URL collisions and are counted as duplicates by the inventory. All
    whitespace is removed, not just spaces — a tab in a heading id must
    not survive into a URL path segment.
    """
    return "".join(heading_id.split()).lower()


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
    if record.slug is None or not record.slug.strip():
        raise PublishError(f"current record {doc_id} has no slug")
    if not _CANONICAL_SLUG.fullmatch(record.slug):
        raise PublishError(
            f"current record {doc_id} slug {record.slug!r} is not a "
            f"canonical URL segment (lowercase [a-z0-9æøåü] groups joined "
            f"by single hyphens)",
        )
    body = read_text(record.markdown_path)
    if body is None:
        raise PublishError(
            f"current record {doc_id} names {record.markdown_path}, "
            f"which cannot be read from the snapshot",
        )
    provisions = _provisions_of(body)
    counts = Counter(provision.pid for provision in provisions)
    fields = _front_matter_fields(doc_id, body)
    if not fields["ref_id"].startswith(f"{route}/"):
        raise PublishError(
            f"document {doc_id} ref_id {fields['ref_id']!r} names a "
            f"different type than its route '{route}': the manifest and "
            f"the front matter disagree about what this document is",
        )
    return DocumentPlan(
        doc_id=doc_id,
        slug=record.slug,
        route=route,
        title=record.title,
        markdown_path=record.markdown_path,
        language=fields["language"],
        ref_id=fields["ref_id"],
        retrieved_at=fields["retrieved_at"],
        date_in_force=fields.get("date_in_force"),
        last_change_in_force=fields.get("last_change_in_force"),
        provisions=provisions,
        duplicate_pids={pid: n for pid, n in counts.items() if n > 1},
    )


def _front_matter_fields(doc_id: str, body: str) -> dict[str, str]:
    """Provenance fields from the front matter, failing closed.

    The renderer writes front matter deterministically as ``key: "value"``
    (or ``key: null``) lines, so a closed line parser suffices — pyyaml is
    deliberately not a runtime dependency of the engine.
    """
    fields: dict[str, str] = {}
    seen: dict[str, str | None] = {}
    for line in _front_matter_lines(body):
        match = _FRONT_MATTER_FIELD.match(line)
        if match is None:
            # A malformed line naming a provenance key must not vanish:
            # a silently skipped line is how a field disappears without a
            # trace (the mcp.py continue-truncation class).
            key = line.split(":", 1)[0].strip()
            if key in _PROVENANCE_KEYS:
                raise PublishError(
                    f"document {doc_id} front matter line for {key} is malformed: {line!r}",
                )
            continue
        key, value = match.group(1), match.group(2)
        if key in _PROVENANCE_KEYS:
            # null counts as a recorded value: null beside a string (either
            # order) is the same conflict as two different strings.
            if key in seen and seen[key] != value:
                raise PublishError(
                    f"document {doc_id} front matter repeats {key} with "
                    f"conflicting values; silently picking one would "
                    f"publish wrong provenance",
                )
            seen[key] = value
        if value is not None:
            fields.setdefault(key, value)
    for required in ("language", "ref_id", "retrieved_at"):
        if not fields.get(required, "").strip():
            raise PublishError(
                f"document {doc_id} front matter carries no {required}; "
                f"publication cannot guess provenance (ADR-0013 Decision 4)",
            )
    _check_provenance_shapes(doc_id, fields)
    return fields


def _check_provenance_shapes(doc_id: str, fields: dict[str, str]) -> None:
    """Grammar checks on the required values, all measured on the corpus."""
    if fields["language"] not in _LANGUAGES:
        raise PublishError(
            f"document {doc_id} carries language "
            f"{fields['language']!r}, outside the corpus set "
            f"{sorted(_LANGUAGES)}; refusing to publish a wrong lang",
        )
    if not _CANONICAL_REF_ID.fullmatch(fields["ref_id"]):
        raise PublishError(
            f"document {doc_id} ref_id {fields['ref_id']!r} matches neither "
            f"attested shape (type/date or type/date-number)",
        )
    for key in ("date_in_force", "last_change_in_force"):
        if key in fields:
            try:
                date.fromisoformat(fields[key])
            except ValueError as error:
                raise PublishError(
                    f"document {doc_id} {key} {fields[key]!r} is not an ISO-8601 date",
                ) from error
    try:
        instant = datetime.fromisoformat(fields["retrieved_at"])
        if "T" not in fields["retrieved_at"]:
            raise ValueError("date without time")
        if instant.utcoffset() != timedelta(0):
            raise ValueError("not an explicit UTC instant")
    except ValueError as error:
        raise PublishError(
            f"document {doc_id} retrieved_at {fields['retrieved_at']!r} "
            f"is not an explicit UTC ISO-8601 timestamp — the shape the "
            f"engine writes; a naive or offset time is not the corpus's "
            f"retrieval instant",
        ) from error


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


def _front_matter_lines(body: str) -> list[str]:
    """Lines of the front matter block, empty when there is none."""
    lines = body.split("\n")
    if lines and lines[0] == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line == "---":
                return lines[1:index]
    return []
