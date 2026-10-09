"""Moving a promoted local regulation to the running extractor (ADR-0016 4e, issue #588).

An extractor bump is "a ``migration:`` commit across the dataset, never new
versions". For one promoted document that means: re-extract the source its
published rendering names (``source_sha256`` at ``source_url``) with the
running extractor and, when the text is byte-identical — same identity, same
``content_hash``, same rendering — move only the version metadata:

* ``manifest.json``: the record's ``extractor_version``;
* ``observations/<slug>.json``: ``promotion.extractor_version`` of every
  promoted version;
* ``evidence/<slug>.json``: written when the document has none (promoted
  before the sidecar existed, #577); an existing sidecar is left as it is.

The Markdown is never written. A text the running extractor reads otherwise
is a new version and is refused, pointing to ``backfill``/``local``; so is a
rendering that would change though the text does not, because a migration
never rewrites a document.

Earlier versions move with the current one, because the observation refresh
and the backfill read every version against the running extractor
(:mod:`.intervals`). Each must be reproduced by the log at the running
extractor — the same text, first observed at the same instant, at the same
URL — exactly as a refresh demands. Every version needs the owner's standing
approval of its text: given at the running extractor
(:func:`~.plan.require_approval`), or — when that version's published
rendering is byte-identical under the running extractor — given at an earlier
one and carried (:mod:`.carry`, owner decision 2026-10-08). The current
version's bytes are the checkout's Markdown; an earlier version's are those
its commit published. Where the bytes cannot be shown identical, a
re-approval is the owner's act, recorded with ``lovspor promote approve``.

A document whose record, current version and sidecar already say the running
extractor is current and is not read again, so a rerun after the commit
writes nothing. Nothing here commits or records; the command does.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, ValidationError

from lovspor.errors import PromotionRefusedError
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion.archive import Fetch, authority_fetches, read_artifact
from lovspor.promotion.carry import (
    VersionApproval,
    audit_at_running_extractor,
    historical_markdown,
    standing_approval,
)
from lovspor.promotion.corpus import LOCAL_DIR, MANIFEST_NAME, CorpusCheckout, LocalRecord
from lovspor.promotion.corpus import manifest_text as render_manifest
from lovspor.promotion.decisions import ArtifactKey, DecisionLog, HumanDecision
from lovspor.promotion.evidence import evidence_path
from lovspor.promotion.extract import EXTRACTOR_VERSION, extract_regulation
from lovspor.promotion.identity import mint_identity
from lovspor.promotion.intervals import reproduction
from lovspor.promotion.models import (
    Authority,
    ExtractedDocument,
    HeldExtraction,
    HeldIdentity,
    LocalDocument,
    ObservedSource,
)
from lovspor.promotion.plan import Prepared
from lovspor.promotion.render import render_local_regulation
from lovspor.promotion.versions import DerivedVersion, read_primary
from lovspor.promotion.withdraw import refuse_withdrawn
from lovspor.promotion.writer import (
    ObservationsFile,
    evidence_text,
    observations_text,
    refuse_nlod,
)

MIGRATION_SUBJECT = "migration(lokal-forskrift): {authority_id}/{slug} extractor v{old}→v{new}"
MIGRATION_BATCH_SUBJECT = "migration(lokal-forskrift): {count} documents to extractor v{new}"

_KLASS_VERSION = re.compile(r'klass_version: ("(?:[^"\\]|\\.)*")')


class Published(BaseModel):
    """One current document as the checkout holds it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    record: LocalRecord
    markdown: str
    observations: ObservationsFile
    has_evidence: bool

    @property
    def observations_path(self) -> str:
        return f"{LOCAL_DIR}/{self.record.authority_id}/observations/{self.record.slug}.json"

    def is_current(self) -> bool:
        """Every version field and the sidecar already say the running extractor."""
        audits = {v.promotion.extractor_version for v in self.observations.versions}
        versions = {self.record.extractor_version, *audits}
        return versions == {EXTRACTOR_VERSION} and self.has_evidence


