"""FC8: record and verify a document's version lineage (#738; §2.2).

:func:`record_observation` takes raw evidence, and optionally a policy and an
FC7b authorization request, and nothing else the caller could assert: it
rebuilds the FC6 manifest, the FC5 assessment, the FC7 decision and the FC7b
verdict itself, and appends one entry to the chain it was given. The chain is
checked first and refused if it is broken; an observation of another
document, one read before the head, or a version that returns after another
(a cycle) is refused, never repaired. A byte-identical rerun appends nothing.

:func:`verify_lineage` takes entries in any order and the private artifact
store (digest to bytes). It rebuilds the chain from its hash links and checks
every referenced artifact against its digest, then rebuilds the manifest, the
assessment and the decision from the raw sources and requires the very same
bytes. Every problem is a finding: ``invalid`` (altered, conflicting, forked,
cyclic, rewritten) or an explicit ``gap`` (an entry or artifact that is not
there). Nothing is silently repaired, and no status the caller typed is read.

What it cannot prove is stated in the record (:data:`STORAGE_GUARANTEE`):
a consistent chain is not proof of append-only storage, and without an
``expected_head`` anchored elsewhere a chain cut short at its end verifies.
An FC7b verdict is checked by digest and policy binding only: reproducing it
needs the signed records and the trust root, which :mod:`policy_authorization`
verifies on its own.

Nothing is fetched, written, signed or published here.
"""

import hashlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import NamedTuple

from pydantic import ValidationError

from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_extract import SourcePage
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.policy_authorization import (
    AuthorizationRequest,
    authorization_text,
    parse_authorization,
    verify_authorization,
)
from lovspor.forarbeider.policy_evaluation import decision_text, evaluate_policy, parse_decision
from lovspor.forarbeider.policy_trust import TrustRoot
from lovspor.forarbeider.publication_policy import (
    PublicationPolicy,
    parse_policy,
    policy_text,
)
from lovspor.forarbeider.span_assessment import assess_document, assessment_text, parse_assessment
from lovspor.forarbeider.version_lineage_models import (
    ChainLink,
    LineageEntry,
    LineageFinding,
    LineageVerification,
    ProcessingVersions,
    status_of,
)
from lovspor.forarbeider.version_manifest import Evidence as SourceEvidence
from lovspor.forarbeider.version_manifest import (
    build_version_manifest,
    manifest_text,
    parse_version_manifest,
)
from lovspor.forarbeider.version_models import (
    PageProvenance,
    Provenance,
    SupportingProvenance,
    VersionManifest,
    canonical_text,
)

#: Entry fields that place an observation in the chain rather than describe it.
_CHAIN_FIELDS = frozenset(
    {"sequence", "previous_entry_sha256", "previous_version_id", "content_change"}
)


class Observation(NamedTuple):
    """What a caller offers for one observation; every artifact is rebuilt from it."""

    evidence: SourceEvidence
    policy: PublicationPolicy | None = None
    authorization: AuthorizationRequest | None = None
    root: TrustRoot | None = None


class Recorded(NamedTuple):
    """The entry for an observation, its bytes, and the private artifacts it names."""

    entry: LineageEntry
    raw: bytes
    artifacts: Mapping[str, bytes]
    appended: bool


class _Link(NamedTuple):
    key: str
    entry: LineageEntry


class _Evaluation(NamedTuple):
    policy_sha256: str | None = None
    decision_sha256: str | None = None
    authorization_sha256: str | None = None
    evaluator_version: str | None = None
    verifier_version: str | None = None


_NOT_EVALUATED = _Evaluation()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def entry_text(entry: LineageEntry) -> str:
    return canonical_text(entry)


def parse_entry(raw: bytes) -> LineageEntry:
    """The entry in ``raw``, which must be its canonical bytes.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid`` for anything else, including
            an entry claiming a status, an eligibility or a change its version
            ids do not show.
    """
    try:
        entry = LineageEntry.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry") from error
    if entry_text(entry).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "lineage entry")
    return entry


