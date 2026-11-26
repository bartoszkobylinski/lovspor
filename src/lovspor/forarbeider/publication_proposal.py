"""AR4a: re-evaluate each batch document, render it, build the exact proposal (#738; §9.3, §9.4).

Pure: no clock, no file, no process. :func:`evaluate` judges each planned
document against the bytes the plan bound, and sorts every failure by whose it is:

- **document-local** (an AR3 stage code, :data:`release_attribution.STAGE_LOCAL`):
  a bound FC6 manifest or raw page that is missing, altered, not canonical, of
  another version or not reproducing its extraction (``artifact_integrity_mismatch``);
  text the renderer refuses (``render_failed``). Each becomes a
  :class:`StageFailure` with its evidence digests, for AR3 to quarantine;
- **global** (``prepare:*``): an FC7 decision that is unavailable or
  contradicts the plan (a span not ``allow``, another version or policy, rules
  not satisfied), a selection whose completeness or policy contradicts the
  plan. The plan planned the document, so its bound evidence disagreeing with
  it is not attributable to the document: it is ambiguous, and halts.

:func:`propose` builds the proposal over the base tree the clone holds: the
documents, the catalogue and index with them, and the release manifest named
by its own digest. A version already catalogued, a release already present, a
base tree whose documents are not exactly its catalogue's, or rendered counts
over the plan's caps halt the transaction (:class:`HaltError`).
"""

from collections.abc import Iterator, Mapping, Sequence
from hashlib import sha256
from typing import Literal, NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import DocumentExtraction, extraction_text, text_digest
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_evaluation import PolicyDecision, parse_decision
from lovspor.forarbeider.publication_models import (
    CATALOGUE_FILE,
    INDEX_FILE,
    Catalogue,
    CatalogueEntry,
    DocumentPublication,
    ProposedFile,
    PublicationTransaction,
    ReleaseManifestFile,
    TransactionContent,
    counts_of,
    document_file,
    publication_for,
    role_of,
    transaction_of,
    within_caps,
)
from lovspor.forarbeider.publication_render import catalogue_bytes, render_index, render_version
from lovspor.forarbeider.release_attribution import StageFailure
from lovspor.forarbeider.release_plan import Row
from lovspor.forarbeider.release_plan_models import PlannedBatch, PlannedDocument, ReleasePlan
from lovspor.forarbeider.release_quarantine_models import GlobalCategory, GlobalFailure
from lovspor.forarbeider.version_manifest import parse_version_manifest
from lovspor.forarbeider.version_models import (
    PublicationState,
    SourceState,
    VersionManifest,
    canonical_text,
)
from lovspor.forarbeider.version_release_models import PolicyReference, digest_of


class HaltError(Exception):
    """A failure no document owns: the transaction stops, nothing is written."""

    def __init__(self, code: str, category: GlobalCategory, document_id: str | None = None) -> None:
        super().__init__(code)
        self.failure = GlobalFailure(
            code=f"prepare:{code}", category=category, document_id=document_id
        )


