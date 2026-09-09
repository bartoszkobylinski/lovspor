"""The capability document: closed schema and pure state derivation (ADR-0014 Decision 4)."""

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.errors import LovsporError
from lovspor.site.capabilities import (
    AuthenticatedStep,
    CapabilityDocument,
    CapabilityDocumentError,
    Checkout,
    Observation,
    ProcessRecord,
    TransportRecord,
    UnauthenticatedStep,
    derive_comparisons,
    derive_hosted_state,
    derive_state,
    load_capabilities,
    parse_capabilities,
    state_sha256,
)
from lovspor.site.errors import SiteBuildError
from tests.unit.site_fixtures import (
    DISCOVERY,
    OBSERVED_AT,
    SURFACE,
    available_observation,
    capability_document,
    checkout_expectations,
    readyz_503,
    unobserved_authenticated,
    unobserved_process,
    unobserved_transport,
)

_PROCESS_VALUES = (
    "ready",
    "runtime_identity",
    "tool_surface_sha256",
    "tool_count",
    "credential_modes",
    "oauth_configured",
)


def _derive(observation: dict[str, Any]) -> tuple[dict[str, str], str]:
    parsed = Observation.model_validate(observation)
    comparisons = derive_comparisons(parsed, Checkout.model_validate(checkout_expectations()))
    return comparisons.model_dump(mode="json"), derive_hosted_state(parsed, comparisons)


