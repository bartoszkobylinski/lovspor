"""The fact sources of one site build (ADR-0014 Decision 4).

A build reads three artifact classes and nothing else (ADR:1085-1119):
the corpus release's ``site-manifest.json`` (``corpus`` facts), the
capability document captured from the running server (``hosted`` facts)
and the checkout at ``lovspor_commit`` — in this PR the tool-surface
descriptor computed against it (``code`` facts). ``fact_registry``
turns those three into the registry templates render from; the kind
of every entry is decided here, by its artifact, never by the template.

Hosted values follow the observation record that holds them
(ADR:747-752): a value the record carries is rendered; a value the
record does not carry — the subject ``unobserved``, the authenticated
step ``unobserved``, a step whose outcome listed nothing, a process
that answered not-ready without an attestation — becomes an
``Unobserved`` reading with the record's reason and ``observed_at``,
so the page degrades to *not attested at this release* instead of
rendering a previous value, a blank or a zero.
"""

import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from lovspor.site.capabilities import (
    AuthenticatedStep,
    CapabilityDocument,
    CommitSha,
    ProcessRecord,
    TransportRecord,
)
from lovspor.site.facts import (
    CAPABILITIES_ARTIFACT,
    DESCRIPTOR_PREFIX,
    MANIFEST_ARTIFACT,
    FactRegistry,
    FactSource,
    FactValue,
    Unobserved,
)
from lovspor.tool_surface import ToolSurfaceDescriptor

COMPARISONS = (
    "runtime_tree_match",
    "environment_match",
    "tool_surface_match",
    "transport_surface_match",
    "oauth_discovery_consistent",
)


class CorpusManifest(BaseModel):
    """The fields of the corpus release's ``site-manifest.json`` the site reads.

    The file is ADR-0013's (``publish/emit.py``): unknown keys are
    tolerated so a corpus-side schema bump adding a field the site never
    reads does not fail the site build.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    corpus_commit: CommitSha
    corpus_commit_time: str
    engine_version: str
    documents: Annotated[int, Field(ge=0)]


class BuildArtifacts(BaseModel):
    """Everything one build read, by logical identity and hash."""

    model_config = ConfigDict(frozen=True)

    lovspor_commit: CommitSha
    manifest: CorpusManifest
    manifest_bytes: bytes
    document: CapabilityDocument
    capability_bytes: bytes
    descriptor: ToolSurfaceDescriptor

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(self.manifest_bytes).hexdigest()

    @property
    def capability_sha256(self) -> str:
        return hashlib.sha256(self.capability_bytes).hexdigest()

    @property
    def descriptor_artifact(self) -> str:
        return f"{DESCRIPTOR_PREFIX}{self.lovspor_commit}"

    def artifact_hashes(self) -> tuple[tuple[str, str], ...]:
        """``(id, sha256)`` per consumed artifact, sorted by id."""
        return tuple(
            sorted(
                (
                    (MANIFEST_ARTIFACT, self.manifest_sha256),
                    (CAPABILITIES_ARTIFACT, self.capability_sha256),
                    (self.descriptor_artifact, self.descriptor.schema_sha256),
                )
            )
        )


def _corpus(fact_id: str, field: str, value: FactValue) -> FactSource:
    return FactSource(
        id=fact_id, kind="corpus", artifact=MANIFEST_ARTIFACT, field=field, value=value
    )


def _corpus_facts(artifacts: BuildArtifacts) -> tuple[FactSource, ...]:
    manifest = artifacts.manifest
    return (
        _corpus("corpus.documents", "documents", manifest.documents),
        _corpus("corpus.commit", "corpus_commit", manifest.corpus_commit),
        _corpus("corpus.commit_time", "corpus_commit_time", manifest.corpus_commit_time),
        _corpus("corpus.engine_version", "engine_version", manifest.engine_version),
        _corpus("corpus.manifest_sha256", "sha256", artifacts.manifest_sha256),
    )


def _code_facts(artifacts: BuildArtifacts) -> tuple[FactSource, ...]:
    artifact = artifacts.descriptor_artifact
    descriptor = artifacts.descriptor
    return (
        FactSource(
            id="code.tool_surface.tool_count",
            kind="code",
            artifact=artifact,
            field="tool_count",
            value=descriptor.tool_count,
        ),
        FactSource(
            id="code.tool_surface.sha256",
            kind="code",
            artifact=artifact,
            field="schema_sha256",
            value=descriptor.schema_sha256,
        ),
        FactSource(
            id="code.lovspor_commit",
            kind="code",
            artifact=artifact,
            field="lovspor_commit",
            value=artifacts.lovspor_commit,
        ),
    )


def _hosted(
    fact_id: str, field: str, value: FactValue | None, absent: Unobserved | None = None
) -> FactSource:
    """A hosted fact: the record's value, or its absence with reason and time."""
    if value is None:
        return FactSource(
            id=fact_id,
            kind="hosted",
            artifact=CAPABILITIES_ARTIFACT,
            field=field,
            unobserved=absent,
        )
    return FactSource(
        id=fact_id, kind="hosted", artifact=CAPABILITIES_ARTIFACT, field=field, value=value
    )


