"""`observatory reattribute` through the CLI — the operator's route (ADR-0015 §7).

Nothing is mocked. The register is built with the register commands, the
archive root is found through ``LOVSPOR_OBSERVATORY_ROOT``, and the host lock
through the path the test suite isolates, because what is under test is
whether an operator can reach the corrected state with the supported
interfaces alone (CLAUDE.md: "a new field is not shipped until an operator
can reach it").
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.exclusive_workload import default_lock_path, exclusive_workload
from lovspor.observatory.freshness import CaptureState, collect_capture_state
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, FetchFailure, RetrievalProvenance
from lovspor.observatory.reattribution import (
    Attribution,
    ReattributionRequest,
    plan_reattribution,
)
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot

runner = CliRunner()
START = datetime(2026, 8, 24, 10, 42, tzinfo=UTC)
ARENDAL = "https://www.arendal.kommune.no"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return observatory


def invoke(*args: str) -> Result:
    return runner.invoke(app, ["observatory", *args])


def repaired_register() -> None:
    """Grimstad registered on Arendal's domain, then repaired — the #221 history."""
    result = invoke(
        "register-source", "--id", "4202", "--name", "Grimstad", "--domain", "arendal.kommune.no"
    )
    assert result.exit_code == 0, result.output
    result = invoke(
        "replace-source-domain",
        "--id",
        "4202",
        "--domain",
        "grimstad.kommune.no",
        "--reason",
        "registered on Arendal's domain by mistake (#221)",
        "--by",
        "owner",
    )
    assert result.exit_code == 0, result.output
    result = invoke(
        "register-source", "--id", "4203", "--name", "Arendal", "--domain", "arendal.kommune.no"
    )
    assert result.exit_code == 0, result.output


def provenance() -> RetrievalProvenance:
    return RetrievalProvenance(
        adapter="http",
        channel="http",
        discovery_method="sitemap",
        user_agent="lovspor-observatory/0.1",
        rate_limit_seconds=7.0,
    )


def misfiled_archive(root: Path) -> ObservationLog:
    """What the engine's own capture filed under Grimstad's wrong row."""
    log = ObservationLog(ObservatoryRoot(root, []))
    for minute, path in enumerate(("a", "b")):
        log.append_artifact(
            ArtifactObservation(
                authority_id="4202",
                url=f"{ARENDAL}/{path}",
                observed_at=START + timedelta(minutes=minute),
                provenance=provenance(),
                sha256=hashlib.sha256(path.encode()).hexdigest(),
                content_type="text/html",
                http_status=200,
            ),
            path.encode(),
        )
    log.append(
        FetchFailure(
            authority_id="4202",
            url=f"{ARENDAL}/gone",
            observed_at=START + timedelta(minutes=5),
            provenance=provenance(),
            outcome="http_404",
        )
    )
    return log


def correction(tmp_path: Path, **overrides: str) -> Path:
    document = {
        "from_authority": "4202",
        "to_authority": "4203",
        "host": "www.arendal.kommune.no",
        "reason": "filed under Grimstad while its row carried Arendal's domain (#221, #276)",
        "corrected_by": "owner",
    }
    document.update(overrides)
    path = tmp_path / "correction.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def tree(root: Path) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def fold(log: ObservationLog, authority_id: str) -> CaptureState:
    state = CaptureState.empty()
    assert log.scan_corrected_into(collect_capture_state(state, authority_id)).complete
    return state


class TestTheOperatorRoute:
    def test_reaches_the_corrected_state_with_supported_commands_only(
        self, root: Path, tmp_path: Path
    ) -> None:
        repaired_register()
        log = misfiled_archive(root)
        before = log.log_path.read_bytes()
        decision = correction(tmp_path)

        dry = invoke("reattribute", "--correction", str(decision))
        applied = invoke("reattribute", "--correction", str(decision), "--apply")
        verified = invoke("verify")
        composition = invoke("composition")

        assert dry.exit_code == 0, dry.output
        assert applied.exit_code == 0, applied.output
        assert "appended 6 lines" in applied.output
        assert verified.exit_code == 0, verified.output
        assert "corrections:      3 re-filed, 3 record tombstones" in composition.output
        assert log.log_path.read_bytes().startswith(before)
        assert set(fold(log, "4203").observed) == {f"{ARENDAL}/a", f"{ARENDAL}/b"}
        assert set(fold(log, "4203").holds) == {f"{ARENDAL}/gone"}
        assert fold(log, "4202") == CaptureState.empty()

    def test_a_second_apply_appends_nothing(self, root: Path, tmp_path: Path) -> None:
        repaired_register()
        log = misfiled_archive(root)
        decision = correction(tmp_path)
        invoke("reattribute", "--correction", str(decision), "--apply")
        after_first = log.log_path.read_bytes()

        again = invoke("reattribute", "--correction", str(decision), "--apply")

        assert again.exit_code == 0, again.output
        assert "already corrected: 3" in again.output
        assert "appended 0 lines" in again.output
        assert log.log_path.read_bytes() == after_first