class Migration(BaseModel):
    """What migrating one document writes, by corpus-relative path, and why it may."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    doc_id: str
    key: ArtifactKey
    record: LocalRecord
    from_version: int
    approval: HumanDecision
    approvals: tuple[VersionApproval, ...]
    files: dict[str, str]

    @property
    def carried(self) -> tuple[VersionApproval, ...]:
        """The versions whose approval this migration carries rather than finds given."""
        return tuple(approval for approval in self.approvals if approval.carried)

    @property
    def commit_subject(self) -> str:
        return MIGRATION_SUBJECT.format(
            authority_id=self.record.authority_id,
            slug=self.record.slug,
            old=self.from_version,
            new=EXTRACTOR_VERSION,
        )


class MigrationInputs:
    """The archive, the decision log and the checkout one migration run reads, and its instant."""

    def __init__(
        self, root: ObservatoryRoot, decisions: DecisionLog, corpus: CorpusCheckout, now: datetime
    ) -> None:
        self.root = root
        self.log = ObservationLog(root)
        self.decisions = decisions
        self.corpus = corpus
        self.now = now
        self._fetches: dict[str, tuple[Fetch, ...]] = {}

    def fetches(self, authority_id: str) -> tuple[Fetch, ...]:
        """The authority's fetches, read from the log once per run."""
        if authority_id not in self._fetches:
            self._fetches[authority_id] = authority_fetches(self.log, authority_id)
        return self._fetches[authority_id]


def published(corpus: CorpusCheckout, doc_id: str, record: LocalRecord) -> Published:
    """The document's rendering and observations; a file that does not read is refused."""
    if record.status != "current":
        msg = f"{doc_id} is withdrawn; a withdrawn document is never migrated"
        raise PromotionRefusedError(msg)
    markdown_path = corpus.inside(record.markdown_path.removeprefix(f"{LOCAL_DIR}/"))
    observations = corpus.inside(f"{record.authority_id}/observations/{record.slug}.json")
    try:
        markdown = markdown_path.read_bytes().decode()
        audit = ObservationsFile.model_validate_json(observations.read_bytes())
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        msg = f"{doc_id}: its rendering or observations do not read ({type(exc).__name__})"
        raise PromotionRefusedError(msg) from exc
    return Published(
        doc_id=doc_id,
        record=record,
        markdown=markdown,
        observations=audit,
        has_evidence=_has_evidence(corpus, record),
    )


def _has_evidence(corpus: CorpusCheckout, record: LocalRecord) -> bool:
    relative = evidence_path(record.authority_id, record.slug).removeprefix(f"{LOCAL_DIR}/")
    return corpus.inside(relative).is_file()


def plan_migration(inputs: MigrationInputs, document: Published, authority: Authority) -> Migration:
    """The migration of ``document``, or a refusal saying why it is not one."""
    key = _source_key(document)
    refuse_withdrawn(inputs.decisions, key, document.doc_id)
    prepared = _reread(inputs, document, key, authority)
    current = standing_approval(inputs.decisions.latest_decision(key), prepared)
    earlier = _require_earlier(_Subject(inputs, document, authority), prepared)
    approvals = (*earlier, current)
    files = {
        document.observations_path: _observations(document, approvals, inputs.now),
        **_evidence(inputs.corpus, document, prepared),
    }
    return Migration(
        doc_id=document.doc_id,
        key=key,
        record=document.record.model_copy(update={"extractor_version": EXTRACTOR_VERSION}),
        from_version=document.record.extractor_version,
        approval=current.decision,
        approvals=approvals,
        files=files,
    )


def published_klass_version(document: Published) -> str:
    """The KLASS vintage the rendering was published under; a migration keeps it."""
    authority = _front_matter(document).get("authority")
    found = None if authority is None else _KLASS_VERSION.search(authority)
    if found is None:
        msg = f"{document.doc_id}: its front matter names no klass_version"
        raise PromotionRefusedError(msg)
    value: str = json.loads(found.group(1))
    return value


def migration_files(corpus: CorpusCheckout, migrations: list[Migration]) -> dict[str, str]:
    """Every file the migrations write: theirs, and one manifest carrying every record."""
    manifest = corpus.local_manifest()
    documents = {**manifest.documents, **{m.doc_id: m.record for m in migrations}}
    files = {
        f"{LOCAL_DIR}/{MANIFEST_NAME}": render_manifest(
            manifest.model_copy(update={"documents": documents})
        )
    }
    for migration in migrations:
        files.update(migration.files)
    refuse_nlod(files)
    return files


def subject(migrations: list[Migration]) -> str:
    """The commit subject: the document's own, or a count when there are several."""
    if len(migrations) == 1:
        return migrations[0].commit_subject
    return MIGRATION_BATCH_SUBJECT.format(count=len(migrations), new=EXTRACTOR_VERSION)


