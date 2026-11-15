"""FC8b: build and verify a release manifest over exact document versions (#738).

:func:`build_release` takes, for each offered document, its FC8a lineage
entries, the head the caller states and, optionally, the FC7b signed records;
plus the private artifact store and a trust root. It takes no status,
eligibility or selection from the caller. For each document it:

1. verifies the whole lineage with :func:`~lovspor.forarbeider.version_lineage.verify_lineage`,
   which rebuilds the FC6 manifest, the FC5 assessment and the FC7 decision
   of every entry from the raw sources and requires the very same bytes. The
   stated head is checked as ``expected_head``, so a head that is not the
   chain's last entry (a cut chain, a stale head) excludes the document; it is
   never *trusted*, because nothing anchors it outside the caller;
2. selects the chain's head and nothing else. A blocked head is never
   replaced by an older version that would pass;
3. re-verifies the FC7b verdict through
   :func:`~lovspor.forarbeider.policy_authorization.verify_authorization_record`,
   which must reproduce the recorded bytes from the signed records and the
   trust root;
4. lists every blocker: integrity (``excluded``), the unanchored head, the
   source and publication states, FC7, FC7b and the unresolved owner
   decisions D10 and D11.

Every offered document appears in the manifest, sorted by id; a document
offered twice, or a lineage offered under another document's id, is refused.

:func:`verify_release` rebuilds the manifest from the same inputs and reports
every difference, document by document. Matching is all it shows: the head
stays unanchored and nothing is eligible.

Nothing is fetched, written, signed or published here.
"""

import re
from collections.abc import Iterator, Mapping, Sequence
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import (
    AuthorizationRequest,
    verify_authorization_record,
)
from lovspor.forarbeider.policy_evaluation import PolicyDecision
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.version_diff import Snapshot, load_snapshot
from lovspor.forarbeider.version_lineage import parse_entry, sha, verify_lineage
from lovspor.forarbeider.version_lineage_models import LineageVerification
from lovspor.forarbeider.version_models import PublicationState, SourceState, canonical_text
from lovspor.forarbeider.version_release_models import (
    Blocker,
    Category,
    DocumentBinding,
    DocumentObservation,
    DocumentSelection,
    PolicyReference,
    ReleaseContent,
    ReleaseFinding,
    ReleaseManifest,
    ReleaseVerification,
    counts_of,
    digest_of,
    status_of,
)

#: Owner decisions with no verifiable mechanism: every document is blocked by them.
_GOVERNANCE = ("d10_legal_assessment_unresolved", "d11_automated_publication_not_accepted")


#: The artifact digests a binding names once a version is selected.
_ARTIFACTS = (
    "lineage_head_sha256",
    "manifest_sha256",
    "assessment_sha256",
    "policy_decision_sha256",
    "authorization_sha256",
)


class DocumentOffer(NamedTuple):
    """What a caller offers for one document; none of it is trusted until verified."""

    document_id: str
    entries: tuple[bytes, ...]
    claimed_head: str | None = None
    authorization: AuthorizationRequest | None = None


class _Inputs(NamedTuple):
    artifacts: Mapping[str, bytes]
    root: TrustRoot | None


class _Document(NamedTuple):
    selection: DocumentSelection
    binding: DocumentBinding
    observation: DocumentObservation


def release_text(manifest: ReleaseManifest) -> str:
    return canonical_text(manifest)


def parse_release(raw: bytes) -> ReleaseManifest:
    """The manifest in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including a
            manifest claiming release, a forged digest or reordered documents.
    """
    try:
        manifest = ReleaseManifest.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest") from error
    if release_text(manifest).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "release manifest")
    return manifest


# --- building ------------------------------------------------------------------------


def build_release(
    offers: Sequence[DocumentOffer],
    artifacts: Mapping[str, bytes],
    root: TrustRoot | None = None,
) -> ReleaseManifest:
    """The release manifest for ``offers``: every document, its head version and its blockers.

    Raises:
        ForarbeiderRefusedError: ``lineage_conflict`` for a document offered twice
            or a lineage entry of another document.
    """
    _check_identities(offers)
    inputs = _Inputs(artifacts, root)
    built = [_document(o, inputs) for o in sorted(offers, key=lambda o: o.document_id)]
    content = ReleaseContent(documents=tuple(item.selection for item in built))
    bindings = tuple(item.binding for item in built)
    return ReleaseManifest(
        release_id=digest_of(content),
        content=content,
        counts=counts_of(content.documents),
        bindings_sha256=digest_of(*bindings),
        bindings=bindings,
        observations=tuple(item.observation for item in built),
    )