class _LocalError(Exception):
    """A failure one document owns, by AR3 stage code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Rendered(NamedTuple):
    planned: PlannedDocument
    row: Row
    extraction: DocumentExtraction
    payload: bytes


class Evaluation(NamedTuple):
    rendered: tuple[Rendered, ...]
    local: tuple[StageFailure, ...]
    halts: tuple[GlobalFailure, ...]


class Target(NamedTuple):
    """The batch a transaction is for, and where it would be published."""

    plan: ReleasePlan
    batch: PlannedBatch
    path: Literal["A", "B"]
    base_commit: str


def _artifact(artifacts: Mapping[str, bytes], key: str | None) -> bytes:
    data = None if key is None else artifacts.get(key)
    if data is None or sha256(data).hexdigest() != key:
        raise _LocalError("artifact_integrity_mismatch")
    return data


def _manifest(row: Row, artifacts: Mapping[str, bytes]) -> VersionManifest:
    try:
        manifest = parse_version_manifest(_artifact(artifacts, row.binding.manifest_sha256))
    except ForarbeiderRefusedError as error:
        raise _LocalError("artifact_integrity_mismatch") from error
    if manifest.version_id != row.selection.version_id:
        raise _LocalError("artifact_integrity_mismatch")
    return manifest


def _extraction(
    row: Row, manifest: VersionManifest, artifacts: Mapping[str, bytes]
) -> DocumentExtraction:
    bound = set(row.selection.source_sha256s)
    if any(page.raw_sha256 not in bound for page in manifest.provenance.pages):
        raise _LocalError("artifact_integrity_mismatch")
    pages = [
        SourcePage(_artifact(artifacts, page.raw_sha256), page.url, page.retrieved_at)
        for page in manifest.provenance.pages
    ]
    try:
        extraction = extract_document(pages)
    except ForarbeiderRefusedError as error:
        raise _LocalError("artifact_integrity_mismatch") from error
    reproduced = text_digest(extraction_text(extraction)) == manifest.provenance.extraction_sha256
    if not reproduced or extraction.document_id != row.selection.document_id:
        raise _LocalError("artifact_integrity_mismatch")
    return extraction


def _decision(row: Row, artifacts: Mapping[str, bytes]) -> PolicyDecision:
    key = row.binding.policy_decision_sha256
    data = None if key is None else artifacts.get(key)
    if data is None or sha256(data).hexdigest() != key:
        raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id)
    try:
        return parse_decision(data)
    except ForarbeiderRefusedError as error:
        raise HaltError("fc7_decision_unavailable", "unknown", row.selection.document_id) from error


def _require_allowed(row: Row, decision: PolicyDecision, extraction: DocumentExtraction) -> None:
    selection = row.selection
    policy = selection.policy
    same = (
        policy is not None
        and (decision.document_id, decision.version_id)
        == (selection.document_id, selection.version_id)
        and (decision.policy_id, decision.policy_version, decision.policy_sha256)
        == (policy.policy_id, policy.policy_version, policy.policy_sha256)
    )
    allowed = {item.entry_id for item in decision.entries if item.decision == "allow"}
    entries = {entry.id for part in extraction.parts if not part.annex for entry in part.entries}
    every = all(item.decision == "allow" for item in decision.entries) and all(
        item.decision == "allow" for item in decision.attachments
    )
    if not (same and decision.document.rules_satisfied and every and entries <= allowed):
        raise HaltError("fc7_decision_contradicts_plan", "unknown", selection.document_id)


def _require_plan_claims(row: Row, policy: PolicyReference) -> None:
    selection = row.selection
    if selection.policy != policy:
        raise HaltError("policy_mismatch", "policy", selection.document_id)
    complete = (selection.source_state, selection.publication_state) == (
        SourceState.SOURCE_COMPLETE,
        PublicationState.PUBLICATION_COMPLETE,
    )
    if not complete:
        raise HaltError("completeness_contradicts_plan", "unknown", selection.document_id)


def _evidence(row: Row) -> tuple[str, ...]:
    keys = {row.binding.manifest_sha256, row.binding.policy_decision_sha256} - {None}
    return tuple(sorted(key for key in keys if key is not None)) or (digest_of(row.binding),)


def _one(planned: PlannedDocument, row: Row, artifacts: Mapping[str, bytes]) -> Rendered:
    manifest = _manifest(row, artifacts)
    extraction = _extraction(row, manifest, artifacts)
    decision = _decision(row, artifacts)
    _require_allowed(row, decision, extraction)
    policy = row.selection.policy
    if policy is None:  # _require_allowed proved it is not; kept for the type
        raise HaltError("policy_mismatch", "policy", row.selection.document_id)
    try:
        payload = render_version(extraction, manifest.version_id, policy)
    except ForarbeiderRefusedError as error:
        raise _LocalError("render_failed") from error
    return Rendered(planned, row, extraction, payload)


def _failure(row: Row, code: str) -> StageFailure:
    version = row.selection.version_id or "0" * 64
    return StageFailure(
        document_id=row.selection.document_id,
        version_id=version,
        code=code,
        evidence_sha256s=_evidence(row),
    )


def evaluate(
    documents: Sequence[tuple[PlannedDocument, Row]],
    artifacts: Mapping[str, bytes],
    policy: PolicyReference,
) -> Evaluation:
    """Every batch document rendered, or its failure, local or global; never the first only."""
    rendered: list[Rendered] = []
    local: list[StageFailure] = []
    halts: list[GlobalFailure] = []
    for planned, row in documents:
        try:
            _require_plan_claims(row, policy)
            rendered.append(_one(planned, row, artifacts))
        except _LocalError as found:
            local.append(_failure(row, found.code))
        except HaltError as found:
            halts.append(found.failure)
    return Evaluation(tuple(rendered), tuple(local), tuple(halts))


# --- the proposal over the base tree ---------------------------------------------------------


def _parsed_catalogue(base: Mapping[str, bytes]) -> Catalogue:
    raw = base.get(CATALOGUE_FILE)
    if raw is None:
        catalogue = Catalogue()
    else:
        try:
            catalogue = Catalogue.model_validate_json(raw)
        except ValidationError as error:
            raise HaltError("base_tree_inconsistent", "repository") from error
        if catalogue_bytes(catalogue) != raw:
            raise HaltError("base_tree_inconsistent", "repository")
    return catalogue


def base_catalogue(base: Mapping[str, bytes]) -> Catalogue:
    """The base tree's catalogue, which must list exactly the documents the base holds.

    Raises:
        HaltError: ``base_tree_inconsistent``.
    """
    catalogue = _parsed_catalogue(base)
    held = {
        path: sha256(data).hexdigest() for path, data in base.items() if role_of(path) == "document"
    }
    listed = {item.path: item.sha256 for item in catalogue.documents}
    if held != listed:
        raise HaltError("base_tree_inconsistent", "repository")
    return catalogue


def _sha(data: bytes | None) -> str | None:
    return None if data is None else sha256(data).hexdigest()


def _entry(item: Rendered, batch_id: str) -> CatalogueEntry:
    extraction = item.extraction
    return CatalogueEntry(
        document_id=extraction.document_id,
        version_id=item.planned.version_id or "",
        document_type=extraction.document_type,
        session=extraction.session,
        proposition_number=extraction.proposition_number,
        title=extraction.title,
        source_url=extraction.source_url,
        path=document_file(extraction.document_id),
        sha256=sha256(item.payload).hexdigest(),
        size_bytes=len(item.payload),
        batch_id=batch_id,
    )


def _publication(item: Rendered, base: Mapping[str, bytes]) -> DocumentPublication:
    planned, binding = item.planned, item.row.binding
    path = document_file(planned.document_id)
    return DocumentPublication(
        document_id=planned.document_id,
        version_id=planned.version_id or "",
        selection_sha256=planned.selection_sha256 or "",
        binding_sha256=planned.binding_sha256 or "",
        manifest_sha256=binding.manifest_sha256 or "",
        policy_decision_sha256=binding.policy_decision_sha256 or "",
        path=path,
        sha256=sha256(item.payload).hexdigest(),
        size_bytes=len(item.payload),
        previous_sha256=_sha(base.get(path)),
    )


def _new_catalogue(base: Catalogue, rendered: Sequence[Rendered], batch_id: str) -> Catalogue:
    known = {item.document_id: item for item in base.documents}
    for item in rendered:
        found = known.get(item.planned.document_id)
        if found is not None and found.version_id == item.planned.version_id:
            raise HaltError("version_already_published", "repository", item.planned.document_id)
        known[item.planned.document_id] = _entry(item, batch_id)
    return Catalogue(documents=tuple(known[key] for key in sorted(known)))


def _file(path: str, payload: bytes, base: Mapping[str, bytes]) -> ProposedFile:
    role = role_of(path)
    if role is None:  # every path here is built from the grammar
        raise HaltError("unexpected_change", "repository")
    return ProposedFile(
        path=path,
        role=role,
        sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
        previous_sha256=_sha(base.get(path)),
    )


def _manifest_file(
    target: Target,
    documents: tuple[DocumentPublication, ...],
    files: tuple[ProposedFile, ...],
    sources: tuple[str, ...],
) -> bytes:
    basis = target.plan.basis
    manifest = ReleaseManifestFile(
        publication=publication_for(target.path, target.base_commit),
        plan_id=target.plan.plan_id,
        batch_id=target.batch.batch_id,
        batch_sequence=target.batch.sequence,
        policy=basis.policy,
        inventory_sha256=basis.inventory_sha256,
        limits_sha256=basis.limits_sha256,
        quarantine_sha256=basis.quarantine_sha256 or "",
        documents=documents,
        files=files,
        source_sha256s=sources,
    )
    return canonical_text(manifest).encode()


def _payloads(
    target: Target, rendered: Sequence[Rendered], base: Mapping[str, bytes]
) -> dict[str, bytes]:
    catalogue = _new_catalogue(base_catalogue(base), rendered, target.batch.batch_id)
    payloads = {document_file(item.planned.document_id): item.payload for item in rendered}
    payloads[CATALOGUE_FILE] = catalogue_bytes(catalogue)
    payloads[INDEX_FILE] = render_index(catalogue)
    return payloads


def _sources(rendered: Sequence[Rendered]) -> tuple[str, ...]:
    return tuple(sorted({key for item in rendered for key in item.row.selection.source_sha256s}))


def propose(
    target: Target, rendered: Sequence[Rendered], base: Mapping[str, bytes]
) -> tuple[PublicationTransaction, dict[str, bytes]]:
    """The transaction and every proposed byte string, by path, over ``base``.

    Raises:
        HaltError: the base tree, an already published version or release, or the caps.
    """
    payloads = _payloads(target, rendered, base)
    documents = tuple(_publication(item, base) for item in sorted(rendered, key=_id))
    files = tuple(_file(path, data, base) for path, data in sorted(payloads.items()))
    manifest = _manifest_file(target, documents, files, _sources(rendered))
    path = f"releases/{sha256(manifest).hexdigest()}.json"
    if path in base:
        raise HaltError("release_already_published", "repository")
    payloads[path] = manifest
    transaction = _transaction(target, documents, payloads, base)
    if not within_caps(transaction.content.counts, target.plan.basis.caps):
        raise HaltError("proposal_exceeds_batch_caps", "policy")
    return transaction, payloads


def _id(item: Rendered) -> str:
    return item.planned.document_id


def _transaction(
    target: Target,
    documents: tuple[DocumentPublication, ...],
    payloads: Mapping[str, bytes],
    base: Mapping[str, bytes],
) -> PublicationTransaction:
    files = tuple(_file(path, data, base) for path, data in sorted(payloads.items()))
    manifest = next(item for item in files if item.role == "release_manifest")
    content = TransactionContent(
        publication=publication_for(target.path, target.base_commit),
        plan_id=target.plan.plan_id,
        batch_id=target.batch.batch_id,
        batch_sequence=target.batch.sequence,
        quarantine_sha256=target.plan.basis.quarantine_sha256 or "",
        base_catalogue_sha256=_sha(base.get(CATALOGUE_FILE)),
        documents=documents,
        release_manifest_path=manifest.path,
        release_manifest_sha256=manifest.sha256,
        files=files,
        counts=counts_of(files, len(documents)),
        pending_release_blockers=target.plan.basis.pending_release_blockers,
    )
    return transaction_of(content)


def stage_payloads(
    payloads: Mapping[str, bytes], base: Mapping[str, bytes]
) -> Iterator[tuple[str, bytes]]:
    """Every byte string a rollback or a write may need, by digest: new and previous."""
    for path, data in payloads.items():
        yield sha256(data).hexdigest(), data
        previous = base.get(path)
        if previous is not None:
            yield sha256(previous).hexdigest(), previous
