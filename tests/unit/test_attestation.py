"""The readiness attestation: what the hosted process runs (ADR-0014 Decision 4)."""

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError

import lovspor
from lovspor.access import write_credential_file
from lovspor.attestation import (
    ProcessAttestation,
    RuntimeIdentity,
    compute_attestation,
    corpus_present,
    credential_modes_for,
)
from lovspor.mcp import HttpConfig, build_server
from lovspor.runtime_identity import installed_environment_sha256, interpreter, tree_sha256
from lovspor.site.capabilities import (
    Checkout,
    Observation,
    ProcessRecord,
    TransportRecord,
    derive_state,
)
from lovspor.tool_surface import describe_tool_surface
from tests.unit.llhb_fixtures import build_corpus
from tests.unit.site_fixtures import COMMIT, OBSERVED_AT, unobserved_transport

PACKAGE_DIR = Path(lovspor.__file__).resolve().parent
CORPUS_DOCS = {"testloven": ("Testloven", "### § 1. Formål\n\nLoven gjelder.\n")}
ATTESTATION_FIELDS = {
    "schema_version",
    "ready",
    "runtime_identity",
    "tool_surface_sha256",
    "tool_count",
    "credential_modes",
    "oauth_configured",
}
IDENTITY_FIELDS = {"tree_sha256", "environment_sha256", "interpreter"}
AUTHKIT_DOMAIN = "https://vigilant-beacon-78-staging.authkit.app"
PUBLIC_URL = "https://lovspor.bartoszkobylinski.com/mcp"
STARTUP_BUDGET_SECONDS = 5.0


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    build_corpus(tmp_path / "corpus", CORPUS_DOCS)
    return tmp_path / "corpus"


@pytest.fixture
def hosted(corpus: Path) -> FastMCP:
    return build_server(corpus, http=HttpConfig(allow_insecure=True))


def _same(server: FastMCP) -> Callable[[Path], FastMCP]:
    """The seam ``describe_tool_surface`` offers: hash this very instance."""
    return lambda _: server


def _attest(corpus: Path, server: FastMCP, *, oauth_configured: bool = False) -> ProcessAttestation:
    return compute_attestation(
        corpus, oauth_configured=oauth_configured, server_factory=_same(server)
    )