def planned_diff(corpus: CorpusCheckout, files: dict[str, str]) -> str:
    """A unified diff of ``files`` against the checkout, for a dry run."""
    chunks: list[str] = []
    for relative, text in sorted(files.items()):
        target = corpus.inside(relative.removeprefix(f"{LOCAL_DIR}/"))
        old = target.read_bytes().decode() if target.is_file() else ""
        chunks += difflib.unified_diff(
            old.splitlines(keepends=True),
            text.splitlines(keepends=True),
            fromfile=f"a/{relative}" if old else "/dev/null",
            tofile=f"b/{relative}",
        )
    return "".join(chunks)


def _front_matter(document: Published) -> dict[str, str]:
    """The raw value of every front-matter line, by key."""
    block = document.markdown.removeprefix("---\n").partition("\n---\n")[0]
    pairs = (line.partition(": ") for line in block.split("\n"))
    return {key: value for key, separator, value in pairs if separator}


def _source_key(document: Published) -> ArtifactKey:
    """The archived artifact the published rendering names as its source."""
    front = _front_matter(document)
    try:
        return ArtifactKey(
            authority_id=document.record.authority_id,
            sha256=json.loads(front["source_sha256"]),
            source_url=json.loads(front["source_url"]),
        )
    except (KeyError, ValueError) as exc:
        msg = f"{document.doc_id}: its front matter does not name its source"
        raise PromotionRefusedError(msg) from exc


def _reread(
    inputs: MigrationInputs, document: Published, key: ArtifactKey, authority: Authority
) -> Prepared:
    """The source re-extracted by the running extractor, refused unless byte-identical."""
    extracted = _extracted(inputs, document, key)
    identity = mint_identity(extracted.regulation, authority, inputs.corpus.central_ref_ids())
    if isinstance(identity, HeldIdentity) or identity.doc_id != document.doc_id:
        msg = f"{document.doc_id}: extractor v{EXTRACTOR_VERSION} names its text otherwise"
        raise PromotionRefusedError(msg)
    _refuse_new_text(document, identity.content_hash)
    prepared = Prepared(
        identity=identity,
        extracted=extracted,
        slug=document.record.slug,
        version=document.record.version,
        unchanged=True,
        markdown=document.markdown,
    )
    _refuse_new_rendering(document, prepared)
    return prepared


def _extracted(inputs: MigrationInputs, document: Published, key: ArtifactKey) -> ExtractedDocument:
    artifact = read_artifact(inputs.log, inputs.fetches(key.authority_id), key, None)
    extracted = extract_regulation(artifact.payload, artifact.content_type)
    if isinstance(extracted, HeldExtraction):
        msg = f"{document.doc_id}: extractor v{EXTRACTOR_VERSION} holds its source "
        raise PromotionRefusedError(msg + f"({extracted.reason.value}: {extracted.detail})")
    return extracted


def _refuse_new_text(document: Published, content_hash: str) -> None:
    if content_hash == document.record.content_hash:
        return
    msg = (
        f"{document.doc_id}: extractor v{EXTRACTOR_VERSION} reads another text (content_hash "
        f"{document.record.content_hash[:12]}… → {content_hash[:12]}…). That is a new version, "
        "not a migration: approve it and run `lovspor promote backfill` (or `promote local`)"
    )
    raise PromotionRefusedError(msg)


def _published_source(document: Published) -> ObservedSource:
    """The observation facts the rendering was published with; a migration keeps them."""
    front = _front_matter(document)
    try:
        return ObservedSource(
            observed_at_first=datetime.fromisoformat(json.loads(front["observed_at_first"])),
            source_url=json.loads(front["source_url"]),
            source_sha256=json.loads(front["source_sha256"]),
        )
    except (KeyError, ValueError) as exc:
        msg = f"{document.doc_id}: its front matter does not name its observed source"
        raise PromotionRefusedError(msg) from exc


def _refuse_new_rendering(document: Published, prepared: Prepared) -> None:
    rendered = render_local_regulation(
        LocalDocument(
            identity=prepared.identity,
            extracted=prepared.extracted,
            slug=prepared.slug,
            version=prepared.version,
            source=_published_source(document),
        )
    )
    if rendered != document.markdown:
        msg = (
            f"{document.doc_id}: under extractor v{EXTRACTOR_VERSION} its rendering changes "
            "though its text does not; a migration never rewrites the Markdown"
        )
        raise PromotionRefusedError(msg)