class TestSchema:
    def test_accepts_a_document_whose_state_is_its_own_derivation(self, tmp_path: Path) -> None:
        path = tmp_path / "deployment-capabilities.json"
        path.write_text(json.dumps(capability_document()), encoding="utf-8")

        document = load_capabilities(path)

        assert document.schema_version == "1"
        assert document.state.hosted_state == "available"
        assert document.observation.process.observed_at == OBSERVED_AT

    def test_accepts_every_unobserved_shape(self) -> None:
        for observation in (
            unobserved_process("timeout"),
            unobserved_transport("network"),
            unobserved_authenticated("probe_credential_rejected"),
            readyz_503(),
        ):
            CapabilityDocument.model_validate(capability_document(observation))

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda d: d.__setitem__("extra", 1), id="unknown top-level key"),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("host", "x"),
                id="unknown nested key",
            ),
            pytest.param(
                lambda d: d["state"]["comparisons"].__setitem__("extra", "true"),
                id="unknown comparison",
            ),
            pytest.param(
                lambda d: d["observation"]["transport"].pop("observer"), id="missing observer"
            ),
            pytest.param(
                lambda d: d["observation"]["process"].pop("observed_at"), id="missing observed_at"
            ),
            pytest.param(
                lambda d: d["state"]["comparisons"].__setitem__("runtime_tree_match", True),
                id="boolean comparison",
            ),
            pytest.param(
                lambda d: d["state"].__setitem__("hosted_state", "up"),
                id="hosted_state outside the vocabulary",
            ),
            pytest.param(
                lambda d: d.__setitem__("schema_version", "2"), id="unknown schema version"
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("reason", "timeout"),
                id="reason on an observed record",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__(
                    "observed_at", "2026-01-01 00:00:00"
                ),
                id="observed_at not RFC 3339 UTC",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__(
                    "observed_at", "2026-01-01T00:00:00+01:00"
                ),
                id="observed_at with an offset",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("observer", "me"),
                id="observer outside the vocabulary",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("tool_surface_sha256", "abc"),
                id="hash that is not sha256 hex",
            ),
            pytest.param(
                lambda d: d["state"]["checkout"].__setitem__("lovspor_commit", "main"),
                id="commit that is not a sha",
            ),
            pytest.param(
                lambda d: d["observation"]["transport"]["authenticated"].__setitem__(
                    "outcome", "fine"
                ),
                id="outcome outside the vocabulary",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("reason", "http_5xx"),
                id="reason http_ without a code",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__(
                    "credential_modes", ["oauth", "oauth"]
                ),
                id="duplicate credential modes",
            ),
            pytest.param(
                lambda d: d["observation"]["process"].__setitem__("ready", None),
                id="observed process without ready",
            ),
            pytest.param(
                lambda d: d["observation"]["transport"]["oauth_discovery"].__setitem__(
                    "invalid_reason", "malformed"
                ),
                id="invalid_reason on a valid verdict",
            ),
            pytest.param(
                lambda d: d["observation"]["transport"].__setitem__("unauthenticated", None),
                id="observed transport without step (a)",
            ),
        ],
    )
    def test_rejects_a_document_outside_the_closed_schema(self, mutate, tmp_path: Path) -> None:
        document = capability_document()
        mutate(document)
        path = tmp_path / "deployment-capabilities.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(CapabilityDocumentError):
            load_capabilities(path)

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(
                lambda o: o["process"].__setitem__("ready", True),
                id="ready on an unobserved process",
            ),
            pytest.param(
                lambda o: o["process"].__setitem__("reason", None),
                id="unobserved process without a reason",
            ),
            pytest.param(
                lambda o: o["process"].__setitem__("tool_count", 17),
                id="value on an unobserved process",
            ),
        ],
    )
    def test_an_unobserved_record_records_no_value(self, mutate) -> None:
        """The document never records a guess (ADR-0014 Decision 4)."""
        observation = unobserved_process("timeout")
        mutate(observation)

        with pytest.raises(CapabilityDocumentError):
            parse_capabilities(json.dumps(_document_unchecked(observation)).encode("utf-8"))

    def test_an_unobserved_step_records_no_value(self) -> None:
        observation = unobserved_authenticated("probe_credential_rejected")
        observation["transport"]["authenticated"]["served_tool_count"] = 17

        with pytest.raises(CapabilityDocumentError):
            parse_capabilities(json.dumps(_document_unchecked(observation)).encode("utf-8"))

    def test_rejects_a_state_that_is_not_the_function_of_its_observation(
        self, tmp_path: Path
    ) -> None:
        document = capability_document()
        document["state"]["hosted_state"] = "unavailable"
        path = tmp_path / "deployment-capabilities.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(CapabilityDocumentError, match="state"):
            load_capabilities(path)

    def test_rejects_a_state_whose_checkout_was_edited(self, tmp_path: Path) -> None:
        document = capability_document()
        document["state"]["checkout"]["expected_tool_surface_sha256"] = "5" * 64
        path = tmp_path / "deployment-capabilities.json"
        path.write_text(json.dumps(document), encoding="utf-8")

        with pytest.raises(CapabilityDocumentError, match="state"):
            load_capabilities(path)

    def test_absent_or_malformed_file_is_the_same_error_family(self, tmp_path: Path) -> None:
        with pytest.raises(CapabilityDocumentError):
            load_capabilities(tmp_path / "missing.json")
        broken = tmp_path / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with pytest.raises(CapabilityDocumentError):
            load_capabilities(broken)

    def test_the_error_is_a_site_build_error(self) -> None:
        assert issubclass(CapabilityDocumentError, SiteBuildError)
        assert issubclass(SiteBuildError, LovsporError)

    def test_models_are_frozen(self) -> None:
        document = CapabilityDocument.model_validate(capability_document())

        with pytest.raises(ValidationError):
            document.state.hosted_state = "unknown"  # type: ignore[misc]

    @pytest.mark.parametrize(
        ("record", "message"),
        [
            pytest.param(
                {"status": "unobserved", "reason": None},
                "an unobserved record names its reason",
                id="unobserved process without a reason",
            ),
            pytest.param(
                {"status": "observed", "reason": "timeout", "ready": True},
                "an observed record carries no reason",
                id="observed process with a reason",
            ),
            pytest.param(
                {"status": "unobserved", "reason": "timeout", "ready": True, "tool_count": 17},
                "an unobserved process records no value: ready, tool_count",
                id="unobserved process with values",
            ),
            pytest.param(
                {"status": "observed", "reason": None, "ready": None},
                "an observed process says whether it is ready: ready",
                id="observed process without ready",
            ),
        ],
    )
    def test_a_process_record_names_the_offending_fields(
        self, record: dict[str, Any], message: str
    ) -> None:
        """The record validators are tested on the record itself: through the
        document, the derivation rule rejects the same shapes first and would
        hide a validator that stopped saying why (ADR:737-743)."""
        absent = dict.fromkeys(_PROCESS_VALUES)

        with pytest.raises(ValidationError, match=re.escape(message)):
            ProcessRecord.model_validate(
                {**absent, "observed_at": OBSERVED_AT, "observer": "release-probe", **record}
            )

    @pytest.mark.parametrize(
        ("step", "message"),
        [
            pytest.param(
                {"status": "observed", "reason": None, "outcome": "ok"},
                "a listed surface records what it listed: "
                "served_tool_surface_sha256, served_tool_count",
                id="ok without the listed surface",
            ),
            pytest.param(
                {"status": "observed", "reason": None, "outcome": None},
                "an observed step records its outcome: outcome",
                id="observed without an outcome",
            ),
            pytest.param(
                {"status": "unobserved", "reason": None, "outcome": None},
                "an unobserved record names its reason",
                id="unobserved step without a reason",
            ),
            pytest.param(
                {"status": "observed", "reason": "timeout", "outcome": "protocol_error"},
                "an observed record carries no reason",
                id="observed step with a reason",
            ),
            pytest.param(
                {
                    "status": "unobserved",
                    "reason": "timeout",
                    "outcome": "ok",
                    "served_tool_surface_sha256": SURFACE,
                    "served_tool_count": 17,
                },
                "an unobserved step records no value: "
                "outcome, served_tool_surface_sha256, served_tool_count",
                id="unobserved step with values",
            ),
            pytest.param(
                {
                    "status": "observed",
                    "reason": None,
                    "outcome": "protocol_error",
                    "served_tool_surface_sha256": SURFACE,
                    "served_tool_count": 17,
                },
                "no surface was listed: served_tool_surface_sha256, served_tool_count",
                id="failed step with a listed surface",
            ),
        ],
    )
    def test_an_authenticated_step_names_the_offending_fields(
        self, step: dict[str, Any], message: str
    ) -> None:
        absent = {"served_tool_surface_sha256": None, "served_tool_count": None}

        with pytest.raises(ValidationError, match=re.escape(message)):
            AuthenticatedStep.model_validate({**absent, **step})

    def test_a_transport_record_names_the_offending_fields(self) -> None:
        observed = available_observation()["transport"]
        unobserved = unobserved_transport("network")["transport"]

        with pytest.raises(
            ValidationError, match=re.escape("an unobserved record names its reason")
        ):
            TransportRecord.model_validate({**unobserved, "reason": None})
        with pytest.raises(
            ValidationError, match=re.escape("an observed record carries no reason")
        ):
            TransportRecord.model_validate({**observed, "reason": "network"})
        with pytest.raises(
            ValidationError,
            match=re.escape("an observed transport records step (a): unauthenticated"),
        ):
            TransportRecord.model_validate({**observed, "unauthenticated": None})
        with pytest.raises(
            ValidationError,
            match=re.escape("an unobserved transport records no value: unauthenticated"),
        ):
            TransportRecord.model_validate(
                {**unobserved, "unauthenticated": observed["unauthenticated"]}
            )
        with pytest.raises(
            ValidationError,
            match=re.escape("an unobserved transport observed neither step (b) nor discovery"),
        ):
            TransportRecord.model_validate(
                {**unobserved, "oauth_discovery": observed["oauth_discovery"]}
            )


