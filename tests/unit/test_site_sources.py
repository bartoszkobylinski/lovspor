"""The fact sources of one build: the registry, entry by entry (ADR-0014 Decision 4).

``fact_registry`` decides every fact's id, kind, artifact and field. The
build tests prove the ledger against the artifacts, but they read a shared
build; here the registry is asserted directly, on artifacts made in the
test, so that a renamed id or field fails without a build in between.
"""

import hashlib
import json
from typing import Any

from lovspor.site.capabilities import CapabilityDocument
from lovspor.site.facts import FactSource, Unobserved
from lovspor.site.sources import BuildArtifacts, CorpusManifest, fact_registry
from lovspor.tool_surface import ToolSurfaceDescriptor
from tests.unit.site_fixtures import (
    COMMIT,
    OBSERVED_AT,
    SURFACE,
    available_observation,
    capability_document,
    readyz_503,
    unobserved_authenticated,
    unobserved_process,
    unobserved_transport,
)

CAPABILITIES = "deployment-capabilities.json"
MANIFEST = "corpus/site-manifest.json"
DESCRIPTOR = f"tool-surface@{COMMIT}"
CORPUS_COMMIT = "b" * 40
CORPUS_COMMIT_TIME = "2026-02-01T00:00:00+00:00"
MANIFEST_PAYLOAD = {
    "corpus_commit": CORPUS_COMMIT,
    "corpus_commit_time": CORPUS_COMMIT_TIME,
    "engine_version": "0.9.0",
    "documents": 5900,
}


def _artifacts(observation: dict[str, Any] | None = None) -> BuildArtifacts:
    manifest_bytes = json.dumps(MANIFEST_PAYLOAD).encode("utf-8")
    document = capability_document(observation)
    return BuildArtifacts(
        lovspor_commit=COMMIT,
        manifest=CorpusManifest.model_validate_json(manifest_bytes),
        manifest_bytes=manifest_bytes,
        document=CapabilityDocument.model_validate(document),
        capability_bytes=json.dumps(document).encode("utf-8"),
        descriptor=ToolSurfaceDescriptor(
            names=("get_law", "get_section", "search_laws"), schema_sha256=SURFACE, tool_count=3
        ),
    )


def _corpus(fact_id: str, field: str, value: str | int) -> FactSource:
    return FactSource(id=fact_id, kind="corpus", artifact=MANIFEST, field=field, value=value)


def _code(fact_id: str, field: str, value: str | int) -> FactSource:
    return FactSource(id=fact_id, kind="code", artifact=DESCRIPTOR, field=field, value=value)


def _hosted(fact_id: str, field: str, value: str | int | bool) -> FactSource:
    return FactSource(id=fact_id, kind="hosted", artifact=CAPABILITIES, field=field, value=value)


def _by_id(artifacts: BuildArtifacts) -> dict[str, FactSource]:
    return {source.id: source for source in fact_registry(artifacts).sources}