@dataclass(frozen=True)
class _Subject:
    """One document being migrated: the run's inputs, the document and its authority."""

    inputs: MigrationInputs
    document: Published
    authority: Authority


def _require_earlier(subject: _Subject, prepared: Prepared) -> tuple[VersionApproval, ...]:
    """Every promoted version reproduced by the log; the earlier ones' approvals."""
    document, inputs = subject.document, subject.inputs
    fetches = inputs.fetches(document.record.authority_id)
    history = read_primary(inputs.log, fetches, _primary_url(document), None)
    derived = {version.version: version for version in history.versions}
    approvals: list[VersionApproval] = []
    for entry in document.observations.versions:
        version = derived.get(entry.version)
        problem = reproduction(entry, version)
        if problem is not None or version is None:
            msg = f"{document.doc_id} v{entry.version} at extractor v{EXTRACTOR_VERSION}: {problem}"
            raise PromotionRefusedError(msg + "; that is not a migration")
        if entry.version != document.record.version:
            approvals.append(_version_approval(subject, version, prepared))
    return tuple(approvals)


def _primary_url(document: Published) -> str:
    """The one URL every promoted version was read at, numbered 1..current."""
    versions = document.observations.versions
    urls = {entry.primary_url for entry in versions}
    current = document.record.version
    if len(urls) != 1 or [entry.version for entry in versions] != list(range(1, current + 1)):
        msg = f"{document.observations_path} does not list one URL's versions 1..{current}"
        raise PromotionRefusedError(msg)
    return urls.pop()


def _version_approval(
    subject: _Subject, version: DerivedVersion, prepared: Prepared
) -> VersionApproval:
    """The standing approval of an earlier version's text, carried only on identical bytes."""
    blobs = set(version.source_sha256s)
    standing = [
        record
        for record in subject.inputs.decisions.records()
        if isinstance(record, HumanDecision)
        and record.artifact.source_url == version.primary_url
        and record.artifact.sha256 in blobs
    ]
    identity = prepared.identity.model_copy(update={"content_hash": version.content_hash})
    earlier = prepared.model_copy(update={"identity": identity, "version": version.version})
    try:
        approval = standing_approval(standing[-1] if standing else None, earlier)
        if approval.carried:
            _require_identical_bytes(subject, version.version, approval.content_hash)
    except PromotionRefusedError as exc:
        msg = f"v{version.version} (approve one of {', '.join(sorted(blobs))}): {exc}"
        raise PromotionRefusedError(msg) from exc
    return approval


def _require_identical_bytes(subject: _Subject, version: int, content_hash: str) -> None:
    """An earlier version's published Markdown re-rendered byte-identically, or a refusal."""
    then = _published_then(subject, version, content_hash)
    try:
        _reread(subject.inputs, then, _source_key(then), subject.authority)
    except PromotionRefusedError as exc:
        msg = (
            f"it is not byte-identical at extractor v{EXTRACTOR_VERSION} ({exc}); approve it again"
        )
        raise PromotionRefusedError(msg) from exc


def _published_then(subject: _Subject, version: int, content_hash: str) -> Published:
    """The document as an earlier version's commit published it."""
    record = subject.document.record
    text = historical_markdown(subject.inputs.corpus, record.markdown_path, version, content_hash)
    if text is None:
        msg = (
            "its published bytes are not in this checkout's history, so they cannot be shown "
            "byte-identical and its approval is not carried; approve it again"
        )
        raise PromotionRefusedError(msg)
    then = record.model_copy(update={"version": version, "content_hash": content_hash})
    return subject.document.model_copy(update={"record": then, "markdown": text})


def _observations(
    document: Published, approvals: tuple[VersionApproval, ...], now: datetime
) -> str:
    """The observations file with every version's audit at the running extractor."""
    by_version = {approval.version: approval for approval in approvals}
    versions = tuple(
        entry.model_copy(
            update={
                "promotion": audit_at_running_extractor(
                    entry.promotion, by_version[entry.version], now
                )
            }
        )
        for entry in document.observations.versions
    )
    return observations_text(document.observations.model_copy(update={"versions": versions}))


def _evidence(corpus: CorpusCheckout, document: Published, prepared: Prepared) -> dict[str, str]:
    if document.has_evidence:
        return {}
    path = evidence_path(document.record.authority_id, document.record.slug)
    return {path: evidence_text(corpus, prepared)}