def verification_text(verification: LineageVerification) -> str:
    return canonical_text(verification)


# --- recording ---------------------------------------------------------------------


def record_observation(history: Sequence[bytes], observation: Observation) -> Recorded:
    """``history`` with one more observation; nothing appended for a byte-identical rerun.

    Raises:
        ForarbeiderRefusedError: ``lineage_conflict`` for a broken history, another
            document, an observation before the head, a returning version, or an
            authorization that does not fit the policy; the refusals of FC3-FC7b.
    """
    chain = _intact(history)
    store: dict[str, bytes] = {}
    fields = _observed(observation, store)
    head = chain[-1] if chain else None
    entry = LineageEntry.model_validate({**fields, **_position(head, fields, chain)})
    if head is not None and _same_observation(head.entry, entry):
        return Recorded(head.entry, _raw_of(history, head.key), store, appended=False)
    return Recorded(entry, entry_text(entry).encode(), store, appended=True)


def _intact(history: Sequence[bytes]) -> list[_Link]:
    if not history:
        return []
    parsed, findings = _parsed(history)
    walked, chain_findings = _walk(parsed)
    problems = sorted({finding.code for finding in (*findings, *chain_findings)})
    if problems:
        raise _conflict("history: " + ",".join(problems))
    return walked


def _position(
    head: _Link | None, fields: dict[str, object], chain: list[_Link]
) -> dict[str, object]:
    if head is None:
        return {"sequence": 0, "previous_entry_sha256": None, "previous_version_id": None}
    version = fields["version_id"]
    if fields["document_id"] != head.entry.document_id:
        raise _conflict("another document")
    observed = fields["observed_at"]
    if not isinstance(observed, datetime) or observed < head.entry.observed_at:
        raise _conflict("observed before the head")
    if version != head.entry.version_id and version in {link.entry.version_id for link in chain}:
        raise _conflict("a version returns after another")
    return {
        "sequence": head.entry.sequence + 1,
        "previous_entry_sha256": head.key,
        "previous_version_id": head.entry.version_id,
        "content_change": "unchanged" if version == head.entry.version_id else "new_version",
    }


def _same_observation(head: LineageEntry, entry: LineageEntry) -> bool:
    return head.model_dump(exclude=set(_CHAIN_FIELDS)) == entry.model_dump(
        exclude=set(_CHAIN_FIELDS)
    )


def _raw_of(history: Sequence[bytes], key: str) -> bytes:
    return next(raw for raw in history if sha(raw) == key)


def _observed(observation: Observation, store: dict[str, bytes]) -> dict[str, object]:
    source = observation.evidence
    manifest = build_version_manifest(source)
    assessment = assess_document(source)
    store.update({sha(page.payload): page.payload for page in _pages(source)})
    evaluation = _evaluation(observation, store)
    return {
        "document_id": manifest.content.document.document_id,
        "version_id": manifest.version_id,
        "content_change": "first_version",
        "observed_at": _observed_at(manifest.provenance),
        "source_sha256s": _sources(manifest.provenance),
        "manifest_sha256": _kept(store, manifest_text(manifest)),
        "assessment_sha256": _kept(store, assessment_text(assessment)),
        "policy_sha256": evaluation.policy_sha256,
        "policy_decision_sha256": evaluation.decision_sha256,
        "authorization_sha256": evaluation.authorization_sha256,
        "processing": _processing(manifest, assessment.assessment_version, evaluation),
    }


def _processing(
    manifest: VersionManifest, assessment_version: str, evaluation: _Evaluation
) -> ProcessingVersions:
    return ProcessingVersions(
        extractor_version=manifest.content.extractor_version,
        classifier_version=manifest.content.classifier_version,
        assessment_version=assessment_version,
        evaluator_version=evaluation.evaluator_version,
        authorization_verifier_version=evaluation.verifier_version,
    )