def _document_unchecked(observation: dict[str, Any]) -> dict[str, Any]:
    """A document body around an observation the schema may refuse."""
    state = capability_document()["state"]
    return {"schema_version": "1", "observation": observation, "state": state}


class TestHostedState:
    def test_every_clause_met_is_available(self) -> None:
        comparisons, hosted = _derive(available_observation())

        assert hosted == "available"
        assert comparisons == dict.fromkeys(comparisons, "true")

    def test_loopback_only_is_never_available(self) -> None:
        """A healthy /readyz beside an unreachable public host is unknown."""
        comparisons, hosted = _derive(unobserved_transport("network"))

        assert hosted == "unknown"
        assert comparisons["transport_surface_match"] == "unknown"
        assert comparisons["oauth_discovery_consistent"] == "unknown"
        assert comparisons["runtime_tree_match"] == "true"
        assert comparisons["environment_match"] == "true"
        assert comparisons["tool_surface_match"] == "true"

    @pytest.mark.parametrize(
        "reason", ["timeout", "network", "tool_missing", "schema_invalid", "http_500"]
    )
    def test_an_unobserved_process_is_unknown_across_the_board(self, reason: str) -> None:
        comparisons, hosted = _derive(unobserved_process(reason))

        assert hosted == "unknown"
        assert comparisons == dict.fromkeys(comparisons, "unknown")

    def test_a_readyz_503_is_unavailable_with_the_checkout_comparisons_unknown(self) -> None:
        comparisons, hosted = _derive(readyz_503())

        assert hosted == "unavailable"
        assert comparisons["runtime_tree_match"] == "unknown"
        assert comparisons["environment_match"] == "unknown"
        assert comparisons["tool_surface_match"] == "unknown"
        assert comparisons["transport_surface_match"] == "unknown"
        assert comparisons["oauth_discovery_consistent"] == "unknown"

    @pytest.mark.parametrize(
        ("status_code", "challenge"),
        [
            (421, None),
            (404, None),
            (502, None),
            (200, None),
            (401, None),
            (401, "Basic realm=x"),
            (403, "Bearer"),
        ],
    )
    def test_step_a_not_a_bearer_401_is_unavailable(
        self, status_code: int, challenge: str | None
    ) -> None:
        observation = available_observation()
        observation["transport"]["unauthenticated"] = {
            "status_code": status_code,
            "challenge": challenge,
        }
        observation["transport"]["authenticated"] = unobserved_authenticated("not_attempted")[
            "transport"
        ]["authenticated"]

        _comparisons, hosted = _derive(observation)

        assert hosted == "unavailable"

    def test_the_bearer_scheme_is_matched_as_a_scheme(self) -> None:
        observation = available_observation()
        observation["transport"]["unauthenticated"]["challenge"] = 'bearer realm="lovspor"'
        assert _derive(observation)[1] == "available"
        observation["transport"]["unauthenticated"]["challenge"] = "Bearerish"
        assert _derive(observation)[1] == "unavailable"

    @pytest.mark.parametrize(
        "reason",
        [
            "probe_credential_rejected",
            "probe_credential_missing",
            "not_attempted",
            "network",
            "timeout",
        ],
    )
    def test_observer_failure_on_step_b_is_unknown_never_unavailable(self, reason: str) -> None:
        comparisons, hosted = _derive(unobserved_authenticated(reason))

        assert hosted == "unknown"
        assert comparisons["transport_surface_match"] == "unknown"

    @pytest.mark.parametrize("outcome", ["http_500", "http_421", "http_403", "protocol_error"])
    def test_a_failed_step_b_is_unavailable(self, outcome: str) -> None:
        observation = available_observation()
        observation["transport"]["authenticated"].update(
            outcome=outcome, served_tool_surface_sha256=None, served_tool_count=None
        )

        _comparisons, hosted = _derive(observation)

        assert hosted == "unavailable"

    def test_a_served_surface_that_differs_from_the_process_is_unavailable(self) -> None:
        observation = available_observation()
        observation["transport"]["authenticated"]["served_tool_surface_sha256"] = "5" * 64
        observation["transport"]["authenticated"]["served_tool_count"] = 16

        comparisons, hosted = _derive(observation)

        assert comparisons["transport_surface_match"] == "false"
        assert hosted == "unavailable"

    def test_ready_false_with_an_attestation_is_unavailable(self) -> None:
        observation = available_observation()
        observation["process"]["ready"] = False

        _comparisons, hosted = _derive(observation)

        assert hosted == "unavailable"