class TestFactRegistry:
    def test_lists_every_source_by_id_kind_artifact_field_and_value(self) -> None:
        artifacts = _artifacts()
        manifest_sha256 = hashlib.sha256(artifacts.manifest_bytes).hexdigest()

        assert fact_registry(artifacts).sources == (
            _corpus("corpus.documents", "documents", 5900),
            _corpus("corpus.commit", "corpus_commit", CORPUS_COMMIT),
            _corpus("corpus.commit_time", "corpus_commit_time", CORPUS_COMMIT_TIME),
            _corpus("corpus.engine_version", "engine_version", "0.9.0"),
            _corpus("corpus.manifest_sha256", "sha256", manifest_sha256),
            _code("code.tool_surface.tool_count", "tool_count", 3),
            _code("code.tool_surface.sha256", "schema_sha256", SURFACE),
            _code("code.lovspor_commit", "lovspor_commit", COMMIT),
            _hosted("hosted.state", "state.hosted_state", "available"),
            _hosted(
                "hosted.comparisons.runtime_tree_match",
                "state.comparisons.runtime_tree_match",
                "true",
            ),
            _hosted(
                "hosted.comparisons.environment_match",
                "state.comparisons.environment_match",
                "true",
            ),
            _hosted(
                "hosted.comparisons.tool_surface_match",
                "state.comparisons.tool_surface_match",
                "true",
            ),
            _hosted(
                "hosted.comparisons.transport_surface_match",
                "state.comparisons.transport_surface_match",
                "true",
            ),
            _hosted(
                "hosted.comparisons.oauth_discovery_consistent",
                "state.comparisons.oauth_discovery_consistent",
                "true",
            ),
            _hosted("hosted.process.status", "observation.process.status", "observed"),
            _hosted("hosted.process.observed_at", "observation.process.observed_at", OBSERVED_AT),
            _hosted("hosted.process.ready", "observation.process.ready", True),
            _hosted("hosted.process.tool_count", "observation.process.tool_count", 17),
            _hosted("hosted.transport.status", "observation.transport.status", "observed"),
            _hosted(
                "hosted.transport.observed_at", "observation.transport.observed_at", OBSERVED_AT
            ),
            _hosted(
                "hosted.transport.authenticated.status",
                "observation.transport.authenticated.status",
                "observed",
            ),
            _hosted(
                "hosted.transport.authenticated.served_tool_count",
                "observation.transport.authenticated.served_tool_count",
                17,
            ),
            _hosted(
                "hosted.transport.oauth_discovery.verdict",
                "observation.transport.oauth_discovery.verdict",
                "valid",
            ),
        )

    def test_every_field_reads_back_from_its_artifact(self) -> None:
        """A field is a path into the artifact it names; the value recorded
        is what that path holds (ADR:2412-2420)."""
        artifacts = _artifacts()
        document = json.loads(artifacts.capability_bytes)

        for source in fact_registry(artifacts).sources:
            if source.artifact == MANIFEST:
                expected = (
                    hashlib.sha256(artifacts.manifest_bytes).hexdigest()
                    if source.field == "sha256"
                    else MANIFEST_PAYLOAD[source.field]
                )
            elif source.artifact == CAPABILITIES:
                node: Any = document
                for part in source.field.split("."):
                    node = node[part]
                expected = node
            else:
                expected = (
                    COMMIT
                    if source.field == "lovspor_commit"
                    else getattr(artifacts.descriptor, source.field)
                )
            assert source.value == expected, source.id


class TestAbsence:
    """A hosted value the record does not carry degrades with the record's reason."""

    def test_a_process_value_the_record_did_not_report(self) -> None:
        observation = available_observation()
        observation["process"]["tool_count"] = None

        source = _by_id(_artifacts(observation))["hosted.process.tool_count"]

        assert source.value is None
        assert source.unobserved == Unobserved(reason="not_reported", observed_at=OBSERVED_AT)

    def test_a_process_that_answered_not_ready(self) -> None:
        sources = _by_id(_artifacts(readyz_503()))

        assert sources["hosted.process.tool_count"].unobserved == Unobserved(
            reason="not_ready", observed_at=OBSERVED_AT
        )
        assert sources["hosted.process.ready"].value is False

    def test_an_unobserved_process(self) -> None:
        sources = _by_id(_artifacts(unobserved_process("timeout")))

        assert sources["hosted.process.tool_count"].unobserved == Unobserved(
            reason="timeout", observed_at=OBSERVED_AT
        )
        assert sources["hosted.process.ready"].unobserved == Unobserved(
            reason="timeout", observed_at=OBSERVED_AT
        )

    def test_a_step_whose_outcome_listed_nothing(self) -> None:
        observation = available_observation()
        observation["transport"]["authenticated"].update(
            outcome="protocol_error", served_tool_surface_sha256=None, served_tool_count=None
        )

        source = _by_id(_artifacts(observation))["hosted.transport.authenticated.served_tool_count"]

        assert source.unobserved == Unobserved(reason="protocol_error", observed_at=OBSERVED_AT)

    def test_an_unobserved_step(self) -> None:
        sources = _by_id(_artifacts(unobserved_authenticated("probe_credential_rejected")))

        assert sources["hosted.transport.authenticated.served_tool_count"].unobserved == Unobserved(
            reason="probe_credential_rejected", observed_at=OBSERVED_AT
        )
        assert sources["hosted.transport.authenticated.status"].value == "unobserved"

    def test_an_unobserved_transport(self) -> None:
        sources = _by_id(_artifacts(unobserved_transport("network")))

        assert sources["hosted.transport.authenticated.served_tool_count"].unobserved == Unobserved(
            reason="network", observed_at=OBSERVED_AT
        )
        assert sources["hosted.transport.status"].value == "unobserved"
        assert sources["hosted.transport.oauth_discovery.verdict"].value == "unobserved"