def _evaluation(observation: Observation, store: dict[str, bytes]) -> _Evaluation:
    policy = observation.policy
    if policy is None:
        if observation.authorization is not None or observation.root is not None:
            raise _conflict("an authorization without a policy")
        return _NOT_EVALUATED
    decision = evaluate_policy(observation.evidence, policy)
    verdict = _verdict(observation, policy)
    return _Evaluation(
        policy_sha256=_kept(store, policy_text(policy)),
        decision_sha256=_kept(store, decision_text(decision)),
        authorization_sha256=None if verdict is None else _kept(store, verdict),
        evaluator_version=decision.evaluator_version,
        verifier_version=None if verdict is None else "policy-auth/1",
    )


def _verdict(observation: Observation, policy: PublicationPolicy) -> str | None:
    request, root = observation.authorization, observation.root
    if request is None and root is None:
        return None
    if request is None or root is None:
        raise _conflict("an authorization needs both the request and a trust root")
    if request.policy != policy_text(policy).encode():
        raise _conflict("the authorization names another policy")
    return authorization_text(verify_authorization(request, root))


def _kept(store: dict[str, bytes], text: str) -> str:
    raw = text.encode()
    store[sha(raw)] = raw
    return sha(raw)


def _pages(source: SourceEvidence) -> list[SourcePage]:
    landing = [] if source.landing is None else [source.landing]
    return [*source.pages, *landing, *source.captures]


def _sources(provenance: Provenance) -> tuple[str, ...]:
    pages = {page.raw_sha256 for page in provenance.pages}
    return tuple(sorted(pages | {item.raw_sha256 for item in provenance.supporting}))


def _observed_at(provenance: Provenance) -> datetime:
    times = [page.retrieved_at for page in provenance.pages]
    times += [item.retrieved_at for item in provenance.supporting]
    return max(times).astimezone(UTC)


def _conflict(detail: str) -> ForarbeiderRefusedError:
    return ForarbeiderRefusedError(RefusalReason.LINEAGE_CONFLICT, detail)


# --- verifying the chain -----------------------------------------------------------


def verify_lineage(
    entries: Iterable[bytes], artifacts: Mapping[str, bytes], expected_head: str | None = None
) -> LineageVerification:
    """What ``entries`` and ``artifacts`` prove of one document's history; never eligibility."""
    offered = {sha(raw): raw for raw in entries}
    parsed, findings = _parsed(offered.values())
    walked, chain_findings = _walk(parsed)
    found = [*findings, *chain_findings, *_anchor(walked, expected_head)]
    for key, entry in sorted(parsed.items()):
        found.extend(entry_findings(key, entry, artifacts))
    ordered = tuple(sorted(set(found), key=_finding_order))
    head = walked[-1] if walked else None
    return LineageVerification(
        document_id=None if head is None else head.entry.document_id,
        status=status_of(ordered),
        chain=tuple(_chain_link(link) for link in walked),
        head_entry_sha256=None if head is None else head.key,
        head_version_id=None if head is None else head.entry.version_id,
        expected_head_sha256=expected_head,
        content_versions=len({link.entry.version_id for link in walked}),
        observations=len(walked),
        entries_offered=len(offered),
        findings=ordered,
    )


def _finding_order(finding: LineageFinding) -> tuple[int, str, str, str]:
    sequence = -1 if finding.sequence is None else finding.sequence
    return (sequence, finding.code, finding.entry_sha256 or "", finding.detail or "")


def _chain_link(link: _Link) -> ChainLink:
    return ChainLink(
        sequence=link.entry.sequence,
        entry_sha256=link.key,
        version_id=link.entry.version_id,
        content_change=link.entry.content_change,
    )