def _process_absence(record: ProcessRecord) -> Unobserved:
    """Why a process value is missing: the record's reason, or not-ready, or not reported."""
    if record.reason is not None:
        reason = record.reason
    elif record.ready is False:
        reason = "not_ready"
    else:
        reason = "not_reported"
    return Unobserved(reason=reason, observed_at=record.observed_at)


def _served_absence(record: TransportRecord) -> Unobserved:
    """Why a served value is missing: transport, then step, then the step's outcome."""
    step: AuthenticatedStep = record.authenticated
    reason = record.reason or step.reason or step.outcome or "not_reported"
    return Unobserved(reason=reason, observed_at=record.observed_at)


def _state_facts(document: CapabilityDocument) -> tuple[FactSource, ...]:
    """The derived state is never absent: every comparison and the hosted state have a value."""
    comparisons = document.state.comparisons
    return (
        _hosted("hosted.state", "state.hosted_state", document.state.hosted_state),
        *(
            _hosted(
                f"hosted.comparisons.{name}",
                f"state.comparisons.{name}",
                getattr(comparisons, name),
            )
            for name in COMPARISONS
        ),
    )


def _process_facts(record: ProcessRecord) -> tuple[FactSource, ...]:
    """Status and time are always recorded; ``ready`` and the count follow the record."""
    absent = _process_absence(record)
    prefix = "observation.process"
    return (
        _hosted("hosted.process.status", f"{prefix}.status", record.status),
        _hosted("hosted.process.observed_at", f"{prefix}.observed_at", record.observed_at),
        _hosted("hosted.process.ready", f"{prefix}.ready", record.ready, absent),
        _hosted("hosted.process.tool_count", f"{prefix}.tool_count", record.tool_count, absent),
    )


def _transport_facts(record: TransportRecord) -> tuple[FactSource, ...]:
    """Statuses, time and verdict are always recorded; the served count follows the step."""
    prefix = "observation.transport"
    return (
        _hosted("hosted.transport.status", f"{prefix}.status", record.status),
        _hosted("hosted.transport.observed_at", f"{prefix}.observed_at", record.observed_at),
        _hosted(
            "hosted.transport.authenticated.status",
            f"{prefix}.authenticated.status",
            record.authenticated.status,
        ),
        _hosted(
            "hosted.transport.authenticated.served_tool_count",
            f"{prefix}.authenticated.served_tool_count",
            record.authenticated.served_tool_count,
            _served_absence(record),
        ),
        _hosted(
            "hosted.transport.oauth_discovery.verdict",
            f"{prefix}.oauth_discovery.verdict",
            record.oauth_discovery.verdict,
        ),
    )


def fact_registry(artifacts: BuildArtifacts) -> FactRegistry:
    """Every fact this build may render, each bound to its artifact and kind."""
    observation = artifacts.document.observation
    return FactRegistry(
        sources=(
            *_corpus_facts(artifacts),
            *_code_facts(artifacts),
            *_state_facts(artifacts.document),
            *_process_facts(observation.process),
            *_transport_facts(observation.transport),
        )
    )