def _package(root: Path) -> Path:
    package = root / "lovspor"
    (package / "site").mkdir(parents=True)
    (package / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (package / "site" / "page.html").write_text("<html>", encoding="utf-8")
    return package


def _as_record(payload: dict[str, object]) -> ProcessRecord:
    """Capture a served payload the way the release probe will: verbatim, no renaming."""
    attested = {name: value for name, value in payload.items() if name != "schema_version"}
    return ProcessRecord.model_validate(
        {
            "status": "observed",
            "reason": None,
            "observed_at": OBSERVED_AT,
            "observer": "release-probe",
            **attested,
        }
    )


class TestSchema:
    def test_carries_exactly_the_fields_of_decision_4(self, corpus: Path, hosted: FastMCP) -> None:
        payload = _attest(corpus, hosted).model_dump(mode="json")

        assert set(payload) == ATTESTATION_FIELDS
        assert set(payload["runtime_identity"]) == IDENTITY_FIELDS
        assert payload["schema_version"] == "1"

    def test_is_frozen(self, corpus: Path, hosted: FastMCP) -> None:
        attestation = _attest(corpus, hosted)

        with pytest.raises(ValidationError):
            attestation.ready = False  # type: ignore[misc]
        with pytest.raises(ValidationError):
            attestation.runtime_identity.interpreter = "other"  # type: ignore[misc]

    def test_schema_version_is_fixed(self) -> None:
        identity = RuntimeIdentity(
            tree_sha256="1" * 64, environment_sha256="2" * 64, interpreter="cpython 3.12.11"
        )

        with pytest.raises(ValidationError):
            ProcessAttestation.model_validate(
                {
                    "schema_version": "2",
                    "ready": True,
                    "runtime_identity": identity,
                    "tool_surface_sha256": "3" * 64,
                    "tool_count": 1,
                    "credential_modes": ("token",),
                    "oauth_configured": False,
                }
            )

    def test_no_clock_secret_url_or_hostname_reaches_the_payload(self, corpus: Path) -> None:
        """The route is unauthenticated: everything in it is public by construction."""
        creds = corpus.parent / "credentials.json"
        write_credential_file(creds, [])
        config = HttpConfig(
            credentials_path=creds, authkit_domain=AUTHKIT_DOMAIN, public_url=PUBLIC_URL
        )
        server = build_server(corpus, http=config)

        text = json.dumps(_attest(corpus, server, oauth_configured=True).model_dump(mode="json"))

        for secret in (AUTHKIT_DOMAIN, PUBLIC_URL, str(creds), str(corpus), "http", "://"):
            assert secret not in text
        assert not any(name.endswith("_at") for name in ATTESTATION_FIELDS)
        assert re.search(r"\d{4}-\d{2}-\d{2}", text) is None


class TestComputeAttestation:
    def test_ready_follows_the_corpus_manifest(self, corpus: Path, hosted: FastMCP) -> None:
        assert corpus_present(corpus) is True
        assert _attest(corpus, hosted).ready is True

        (corpus / "manifest.json").unlink()

        assert corpus_present(corpus) is False
        assert _attest(corpus, hosted).ready is False

    def test_manifest_path_must_be_a_file(self, corpus: Path) -> None:
        manifest = corpus / "manifest.json"
        manifest.unlink()
        manifest.mkdir()

        assert corpus_present(corpus) is False

    def test_runtime_identity_is_what_the_site_build_computes(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        """One canonical form on both sides of the comparison (ADR-0014 Decision 4)."""
        identity = _attest(corpus, hosted).runtime_identity

        assert identity.tree_sha256 == tree_sha256(PACKAGE_DIR)
        assert identity.environment_sha256 == installed_environment_sha256()
        assert identity.interpreter == interpreter().label

    def test_package_dir_defaults_to_the_installed_package(
        self, corpus: Path, hosted: FastMCP, tmp_path: Path
    ) -> None:
        package = _package(tmp_path / "elsewhere")

        explicit = compute_attestation(
            corpus, oauth_configured=False, package_dir=package, server_factory=_same(hosted)
        )
        default = _attest(corpus, hosted)

        assert explicit.runtime_identity.tree_sha256 == tree_sha256(package)
        assert default.runtime_identity.tree_sha256 == tree_sha256(PACKAGE_DIR)
        assert explicit.runtime_identity.tree_sha256 != default.runtime_identity.tree_sha256

    def test_tool_surface_is_the_descriptor_of_the_same_server(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        attestation = _attest(corpus, hosted)
        descriptor = describe_tool_surface(corpus, server_factory=_same(hosted))

        assert attestation.tool_surface_sha256 == descriptor.schema_sha256
        assert attestation.tool_count == descriptor.tool_count
        assert attestation.tool_count > 0

    def test_hosted_surface_hashes_as_the_checkout_descriptor(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        """``tool_surface_match`` can only ever be true if the hosted wrappers
        (thread offload, quota) leave the client-facing surface untouched."""
        attestation = _attest(corpus, hosted)
        checkout = describe_tool_surface(corpus)

        assert attestation.tool_surface_sha256 == checkout.schema_sha256
        assert attestation.tool_count == checkout.tool_count

    def test_without_a_factory_the_surface_is_the_descriptors_default(self, corpus: Path) -> None:
        attestation = compute_attestation(corpus, oauth_configured=False)

        assert attestation.tool_surface_sha256 == describe_tool_surface(corpus).schema_sha256

    @pytest.mark.parametrize(
        ("oauth_configured", "modes"),
        [(False, ("token",)), (True, ("token", "oauth"))],
    )
    def test_credential_modes_follow_the_authkit_flag(
        self,
        corpus: Path,
        hosted: FastMCP,
        oauth_configured: bool,
        modes: tuple[str, ...],
    ) -> None:
        attestation = _attest(corpus, hosted, oauth_configured=oauth_configured)

        assert credential_modes_for(oauth_configured) == modes
        assert attestation.credential_modes == modes
        assert attestation.oauth_configured is oauth_configured

    def test_is_deterministic_within_a_process(self, corpus: Path, hosted: FastMCP) -> None:
        first = _attest(corpus, hosted)
        second = _attest(corpus, hosted)

        assert first == second
        assert first.model_dump_json() == second.model_dump_json()

    def test_runs_within_the_startup_budget(self, corpus: Path, hosted: FastMCP) -> None:
        started = time.perf_counter()
        _attest(corpus, hosted)
        elapsed = time.perf_counter() - started

        assert elapsed < STARTUP_BUDGET_SECONDS, f"compute_attestation took {elapsed:.3f}s"


class TestPayload:
    def test_readiness_is_overlaid_and_nothing_else_moves(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        attestation = _attest(corpus, hosted)

        served = attestation.payload(ready=False)

        assert served["ready"] is False
        assert {k: v for k, v in served.items() if k != "ready"} == {
            k: v for k, v in attestation.model_dump(mode="json").items() if k != "ready"
        }
        assert attestation.payload(ready=True) == attestation.model_dump(mode="json")


class TestCapturable:
    def test_round_trips_into_the_process_record_without_renaming(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        attestation = _attest(corpus, hosted, oauth_configured=True)

        record = _as_record(attestation.model_dump(mode="json"))

        assert record.ready is attestation.ready
        assert record.runtime_identity is not None
        assert record.runtime_identity.model_dump() == attestation.runtime_identity.model_dump()
        assert record.tool_surface_sha256 == attestation.tool_surface_sha256
        assert record.tool_count == attestation.tool_count
        assert record.credential_modes == attestation.credential_modes
        assert record.oauth_configured is attestation.oauth_configured

    def test_a_not_ready_payload_is_an_observed_process_that_is_not_ready(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        record = _as_record(_attest(corpus, hosted).payload(ready=False))

        assert record.status == "observed"
        assert record.ready is False
        assert record.runtime_identity is not None

    def test_the_site_build_compares_it_true_against_its_own_checkout(
        self, corpus: Path, hosted: FastMCP
    ) -> None:
        """The build side and the process side compute one canonical form,
        so an unchanged checkout compares ``true`` on every code comparison."""
        attestation = _attest(corpus, hosted)
        observation = Observation(
            process=_as_record(attestation.model_dump(mode="json")),
            transport=TransportRecord.model_validate(unobserved_transport("timeout")["transport"]),
        )
        checkout = Checkout.model_validate(
            {
                "lovspor_commit": COMMIT,
                "expected_runtime_identity": attestation.runtime_identity.model_dump(),
                "expected_tool_surface_sha256": describe_tool_surface(corpus).schema_sha256,
            }
        )

        comparisons = derive_state(observation, checkout).comparisons

        assert comparisons.runtime_tree_match == "true"
        assert comparisons.environment_match == "true"
        assert comparisons.tool_surface_match == "true"
