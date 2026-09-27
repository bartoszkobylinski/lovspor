"""Planning a re-attribution (ADR-0015 §6, §7) — the logic under `reattribute`."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.errors import CorrectionRefusedError, LogIntegrityError
from lovspor.observatory.log import ObservationLog, verify_snapshot
from lovspor.observatory.model import (
    ArtifactObservation,
    FetchFailure,
    ObservationRecord,
    RecordTombstone,
    RefiledObservation,
    RetrievalProvenance,
    record_key,
    record_to_json_line,
)
from lovspor.observatory.reattribution import (
    Attribution,
    ReattributionRequest,
    apply_plan,
    check_registry,
    plan_reattribution,
)
from lovspor.observatory.registry import SourceRecord, SourceRegistry
from lovspor.observatory.storage import ObservatoryRoot

START = datetime(2026, 8, 24, 10, 42, tzinfo=UTC)
ARENDAL = "https://www.arendal.kommune.no"


def make_log(root: Path) -> ObservationLog:
    return ObservationLog(ObservatoryRoot(root, []))


def artifact(path: str, authority_id: str = "4202", minute: int = 0) -> ArtifactObservation:
    return ArtifactObservation(
        authority_id=authority_id,
        url=f"{ARENDAL}/{path}",
        observed_at=START + timedelta(minutes=minute),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="lovspor-observatory/0.1",
            rate_limit_seconds=7.0,
        ),
        sha256=hashlib.sha256(path.encode()).hexdigest(),
        content_type="text/html",
        http_status=200,
    )


def failure(path: str, authority_id: str = "4202", minute: int = 0) -> FetchFailure:
    return FetchFailure(
        authority_id=authority_id,
        url=f"{ARENDAL}/{path}",
        observed_at=START + timedelta(minutes=minute),
        provenance=artifact(path).provenance,
        outcome="http_404",
    )


def request(**overrides: str) -> ReattributionRequest:
    fields = {
        "from_authority": "4202",
        "to_authority": "4203",
        "host": "www.arendal.kommune.no",
        "reason": "filed under Grimstad while its row carried Arendal's domain (#221, #276)",
        "corrected_by": "owner",
    }
    fields.update(overrides)
    return ReattributionRequest.model_validate(fields)


def attribution(correction_id: str = "run-1") -> Attribution:
    return Attribution(
        correction_id=correction_id,
        reason=request().reason,
        corrected_by="owner",
        corrected_at=datetime(2026, 9, 27, 8, 0, tzinfo=UTC),
    )


def key_of(record: ObservationRecord) -> str:
    return record_key(record_to_json_line(record).encode("utf-8"))


def source(authority_id: str, domain: str) -> SourceRecord:
    return SourceRecord(
        authority_type="kommune",
        authority_id=authority_id,
        name=authority_id,
        canonical_domain=domain,
    )


def registry(grimstad: str = "grimstad.kommune.no") -> SourceRegistry:
    return SourceRegistry(
        sources={"4202": source("4202", grimstad), "4203": source("4203", "arendal.kommune.no")}
    )


def misfiled(root: Path) -> ObservationLog:
    """Grimstad's row holding Arendal's pages, beside records that stay put."""
    log = make_log(root)
    for record in (
        artifact("a", minute=1),
        failure("b", minute=2),
        artifact("own", authority_id="4203", minute=3),
        artifact("c", minute=4),
    ):
        log.append(record)
    log.append(
        artifact("x", minute=5).model_copy(update={"url": "https://www.grimstad.kommune.no/x"})
    )
    return log


def _tree(root: Path) -> list[tuple[str, bytes]]:
    return sorted((str(path), path.read_bytes()) for path in root.rglob("*") if path.is_file())


def authorities(log: ObservationLog) -> list[str]:
    seen: list[str] = []
    log.scan_corrected_into(lambda record: seen.append(record.authority_id))  # type: ignore[union-attr]
    return seen


class TestTheRequest:
    @pytest.mark.parametrize("field", ["reason", "corrected_by", "host"])
    def test_a_blank_field_is_refused(self, field: str) -> None:
        with pytest.raises(ValidationError, match="must not be blank"):
            request(**{field: "  "})

    def test_a_missing_attribution_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ReattributionRequest.model_validate(
                {"from_authority": "4202", "to_authority": "4203", "host": "h", "reason": "r"}
            )

    def test_a_move_to_the_same_authority_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="same authority"):
            request(to_authority="4202")

    def test_attribution_is_stored_trimmed(self) -> None:
        assert request(corrected_by="  owner ").corrected_by == "owner"


class TestTheRegisterMustSupportTheMove:
    def test_passes_on_the_repaired_register(self) -> None:
        check_registry(registry(), request())

    def test_an_unregistered_target_is_refused(self) -> None:
        with pytest.raises(CorrectionRefusedError, match="not registered"):
            check_registry(registry(), request(to_authority="9999"))

    def test_a_target_whose_domain_does_not_cover_the_host_is_refused(self) -> None:
        with pytest.raises(CorrectionRefusedError, match="outside 4203"):
            check_registry(registry(), request(host="www.grimstad.kommune.no"))

    def test_a_source_still_covering_the_host_is_refused(self) -> None:
        with pytest.raises(CorrectionRefusedError, match="still inside 4202"):
            check_registry(registry(grimstad="kommune.no"), request())

    def test_an_unregistered_source_does_not_block_the_move(self) -> None:
        check_registry(registry(), request(from_authority="1111"))


class TestThePlan:
    def test_an_empty_selection_has_no_observation_bounds(self, tmp_path: Path) -> None:
        plan = plan_reattribution(make_log(tmp_path), request(), attribution())

        assert plan.selected == 0
        assert plan.first_observed is None
        assert plan.last_observed is None

    def test_selects_the_source_s_records_on_the_host_and_nothing_else(
        self, tmp_path: Path
    ) -> None:
        plan = plan_reattribution(misfiled(tmp_path), request(), attribution())

        assert (plan.selected, plan.artifacts, plan.failures) == (3, 2, 1)
        assert (plan.first_observed, plan.last_observed) == (
            START + timedelta(minutes=1),
            START + timedelta(minutes=4),
        )
        assert len(plan.appends) == 6

    def test_orders_each_correction_refiled_half_first(self, tmp_path: Path) -> None:
        plan = plan_reattribution(misfiled(tmp_path), request(), attribution())

        kinds = [type(record) for record in plan.appends]
        assert kinds == [RefiledObservation, RecordTombstone] * 3
        for refiled, tombstone in zip(plan.appends[::2], plan.appends[1::2], strict=True):
            assert refiled.correction.supersedes == tombstone.retracts  # type: ignore[union-attr]

    def test_the_refiled_record_restates_the_original_with_its_provenance(
        self, tmp_path: Path
    ) -> None:
        plan = plan_reattribution(misfiled(tmp_path), request(), attribution())
        refiled = plan.appends[0]

        assert isinstance(refiled, RefiledObservation)
        assert refiled.observation == artifact("a", authority_id="4203", minute=1)
        assert refiled.correction.previous_values == {"authority_id": "4202"}
        assert refiled.correction.corrected_fields == ("authority_id",)
        assert refiled.correction.corrected_by == "owner"
        assert refiled.correction.correction_id == "run-1"

    def test_the_host_is_compared_as_a_domain(self, tmp_path: Path) -> None:
        plan = plan_reattribution(
            misfiled(tmp_path), request(host="WWW.Arendal.Kommune.NO."), attribution()
        )

        assert plan.selected == 3

    def test_selects_the_exact_url_host_not_its_subdomains(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(artifact("on-requested-host"))
        log.append(
            artifact("on-subdomain").model_copy(
                update={"url": "https://archive.www.arendal.kommune.no/on-subdomain"}
            )
        )

        plan = plan_reattribution(log, request(), attribution())

        assert plan.selected == 1
        assert len(plan.appends) == 2
        refiled = plan.appends[0]
        assert isinstance(refiled, RefiledObservation)
        assert refiled.observation.url == f"{ARENDAL}/on-requested-host"

    def test_a_url_without_a_hostname_is_not_selected(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(artifact("missing-host").model_copy(update={"url": "XXXX"}))

        plan = plan_reattribution(log, request(host="XXXX"), attribution())

        assert plan.selected == 0
        assert plan.appends == ()

    def test_planning_writes_nothing(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        before = sorted((p.name, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file())

        plan_reattribution(log, request(), attribution())

        assert (
            sorted((p.name, p.read_bytes()) for p in tmp_path.rglob("*") if p.is_file()) == before
        )

    def test_a_damaged_log_is_refused(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        with log.log_path.open("ab") as handle:
            handle.write(b"{torn")

        with pytest.raises(LogIntegrityError, match="damaged"):
            plan_reattribution(log, request(), attribution())

    def test_byte_identical_lines_are_one_claim_and_one_correction(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        log.append(artifact("a"))
        log.append(artifact("a"))

        plan = plan_reattribution(log, request(), attribution())
        apply_plan(log, plan)

        assert (plan.selected, len(plan.appends)) == (1, 2)
        assert authorities(log) == ["4203", "4203"]


class TestApplying:
    def test_moves_the_records_and_keeps_every_existing_byte(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        prefix = log.log_path.read_bytes()

        appended = apply_plan(log, plan_reattribution(log, request(), attribution()))

        assert appended == 6
        assert log.log_path.read_bytes().startswith(prefix)
        assert authorities(log) == ["4203", "4203", "4203", "4203", "4202"]

    def test_a_second_run_appends_nothing(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        apply_plan(log, plan_reattribution(log, request(), attribution("run-1")))
        size = log.log_path.stat().st_size

        again = plan_reattribution(log, request(), attribution("run-2"))

        assert (again.already_corrected, len(again.appends)) == (3, 0)
        assert apply_plan(log, again) == 0
        assert log.log_path.stat().st_size == size

    def test_a_crash_between_the_halves_is_completed_by_the_missing_half_only(
        self, tmp_path: Path
    ) -> None:
        log = misfiled(tmp_path)
        first = plan_reattribution(log, request(), attribution("run-1"))
        log.append(first.appends[0])
        assert authorities(log)[0] == "4202"
        assert verify_snapshot(log).incomplete_corrections

        resumed = plan_reattribution(log, request(), attribution("run-2"))

        assert resumed.to_complete == 1
        assert resumed.appends[0] == first.appends[1]
        assert len(resumed.appends) == 5
        apply_plan(log, resumed)
        assert authorities(log) == ["4203", "4203", "4203", "4203", "4202"]
        assert not verify_snapshot(log).incomplete_corrections

    def test_a_lone_tombstone_is_completed_with_its_own_attribution(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        log.append(RecordTombstone(retracts=key_of(original), **attribution("run-0").model_dump()))

        plan = plan_reattribution(log, request(), attribution("run-1"))

        assert plan.to_complete == 1
        [refiled] = plan.appends
        assert isinstance(refiled, RefiledObservation)
        assert refiled.correction.correction_id == "run-0"
        apply_plan(log, plan)
        assert authorities(log) == ["4203"]

    def test_a_half_written_correction_elsewhere_is_refused(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        stray = plan_reattribution(log, request(to_authority="4204"), attribution("run-0"))
        log.append(stray.appends[0])

        expected = f"record {key_of(original)[:12]} has a half-written correction to 4204, not 4203"
        with pytest.raises(CorrectionRefusedError, match=expected):
            plan_reattribution(log, request(), attribution("run-1"))

    def test_the_audit_is_clean_after_a_run(self, tmp_path: Path) -> None:
        log = make_log(tmp_path)
        for path in ("a", "b"):
            log.append_artifact(artifact(path), path.encode())

        apply_plan(log, plan_reattribution(log, request(), attribution()))

        assert verify_snapshot(log).ok


class TestEdgesOfAPlan:
    def test_a_correction_is_never_itself_selected_for_correction(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        apply_plan(log, plan_reattribution(log, request(), attribution()))

        onward = plan_reattribution(
            log, request(from_authority="4203", to_authority="4204"), attribution("run-2")
        )

        assert onward.selected == 1
        assert onward.appends[0].observation.url == f"{ARENDAL}/own"  # type: ignore[union-attr]

    def test_a_stray_tombstone_naming_no_line_changes_nothing(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        log.append(RecordTombstone(retracts="f" * 64, **attribution("stray").model_dump()))

        plan = plan_reattribution(log, request(), attribution())

        assert (plan.selected, plan.already_corrected, plan.to_complete) == (3, 0, 0)

    def test_a_half_on_a_record_of_another_authority_is_left_alone(self, tmp_path: Path) -> None:
        log = misfiled(tmp_path)
        own = artifact("own", authority_id="4203", minute=3)
        log.append(RecordTombstone(retracts=key_of(own), **attribution("stray").model_dump()))

        plan = plan_reattribution(log, request(), attribution())

        assert (plan.selected, plan.to_complete, len(plan.appends)) == (3, 0, 6)

    def test_a_tombstone_disagreeing_with_its_refiled_half_does_not_count_as_done(
        self, tmp_path: Path
    ) -> None:
        log = make_log(tmp_path)
        original = artifact("a")
        log.append(original)
        first = plan_reattribution(log, request(), attribution("run-1"))
        log.append(first.appends[0])
        log.append(first.appends[1].model_copy(update={"corrected_by": "someone else"}))

        plan = plan_reattribution(log, request(), attribution("run-2"))

        assert (plan.already_corrected, plan.to_complete) == (0, 1)
        assert plan.appends == (first.appends[1],)
