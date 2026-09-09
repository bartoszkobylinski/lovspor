"""The capability-document fixture generator (ADR-0014 Decision 4, plan B)."""

import ast
import json
from pathlib import Path

import pytest

import lovspor.site.fixture as fixture_module
from lovspor.publish.emit import emit_site
from lovspor.runtime_identity import installed_environment_sha256, interpreter, tree_sha256
from lovspor.site.build import SiteInputs, build_site
from lovspor.site.capabilities import (
    CapabilityDocument,
    Checkout,
    Observation,
    derive_state,
    parse_capabilities,
)
from lovspor.site.errors import SiteBuildError
from lovspor.site.fixture import (
    FIXED_OBSERVED_AT,
    FixtureCase,
    document_bytes,
    expected_checkout,
    synthetic_document,
)
from lovspor.tool_surface import describe_tool_surface
from tests.unit.site_fixtures import run_git, throwaway_checkout, throwaway_corpus

_REPO = Path(__file__).resolve().parents[2]

# case -> (hosted_state, the comparisons that must read "false" or "unknown")
INTENT: dict[FixtureCase, tuple[str, dict[str, str]]] = {
    FixtureCase.available: ("available", {"oauth_discovery_consistent": "false"}),
    FixtureCase.process_not_ready: (
        "unavailable",
        {
            "runtime_tree_match": "unknown",
            "environment_match": "unknown",
            "tool_surface_match": "unknown",
            "transport_surface_match": "unknown",
            "oauth_discovery_consistent": "unknown",
        },
    ),
    FixtureCase.process_unobserved: (
        "unknown",
        {
            "runtime_tree_match": "unknown",
            "environment_match": "unknown",
            "tool_surface_match": "unknown",
            "transport_surface_match": "unknown",
            "oauth_discovery_consistent": "unknown",
        },
    ),
    FixtureCase.transport_unobserved: (
        "unknown",
        {"transport_surface_match": "unknown", "oauth_discovery_consistent": "unknown"},
    ),
    FixtureCase.transport_misdirected: (
        "unavailable",
        {"transport_surface_match": "unknown", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.credential_rejected: (
        "unknown",
        {"transport_surface_match": "unknown", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.credential_missing: (
        "unknown",
        {"transport_surface_match": "unknown", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.served_surface_differs: (
        "unavailable",
        {"transport_surface_match": "false", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.tree_differs: (
        "available",
        {"runtime_tree_match": "false", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.environment_differs: (
        "available",
        {"environment_match": "false", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.surface_differs: (
        "available",
        {"tool_surface_match": "false", "oauth_discovery_consistent": "false"},
    ),
    FixtureCase.oauth_discovery_valid: ("available", {}),
}


@pytest.fixture(scope="module")
def repos(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, Path]:
    root = tmp_path_factory.mktemp("fixture")
    checkout, commit = throwaway_checkout(root / "lovspor")
    corpus, _ = throwaway_corpus(root / "lovverk")
    return checkout, commit, corpus


class TestExpectedCheckout:
    def test_is_read_from_the_real_head_tree_environment_and_descriptor(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, commit, corpus = repos

        expected = expected_checkout(checkout, corpus)

        assert expected.lovspor_commit == commit
        assert expected.expected_tool_surface_sha256 == describe_tool_surface(corpus).schema_sha256
        assert expected.expected_runtime_identity.tree_sha256 == tree_sha256(
            checkout / "src" / "lovspor"
        )
        assert expected.expected_runtime_identity.environment_sha256 == (
            installed_environment_sha256()
        )
        assert expected.expected_runtime_identity.interpreter == interpreter().label

    def test_a_dirty_checkout_is_refused(
        self, tmp_path: Path, repos: tuple[Path, str, Path]
    ) -> None:
        """A fixture naming HEAD while the tree differs from it would be the
        fabricated provenance the ledger exists to exclude."""
        _, _, corpus = repos
        checkout, _ = throwaway_checkout(tmp_path / "dirty")
        (checkout / "src" / "lovspor" / "extra.py").write_text("x = 1\n", encoding="utf-8")

        with pytest.raises(SiteBuildError, match="dirty"):
            expected_checkout(checkout, corpus)


class TestSyntheticDocument:
    def test_every_case_is_a_valid_document_whose_state_is_its_own_derivation(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, commit, corpus = repos
        for case in FixtureCase:
            document = synthetic_document(checkout, corpus, case)

            assert isinstance(document, CapabilityDocument)
            assert document.schema_version == "1"
            assert document.state.checkout.lovspor_commit == commit
            assert document.state == derive_state(document.observation, document.state.checkout)
            assert parse_capabilities(document_bytes(document)) == document

    @pytest.mark.parametrize("case", list(FixtureCase))
    def test_intent_matches_the_derived_state(
        self, repos: tuple[Path, str, Path], case: FixtureCase
    ) -> None:
        checkout, _, corpus = repos
        hosted_state, comparisons = INTENT[case]

        document = synthetic_document(checkout, corpus, case)

        assert document.state.hosted_state == hosted_state, case
        recorded = document.state.comparisons.model_dump()
        for name, value in comparisons.items():
            assert recorded[name] == value, (case, name)
        for name, value in recorded.items():
            if name not in comparisons:
                assert value == "true", (case, name)

    def test_the_available_case_meets_every_clause(self, repos: tuple[Path, str, Path]) -> None:
        checkout, _, corpus = repos
        document = synthetic_document(checkout, corpus, FixtureCase.available)
        process, transport = document.observation.process, document.observation.transport

        assert process.status == "observed" and process.ready is True
        assert transport.unauthenticated is not None
        assert transport.unauthenticated.status_code == 401
        assert transport.authenticated.outcome == "ok"
        assert transport.authenticated.served_tool_count == process.tool_count
        assert process.tool_count == describe_tool_surface(corpus).tool_count
        assert process.credential_modes == ("token",)
        assert process.oauth_configured is False
        assert transport.oauth_discovery.verdict == "absent"

    def test_the_oauth_case_is_the_available_case_with_discovery_and_the_pair(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, _, corpus = repos
        document = synthetic_document(checkout, corpus, FixtureCase.oauth_discovery_valid)
        process, transport = document.observation.process, document.observation.transport

        assert process.credential_modes == ("token", "oauth")
        assert process.oauth_configured is True
        assert transport.oauth_discovery.verdict == "valid"
        assert transport.oauth_discovery.document_sha256 is not None
        assert document.state.comparisons.oauth_discovery_consistent == "true"

    def test_observer_failures_are_unobserved_with_their_reason(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, _, corpus = repos
        for case, reason in (
            (FixtureCase.credential_rejected, "probe_credential_rejected"),
            (FixtureCase.credential_missing, "probe_credential_missing"),
        ):
            step = synthetic_document(checkout, corpus, case).observation.transport.authenticated

            assert step.status == "unobserved"
            assert step.reason == reason
            assert step.served_tool_count is None

    def test_the_process_not_ready_case_carries_no_attestation(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, _, corpus = repos
        process = synthetic_document(
            checkout, corpus, FixtureCase.process_not_ready
        ).observation.process

        assert process.status == "observed"
        assert process.ready is False
        assert process.runtime_identity is None
        assert process.tool_count is None

    def test_observed_at_is_the_fixed_constant_unless_given(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, _, corpus = repos
        default = synthetic_document(checkout, corpus, FixtureCase.available)
        custom = synthetic_document(
            checkout, corpus, FixtureCase.available, observed_at="2026-03-04T05:06:07Z"
        )

        assert FIXED_OBSERVED_AT == "2026-01-01T00:00:00Z"
        assert default.observation.process.observed_at == FIXED_OBSERVED_AT
        assert default.observation.transport.observed_at == FIXED_OBSERVED_AT
        assert custom.observation.process.observed_at == "2026-03-04T05:06:07Z"
        assert custom.observation.transport.observed_at == "2026-03-04T05:06:07Z"
        assert custom.state == default.state
        assert default.observation.process.observer == "release-probe"

    def test_is_deterministic(self, repos: tuple[Path, str, Path]) -> None:
        checkout, _, corpus = repos
        for case in FixtureCase:
            one = document_bytes(synthetic_document(checkout, corpus, case))
            two = document_bytes(synthetic_document(checkout, corpus, case))

            assert one == two, case

    def test_bytes_are_companion_json(self, repos: tuple[Path, str, Path]) -> None:
        checkout, _, corpus = repos
        raw = document_bytes(synthetic_document(checkout, corpus, FixtureCase.available))
        payload = json.loads(raw)

        assert set(payload) == {"schema_version", "observation", "state"}
        assert raw == (
            json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
        ).encode("utf-8")

    def test_every_case_builds_a_site(self, repos: tuple[Path, str, Path], tmp_path: Path) -> None:
        """The build accepts each shape and publishes its derived state."""
        checkout, commit, corpus = repos
        corpus_site = tmp_path / "corpus-site"
        emit_site(corpus, run_git(corpus, "rev-parse", "HEAD"), corpus_site)
        for case in FixtureCase:
            capabilities = tmp_path / f"{case.value}.json"
            capabilities.write_bytes(document_bytes(synthetic_document(checkout, corpus, case)))

            report = build_site(
                SiteInputs(
                    checkout=checkout,
                    corpus=corpus,
                    corpus_manifest=corpus_site / "site-manifest.json",
                    capabilities=capabilities,
                    out=tmp_path / case.value,
                )
            )

            assert report.hosted_state == INTENT[case][0], case
            assert report.lovspor_commit == commit


class TestNoClock:
    def test_the_generator_imports_no_clock_and_reads_no_time(self) -> None:
        """observed_at is an explicit input, never the generator's clock (ADR:645-660)."""
        tree = ast.parse(Path(fixture_module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}

        assert not imported & {"datetime", "time"}
        assert not [name for name in imported if name.startswith(("datetime.", "time."))]

    def test_the_case_vocabulary_is_the_plan_s(self) -> None:
        assert [case.value for case in FixtureCase] == [
            "available",
            "process_not_ready",
            "process_unobserved",
            "transport_unobserved",
            "transport_misdirected",
            "credential_rejected",
            "credential_missing",
            "served_surface_differs",
            "tree_differs",
            "environment_differs",
            "surface_differs",
            "oauth_discovery_valid",
        ]


class TestCheckoutModel:
    def test_the_expected_checkout_is_the_document_s_checkout_part(
        self, repos: tuple[Path, str, Path]
    ) -> None:
        checkout, _, corpus = repos
        expected = expected_checkout(checkout, corpus)
        document = synthetic_document(checkout, corpus, FixtureCase.tree_differs)

        assert isinstance(expected, Checkout)
        assert document.state.checkout == expected
        assert isinstance(document.observation, Observation)