def _parsed(entries: Iterable[bytes]) -> tuple[dict[str, LineageEntry], list[LineageFinding]]:
    parsed: dict[str, LineageEntry] = {}
    findings: list[LineageFinding] = []
    for raw in entries:
        try:
            parsed[sha(raw)] = parse_entry(raw)
        except ForarbeiderRefusedError:
            findings.append(_finding("entry_malformed", "invalid", sha(raw)))
    return parsed, findings


def _finding(
    code: str, effect: str, key: str | None = None, entry: LineageEntry | None = None
) -> LineageFinding:
    return LineageFinding.model_validate(
        {
            "code": code,
            "effect": effect,
            "entry_sha256": key,
            "sequence": None if entry is None else entry.sequence,
        }
    )


def _walk(parsed: dict[str, LineageEntry]) -> tuple[list[_Link], list[LineageFinding]]:
    """The chain from its single first entry, and a finding for every entry it does not reach."""
    children: dict[str | None, list[str]] = defaultdict(list)
    for key in sorted(parsed):
        children[parsed[key].previous_entry_sha256].append(key)
    findings = _root_findings(children[None])
    walked: list[_Link] = []
    current = children[None][0] if len(children[None]) == 1 else None
    while current is not None and current not in {link.key for link in walked}:
        link = _Link(current, parsed[current])
        if walked:
            findings.extend(_link_findings(walked, link))
        walked.append(link)
        current, fork = _next(children[current])
        findings.extend([_finding("fork", "invalid", link.key, link.entry)] if fork else [])
    return walked, [*findings, *_unreached(parsed, walked)]


def _root_findings(roots: list[str]) -> list[LineageFinding]:
    if not roots:
        return [_finding("root_missing", "gap")]
    return [_finding("fork", "invalid")] if len(roots) > 1 else []


def _next(children: list[str]) -> tuple[str | None, bool]:
    if len(children) > 1:
        return None, True
    return (children[0] if children else None), False


def _link_findings(walked: list[_Link], link: _Link) -> list[LineageFinding]:
    parent, child = walked[-1].entry, link.entry
    earlier = {item.entry.version_id for item in walked}
    returning = child.version_id != parent.version_id and child.version_id in earlier
    checks = (
        ("sequence_mismatch", child.sequence != parent.sequence + 1),
        ("document_conflict", child.document_id != parent.document_id),
        ("version_link_mismatch", child.previous_version_id != parent.version_id),
        ("observation_out_of_order", child.observed_at < parent.observed_at),
        ("version_cycle", returning),
    )
    return [_finding(code, "invalid", link.key, child) for code, failed in checks if failed]


def _unreached(parsed: dict[str, LineageEntry], walked: list[_Link]) -> list[LineageFinding]:
    """One finding for every offered entry the walk did not reach: none disappears."""
    reached = {link.key for link in walked}
    placed = {link.entry.sequence: link.key for link in walked}
    findings = []
    for key, entry in sorted(parsed.items()):
        if key in reached:
            continue
        parent = entry.previous_entry_sha256
        if parent is not None and parent not in parsed:
            code, effect = _orphan(entry, parent, placed)
        else:
            code, effect = _ancestry(key, parsed, reached, placed)
        findings.append(_finding(code, effect, key, entry))
    return findings


def _orphan(entry: LineageEntry, parent: str, placed: dict[int, str]) -> tuple[str, str]:
    """An entry whose parent was not offered: a gap, unless its place is taken (a rewrite)."""
    occupied = placed.get(entry.sequence - 1) not in {None, parent}
    return ("history_rewritten", "invalid") if occupied else ("parent_missing", "gap")