def _check_identities(offers: Sequence[DocumentOffer]) -> None:
    seen: set[str] = set()
    for offer in offers:
        _check_offer(offer)
        if offer.document_id in seen:
            raise _conflict("document offered twice")
        seen.add(offer.document_id)
        for raw in offer.entries:
            try:
                document = parse_entry(raw).document_id
            except ForarbeiderRefusedError:
                continue
            if document != offer.document_id:
                raise _conflict("lineage entry of another document")


def _check_offer(offer: DocumentOffer) -> None:
    if not offer.document_id.strip():
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "document id")
    head = offer.claimed_head
    if head is not None and re.fullmatch(r"[0-9a-f]{64}", head) is None:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "claimed head")


def _conflict(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)


def _document(offer: DocumentOffer, inputs: _Inputs) -> _Document:
    lineage = verify_lineage(offer.entries, inputs.artifacts, offer.claimed_head)
    unbound = _unbound(offer, lineage)
    if lineage.status != "verified" or lineage.head_entry_sha256 is None:
        return _Document(_excluded(offer, lineage), *unbound)
    snapshot = load_snapshot(_head_raw(offer, lineage.head_entry_sha256), inputs.artifacts)
    return _selected(offer, inputs, snapshot, unbound)


def _unbound(
    offer: DocumentOffer, lineage: LineageVerification
) -> tuple[DocumentBinding, DocumentObservation]:
    """The binding and observation of a document no version is selected for, yet."""
    binding = DocumentBinding.model_validate(
        {
            "document_id": offer.document_id,
            "lineage_status": lineage.status,
            "lineage_entries": lineage.entries_offered,
            "claimed_head_sha256": offer.claimed_head,
        }
        | dict.fromkeys(_ARTIFACTS)
    )
    observation = DocumentObservation(
        document_id=offer.document_id,
        observed_at=None,
        observations=lineage.observations,
        content_versions=lineage.content_versions,
    )
    return binding, observation


def _head_raw(offer: DocumentOffer, key: str) -> bytes:
    return next(raw for raw in offer.entries if sha(raw) == key)


def _excluded(offer: DocumentOffer, lineage: LineageVerification) -> DocumentSelection:
    found: list[tuple[Category, str]] = [("integrity", f"lineage_{lineage.status}")]
    found += [("integrity", f"lineage:{finding.code}") for finding in lineage.findings]
    found += [("trust", code) for code in _trust(offer)]
    found += [("governance", code) for code in _GOVERNANCE]
    return DocumentSelection(
        document_id=offer.document_id,
        status="excluded",
        version_id=None,
        source_sha256s=(),
        policy=None,
        processing=None,
        source_state=None,
        publication_state=None,
        blockers=_blockers(found),
    )


def _blockers(found: list[tuple[Category, str]]) -> tuple[Blocker, ...]:
    unique = sorted(set(found))
    return tuple(Blocker(code=code, category=category) for category, code in unique)


def _trust(offer: DocumentOffer) -> Iterator[str]:
    if offer.claimed_head is None:
        yield "head_missing"
    yield "head_unanchored"


def _selected(
    offer: DocumentOffer,
    inputs: _Inputs,
    snapshot: Snapshot,
    unbound: tuple[DocumentBinding, DocumentObservation],
) -> _Document:
    entry, manifest, decision = snapshot.entry, snapshot.manifest, snapshot.decision
    found: list[tuple[Category, str]] = [("trust", code) for code in _trust(offer)]
    found += [("governance", code) for code in _GOVERNANCE]
    found += _completeness(manifest.source.state, manifest.publication.state)
    found += _policy(decision)
    found += _authorization(offer, inputs, entry.authorization_sha256)
    selection = DocumentSelection(
        document_id=offer.document_id,
        status=status_of(_blockers(found)),
        version_id=entry.version_id,
        source_sha256s=entry.source_sha256s,
        policy=_reference(decision),
        processing=entry.processing,
        source_state=manifest.source.state,
        publication_state=manifest.publication.state,
        blockers=_blockers(found),
    )
    return _Document(selection, *_bound(snapshot, *unbound))


def _bound(
    snapshot: Snapshot, binding: DocumentBinding, observation: DocumentObservation
) -> tuple[DocumentBinding, DocumentObservation]:
    entry = snapshot.entry
    bound = binding.model_copy(
        update={
            "lineage_head_sha256": snapshot.key,
            "manifest_sha256": entry.manifest_sha256,
            "assessment_sha256": entry.assessment_sha256,
            "policy_decision_sha256": entry.policy_decision_sha256,
            "authorization_sha256": entry.authorization_sha256,
        }
    )
    return bound, observation.model_copy(update={"observed_at": entry.observed_at})