class TestComparisons:
    def test_a_code_change_moving_tree_and_surface_is_exactly_two_falses(self) -> None:
        observation = available_observation()
        observation["process"]["runtime_identity"]["tree_sha256"] = "5" * 64
        observation["process"]["tool_surface_sha256"] = "6" * 64
        observation["transport"]["authenticated"]["served_tool_surface_sha256"] = "6" * 64

        comparisons, hosted = _derive(observation)

        assert comparisons == {
            "runtime_tree_match": "false",
            "environment_match": "true",
            "tool_surface_match": "false",
            "transport_surface_match": "true",
            "oauth_discovery_consistent": "true",
        }
        assert hosted == "available"

    def test_environment_match_needs_both_hash_and_interpreter(self) -> None:
        observation = available_observation()
        observation["process"]["runtime_identity"]["interpreter"] = "cpython 3.13.1"
        assert _derive(observation)[0]["environment_match"] == "false"

        observation = available_observation()
        observation["process"]["runtime_identity"]["environment_sha256"] = "5" * 64
        assert _derive(observation)[0]["environment_match"] == "false"

    @pytest.mark.parametrize(
        ("verdict", "configured", "expected"),
        [
            ("valid", True, "true"),
            ("valid", False, "false"),
            ("invalid", True, "false"),
            ("invalid", False, "false"),
            ("absent", True, "false"),
            ("absent", False, "false"),
            ("unobserved", True, "unknown"),
            ("unobserved", False, "unknown"),
        ],
    )
    def test_oauth_discovery_consistent_table(
        self, verdict: str, configured: bool, expected: str
    ) -> None:
        observation = available_observation()
        observation["process"]["oauth_configured"] = configured
        observation["transport"]["oauth_discovery"] = {
            "verdict": verdict,
            "invalid_reason": "resource_mismatch" if verdict == "invalid" else None,
            "document_sha256": DISCOVERY if verdict in {"valid", "invalid"} else None,
        }

        assert _derive(observation)[0]["oauth_discovery_consistent"] == expected

    def test_oauth_discovery_consistent_is_unknown_without_the_process_claim(self) -> None:
        assert _derive(readyz_503())[0]["oauth_discovery_consistent"] == "unknown"

    def test_comparisons_take_only_the_three_values(self) -> None:
        comparisons, _hosted = _derive(available_observation())

        assert set(comparisons) == {
            "runtime_tree_match",
            "environment_match",
            "tool_surface_match",
            "transport_surface_match",
            "oauth_discovery_consistent",
        }
        assert set(comparisons.values()) <= {"true", "false", "unknown"}