def _ancestry(
    key: str, parsed: dict[str, LineageEntry], reached: set[str], placed: dict[int, str]
) -> tuple[str, str]:
    """Why an entry with an offered parent was not reached.

    Its ancestors lead to a missing parent (it inherits that gap or rewrite),
    to the chain or a second first entry (a fork: invalid), or back to itself
    (a link cycle: invalid).
    """
    seen = {key}
    parent = parsed[key].previous_entry_sha256
    while parent is not None and parent in parsed and parent not in reached:
        if parent in seen:
            return "link_cycle", "invalid"
        seen.add(parent)
        key, parent = parent, parsed[parent].previous_entry_sha256
    if parent is None or parent in reached:
        return "entry_unreachable", "invalid"
    return "entry_unreachable", _orphan(parsed[key], parent, placed)[1]


def _anchor(walked: list[_Link], expected_head: str | None) -> list[LineageFinding]:
    if expected_head is None or (walked and walked[-1].key == expected_head):
        return []
    return [_finding("head_mismatch", "invalid", expected_head)]


# --- verifying the artifacts -------------------------------------------------------


class _Checker:
    """Collects one entry's artifact findings."""

    def __init__(self, key: str, entry: LineageEntry, artifacts: Mapping[str, bytes]) -> None:
        self.key, self.entry, self.artifacts = key, entry, artifacts
        self.findings: list[LineageFinding] = []

    def note(self, code: str, effect: str, detail: str) -> None:
        found = _finding(code, effect, self.key, self.entry)
        self.findings.append(found.model_copy(update={"detail": detail}))

    def fetch(self, digest: str, kind: str) -> bytes | None:
        raw = self.artifacts.get(digest)
        if raw is None:
            self.note("artifact_missing", "gap", kind)
            return None
        if sha(raw) != digest:
            self.note("artifact_altered", "invalid", kind)
            return None
        return raw

    def bound(self, holds: bool, kind: str) -> None:
        if not holds:
            self.note("artifact_mismatch", "invalid", kind)

    def rebuilt(self, raw: bytes, build: Callable[[], str], kind: str) -> None:
        try:
            same = build().encode() == raw
        except ForarbeiderRefusedError:
            same = False
        if not same:
            self.note("artifact_not_rebuilt", "invalid", kind)


def entry_findings(
    key: str, entry: LineageEntry, artifacts: Mapping[str, bytes]
) -> list[LineageFinding]:
    """Every finding about the artifacts ``entry`` names: missing, altered, unbound, not rebuilt."""
    checker = _Checker(key, entry, artifacts)
    sources = _source_bytes(checker)
    manifest = _manifest(checker)
    evidence, problem = _evidence(entry, manifest, sources)
    if problem is not None or (evidence is None and not checker.findings):
        checker.note("evidence_not_reconstructed", "invalid", problem or "inputs")
    if manifest is not None and evidence is not None:
        built = manifest_text(manifest).encode()
        checker.rebuilt(built, lambda: manifest_text(build_version_manifest(evidence)), "manifest")
    _assessment(checker, evidence)
    policy = _policy(checker)
    _decision(checker, evidence, policy)
    _authorization(checker)
    return checker.findings


def _source_bytes(checker: _Checker) -> dict[str, bytes] | None:
    found = {d: checker.fetch(d, "source") for d in checker.entry.source_sha256s}
    kept = {digest: raw for digest, raw in found.items() if raw is not None}
    return kept if len(kept) == len(found) else None


def _parse[T](checker: _Checker, digest: str, kind: str, parse: Callable[[bytes], T]) -> T | None:
    raw = checker.fetch(digest, kind)
    if raw is None:
        return None
    try:
        return parse(raw)
    except ForarbeiderRefusedError:
        checker.note("artifact_invalid", "invalid", kind)
        return None


def _manifest(checker: _Checker) -> VersionManifest | None:
    entry = checker.entry
    manifest = _parse(checker, entry.manifest_sha256, "manifest", parse_version_manifest)
    if manifest is None:
        return None
    content, provenance = manifest.content, manifest.provenance
    checker.bound(manifest.version_id == entry.version_id, "manifest version")
    checker.bound(content.document.document_id == entry.document_id, "manifest document")
    checker.bound(_sources(provenance) == entry.source_sha256s, "manifest sources")
    checker.bound(_observed_at(provenance) == entry.observed_at, "manifest observation time")
    processing = (content.extractor_version, content.classifier_version)
    recorded = (entry.processing.extractor_version, entry.processing.classifier_version)
    checker.bound(processing == recorded, "manifest processing versions")
    return manifest