class TestTheDryRun:
    def test_reports_the_selection_and_writes_nothing(self, root: Path, tmp_path: Path) -> None:
        repaired_register()
        misfiled_archive(root)
        before = tree(root)

        result = invoke("reattribute", "--correction", str(correction(tmp_path)))

        assert result.exit_code == 0, result.output
        assert "selected: 3 records (2 artifact, 1 fetch_failure)" in result.output
        assert "observed: 2026-08-24T10:42:00+00:00 .. 2026-08-24T10:47:00+00:00" in result.output
        assert "to append: 6 lines" in result.output
        assert '  first refiled_observation: {"correction":' in result.output
        assert '  first record_tombstone: {"corrected_at":' in result.output
        assert "dry run — nothing written" in result.output
        assert tree(root) == before
        assert not default_lock_path().exists()

    def test_runs_while_a_sweep_holds_the_lock(self, root: Path, tmp_path: Path) -> None:
        repaired_register()
        misfiled_archive(root)

        with exclusive_workload("observatory-sweep", default_lock_path()):
            result = invoke("reattribute", "--correction", str(correction(tmp_path)))

        assert result.exit_code == 0, result.output


class TestRefusals:
    def test_apply_refuses_while_a_sweep_holds_the_lock(self, root: Path, tmp_path: Path) -> None:
        repaired_register()
        log = misfiled_archive(root)
        before = log.log_path.read_bytes()

        with exclusive_workload("observatory-sweep", default_lock_path()):
            result = invoke("reattribute", "--correction", str(correction(tmp_path)), "--apply")

        assert result.exit_code == 1
        assert "Refused" in result.stderr
        assert "observatory-sweep" in result.stderr
        assert log.log_path.read_bytes() == before

    @pytest.mark.parametrize("field", ["reason", "corrected_by"])
    def test_blank_attribution_is_refused(self, root: Path, tmp_path: Path, field: str) -> None:
        repaired_register()
        misfiled_archive(root)

        result = invoke("reattribute", "--correction", str(correction(tmp_path, **{field: " "})))

        assert result.exit_code == 1
        assert "Refused: cannot read the correction" in result.stderr

    def test_a_missing_correction_file_is_refused(self, root: Path, tmp_path: Path) -> None:
        result = invoke("reattribute", "--correction", str(tmp_path / "absent.json"))

        assert result.exit_code == 1
        assert "Refused: cannot read the correction" in result.stderr

    def test_a_move_the_register_does_not_support_is_refused(
        self, root: Path, tmp_path: Path
    ) -> None:
        repaired_register()
        log = misfiled_archive(root)
        before = log.log_path.read_bytes()

        result = invoke(
            "reattribute",
            "--correction",
            str(correction(tmp_path, to_authority="9999")),
            "--apply",
        )

        assert result.exit_code == 1
        assert "Refused: 9999 is not registered" in result.stderr
        assert log.log_path.read_bytes() == before

    def test_a_damaged_log_is_refused(self, root: Path, tmp_path: Path) -> None:
        repaired_register()
        log = misfiled_archive(root)
        with log.log_path.open("ab") as handle:
            handle.write(b"{torn")
        before = log.log_path.read_bytes()

        result = invoke("reattribute", "--correction", str(correction(tmp_path)), "--apply")

        assert result.exit_code == 1
        assert "damaged" in result.stderr
        assert log.log_path.read_bytes() == before

    def test_a_conflicting_half_written_correction_is_refused(
        self, root: Path, tmp_path: Path
    ) -> None:
        repaired_register()
        log = misfiled_archive(root)
        stray = plan_reattribution(
            log,
            ReattributionRequest.model_validate(
                json.loads(correction(tmp_path, to_authority="9999").read_text(encoding="utf-8"))
            ),
            Attribution(correction_id="crashed", reason="x", corrected_by="y", corrected_at=START),
        )
        log.append(stray.appends[0])
        before = log.log_path.read_bytes()

        result = invoke("reattribute", "--correction", str(correction(tmp_path)), "--apply")

        assert result.exit_code == 1
        assert "half-written correction to 9999" in result.stderr
        assert log.log_path.read_bytes() == before