def _completeness(source: SourceState, publication: PublicationState) -> list[tuple[Category, str]]:
    found: list[tuple[Category, str]] = []
    if source is not SourceState.SOURCE_COMPLETE:
        found.append(("source", f"source_{source.value.removeprefix('source_')}"))
    if publication is not PublicationState.PUBLICATION_COMPLETE:
        name = publication.value.removeprefix("publication_")
        found.append(("publication", f"publication_{name}"))
    return found


def _reference(decision: PolicyDecision | None) -> PolicyReference | None:
    if decision is None:
        return None
    return PolicyReference(
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        policy_sha256=decision.policy_sha256,
    )


def _policy(decision: PolicyDecision | None) -> list[tuple[Category, str]]:
    if decision is None:
        return [("policy", "policy_not_evaluated")]
    decided = {item.decision for item in decision.entries}
    decided |= {item.decision for item in decision.attachments}
    found: list[tuple[Category, str]] = [("policy", f"fc7:{b}") for b in decision.document.blockers]
    if not decision.document.rules_satisfied:
        found.append(("policy", "fc7_rules_not_satisfied"))
    found += [("policy", f"fc7_{value}") for value in ("deny", "unresolved") if value in decided]
    return found


def _authorization(
    offer: DocumentOffer, inputs: _Inputs, digest: str | None
) -> list[tuple[Category, str]]:
    if digest is None:
        return [("authorization", "fc7b_authorization_missing")]
    if offer.authorization is None or inputs.root is None:
        return [("authorization", "fc7b_authorization_not_reverified")]
    try:
        verdict = verify_authorization_record(
            inputs.artifacts[digest], offer.authorization, inputs.root
        )
    except ForarbeiderRefusedError:
        return [("integrity", "fc7b_authorization_not_reproduced")]
    found: list[tuple[Category, str]] = [("authorization", f"fc7b:{b}") for b in verdict.blockers]
    if verdict.status != "signatures_verified":
        found.append(("authorization", f"fc7b_{verdict.status}"))
    return found


# --- verifying -----------------------------------------------------------------------


def verify_release(
    raw: bytes,
    offers: Sequence[DocumentOffer],
    artifacts: Mapping[str, bytes],
    root: TrustRoot | None = None,
) -> ReleaseVerification:
    """Whether ``raw`` is exactly the manifest ``offers`` rebuild; never eligibility."""
    try:
        offered: ReleaseManifest | None = parse_release(raw)
    except ForarbeiderRefusedError:
        offered = None
    try:
        rebuilt: ReleaseManifest | None = build_release(offers, artifacts, root)
    except ForarbeiderRefusedError:
        rebuilt = None
    findings = _differences(offered, rebuilt)
    return ReleaseVerification(
        status="invalid" if findings else "reproduced",
        offered_release_id=None if offered is None else offered.release_id,
        rebuilt_release_id=None if rebuilt is None else rebuilt.release_id,
        findings=findings,
    )


def _differences(
    offered: ReleaseManifest | None, rebuilt: ReleaseManifest | None
) -> tuple[ReleaseFinding, ...]:
    found: list[ReleaseFinding] = []
    if offered is None:
        found.append(ReleaseFinding(code="manifest_malformed"))
    if rebuilt is None:
        found.append(ReleaseFinding(code="inputs_refused"))
    if offered is not None and rebuilt is not None:
        found += _compared(offered, rebuilt)
    return tuple(sorted(found, key=lambda item: (item.document_id or "", item.code)))


def _compared(offered: ReleaseManifest, rebuilt: ReleaseManifest) -> list[ReleaseFinding]:
    found = [
        ReleaseFinding(code=code)
        for code, differs in (
            ("release_id_mismatch", offered.release_id != rebuilt.release_id),
            ("bindings_mismatch", offered.bindings_sha256 != rebuilt.bindings_sha256),
        )
        if differs
    ]
    before, after = _by_document(offered), _by_document(rebuilt)
    for document_id in sorted(before.keys() | after.keys()):
        found += _document_differences(document_id, before.get(document_id), after.get(document_id))
    if not found and release_text(offered) != release_text(rebuilt):
        found.append(ReleaseFinding(code="manifest_differs"))
    return found


def _by_document(manifest: ReleaseManifest) -> dict[str, _Document]:
    rows = zip(manifest.content.documents, manifest.bindings, manifest.observations, strict=True)
    return {row[0].document_id: _Document(*row) for row in rows}


def _document_differences(
    document_id: str, offered: _Document | None, rebuilt: _Document | None
) -> list[ReleaseFinding]:
    if offered is None:
        return [ReleaseFinding(code="document_missing", document_id=document_id)]
    if rebuilt is None:
        return [ReleaseFinding(code="document_added", document_id=document_id)]
    return [
        ReleaseFinding(code=f"{part}_changed", document_id=document_id)
        for part, before, after in zip(_Document._fields, offered, rebuilt, strict=True)
        if before != after
    ]