def _evidence(
    entry: LineageEntry, manifest: VersionManifest | None, sources: dict[str, bytes] | None
) -> tuple[SourceEvidence | None, str | None]:
    """The raw evidence the manifest's provenance names, or why it cannot be rebuilt.

    ``None`` with no reason only when a source or the manifest is already a
    finding (missing, altered or invalid); every other refusal names itself.
    """
    if manifest is None or sources is None:
        return None, None
    provenance = manifest.provenance
    if _sources(provenance) != entry.source_sha256s or not set(entry.source_sha256s) <= set(
        sources
    ):
        return None, "source mapping"
    landing = [s for s in provenance.supporting if s.role == "landing"]
    if len(landing) > 1:
        return None, "landing records"
    return _source_evidence(provenance, sources), None


def _source_evidence(provenance: Provenance, sources: dict[str, bytes]) -> SourceEvidence:
    def page(item: PageProvenance | SupportingProvenance) -> SourcePage:
        return SourcePage(sources[item.raw_sha256], item.url, item.retrieved_at)

    supporting = {
        role: tuple(page(s) for s in provenance.supporting if s.role == role)
        for role in ("landing", "capture")
    }
    landing = supporting["landing"][0] if supporting["landing"] else None
    return SourceEvidence(tuple(page(p) for p in provenance.pages), landing, supporting["capture"])


def _assessment(checker: _Checker, evidence: SourceEvidence | None) -> None:
    entry = checker.entry
    assessment = _parse(checker, entry.assessment_sha256, "assessment", parse_assessment)
    if assessment is None:
        return
    checker.bound(assessment.version_id == entry.version_id, "assessment version")
    recorded = entry.processing.assessment_version
    checker.bound(assessment.assessment_version == recorded, "assessment processing version")
    if evidence is not None:
        built = assessment_text(assessment).encode()
        checker.rebuilt(built, lambda: assessment_text(assess_document(evidence)), "assessment")


def _policy(checker: _Checker) -> PublicationPolicy | None:
    digest = checker.entry.policy_sha256
    return None if digest is None else _parse(checker, digest, "policy", parse_policy)


def _decision(
    checker: _Checker, evidence: SourceEvidence | None, policy: PublicationPolicy | None
) -> None:
    entry = checker.entry
    if entry.policy_decision_sha256 is None:
        return
    decision = _parse(checker, entry.policy_decision_sha256, "decision", parse_decision)
    if decision is None:
        return
    checker.bound(decision.version_id == entry.version_id, "decision version")
    checker.bound(decision.assessment_sha256 == entry.assessment_sha256, "decision assessment")
    checker.bound(decision.policy_sha256 == entry.policy_sha256, "decision policy")
    checker.bound(decision.evaluator_version == entry.processing.evaluator_version, "evaluator")
    if evidence is not None and policy is not None:
        built = decision_text(decision).encode()
        checker.rebuilt(built, lambda: decision_text(evaluate_policy(evidence, policy)), "decision")
    elif not checker.findings:
        checker.note("artifact_not_rebuilt", "invalid", "decision inputs")


def _authorization(checker: _Checker) -> None:
    entry = checker.entry
    if entry.authorization_sha256 is None:
        return
    verdict = _parse(checker, entry.authorization_sha256, "authorization", parse_authorization)
    if verdict is None:
        return
    checker.bound(verdict.policy_sha256 == entry.policy_sha256, "authorization policy")
    recorded = entry.processing.authorization_verifier_version
    checker.bound(verdict.verifier_version == recorded, "authorization verifier")