class TestStateHash:
    def test_state_drops_observed_at_observer_and_unobserved_reason(self) -> None:
        state = derive_state(
            Observation.model_validate(available_observation()),
            Checkout.model_validate(checkout_expectations()),
        )
        dumped = json.dumps(state.model_dump(mode="json"))

        assert '"observed_at"' not in dumped
        assert '"observer"' not in dumped
        assert '"reason"' not in dumped
        assert '"invalid_reason"' in dumped  # an observed value, not the unobserved reason
        assert state.transport.authenticated.status == "observed"

    def test_state_sha256_is_invariant_to_observation_time_and_observer(self) -> None:
        base = available_observation()
        later = copy.deepcopy(base)
        later["process"]["observed_at"] = "2026-02-02T12:00:00Z"
        later["transport"]["observed_at"] = "2026-02-02T12:00:00Z"
        later["transport"]["observer"] = "drift-timer"

        checkout = Checkout.model_validate(checkout_expectations())
        assert state_sha256(
            derive_state(Observation.model_validate(base), checkout)
        ) == state_sha256(derive_state(Observation.model_validate(later), checkout))

    def test_state_sha256_is_invariant_to_the_unobserved_reason(self) -> None:
        checkout = Checkout.model_validate(checkout_expectations())
        rejected = derive_state(
            Observation.model_validate(unobserved_authenticated("probe_credential_rejected")),
            checkout,
        )
        missing = derive_state(
            Observation.model_validate(unobserved_authenticated("probe_credential_missing")),
            checkout,
        )

        assert state_sha256(rejected) == state_sha256(missing)

    def test_state_sha256_moves_with_the_state(self) -> None:
        checkout = Checkout.model_validate(checkout_expectations())
        available = derive_state(Observation.model_validate(available_observation()), checkout)
        unknown = derive_state(
            Observation.model_validate(unobserved_authenticated("timeout")), checkout
        )

        assert state_sha256(available) != state_sha256(unknown)

    def test_state_sha256_is_the_canonical_json_digest(self) -> None:
        state = derive_state(
            Observation.model_validate(available_observation()),
            Checkout.model_validate(checkout_expectations()),
        )
        canonical = json.dumps(
            state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )

        assert state_sha256(state) == hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def test_state_keeps_step_a_verbatim(self) -> None:
        state = derive_state(
            Observation.model_validate(available_observation()),
            Checkout.model_validate(checkout_expectations()),
        )

        assert state.transport.unauthenticated == UnauthenticatedStep(
            status_code=401, challenge="Bearer"
        )

    def test_state_sha256_keeps_non_ascii_unescaped(self) -> None:
        """Canonical form keeps non-ASCII as is (ADR:1003-1006): a challenge
        naming a realm in Norwegian hashes as its characters, not as escapes."""
        observation = available_observation()
        observation["transport"]["unauthenticated"]["challenge"] = 'Bearer realm="Bærum"'
        state = derive_state(
            Observation.model_validate(observation),
            Checkout.model_validate(checkout_expectations()),
        )
        dumped = state.model_dump(mode="json")
        kept = json.dumps(dumped, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        escaped = json.dumps(dumped, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

        assert "Bærum" in kept and "Bærum" not in escaped
        assert state_sha256(state) == hashlib.sha256(kept.encode("utf-8")).hexdigest()
        assert state_sha256(state) != hashlib.sha256(escaped.encode("utf-8")).hexdigest()

    def test_derive_state_is_pure(self) -> None:
        observation = Observation.model_validate(available_observation())
        checkout = Checkout.model_validate(checkout_expectations())

        assert derive_state(observation, checkout) == derive_state(observation, checkout)
