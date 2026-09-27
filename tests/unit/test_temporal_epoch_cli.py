"""``lovspor temporal-epoch`` end to end (ADR-0012 Amendment 1, lovspor#432).

The operator's question, per CLAUDE.md: can the epoch state be reached
using only supported interfaces? Every test drives the real CLI against
real git — a bare origin, an operator or runner clone, and a consumer
clone acquired with ``fetch_corpus`` — and reads the result back through
``get_temporal_events``. Nothing here constructs the record directly.
"""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.corpus_fetch import fetch_corpus
from lovspor.errors import TemporalDerivationError
from lovspor.mcp import build_server
from lovspor.storage.manifest import ManifestRecord
from lovspor.sync.orchestrator import _ensure_head_attested, _UpstreamDoc
from lovspor.temporal import TEMPORAL_PARSER_VERSION
from lovspor.temporal_attestation import (
    EPOCH_NOTES_REF,
    TemporalAttestation,
    fetch_attestations,
    read_attestation,
    read_gate_epochs,
    write_attestation,
)
from lovspor.temporal_gate import UnattestedGateStateError
from tests.unit.test_mcp_recorded_at import _doc, _manifest, _record

LAW = "## Kapittel 1.\n\n### § 1. Formål\n\nLovtekst {n}.\n"
EPOCH_AT = "2026-05-09T00:00:00Z"
runner = CliRunner()


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    ).stdout.strip()


def _identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")


def _state(repo: Path, n: int, iso_date: str) -> str:
    (repo / "lover" / "testloven.md").write_text(_doc("Testloven", LAW.format(n=n)))
    (repo / "manifest.json").write_text(_manifest({"doc-a": _record("testloven", f"h{n}")}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"state {n}", env={"GIT_AUTHOR_DATE": iso_date})
    return _git(repo, "rev-parse", "HEAD")


def _attest(repo: Path, sha: str) -> None:
    write_attestation(
        repo,
        TemporalAttestation(
            corpus_commit=sha,
            parser_version=TEMPORAL_PARSER_VERSION,
            documents_reconciled=1,
            notes_total=0,
            events_total=0,
            attested_at=datetime(2026, 5, 11, 13, 0, tzinfo=UTC),
        ),
    )


def _origin(tmp_path: Path, *, gate_ran: bool) -> tuple[Path, list[str]]:
    """A corpus whose history straddles a gate that ran under the serving
    parser version: boundary (05-01), a gate-era intermediate commit
    (05-10) and the attested sync commit (05-11)."""
    work = tmp_path / "work"
    (work / "lover").mkdir(parents=True)
    _git(work, "init", "-b", "main")
    _identity(work)
    shas = [_state(work, 1, "2026-05-01T12:00:00Z")]
    if gate_ran:
        shas.append(_state(work, 2, "2026-05-10T12:00:00Z"))
        shas.append(_state(work, 3, "2026-05-11T12:00:00Z"))
        _attest(work, shas[-1])
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--bare", str(work), str(origin))
    if gate_ran:
        _git(work, "push", str(origin), "refs/notes/temporal-attestations")
    return origin, shas


def _writable_clone(origin: Path, dest: Path) -> Path:
    _git(origin.parent, "clone", str(origin), str(dest))
    _identity(dest)
    return dest


def _cli(corpus: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["temporal-epoch", "--corpus-path", str(corpus), *args])
    return result.exit_code, result.output


def _backfill(clone: Path, boundary: str, *extra: str) -> tuple[int, str]:
    return _cli(
        clone,
        "backfill",
        "--epoch-at",
        EPOCH_AT,
        "--boundary-commit",
        boundary,
        "--sync-run",
        "33854986231",
        *extra,
    )


def _origin_has_epoch_ref(origin: Path) -> bool:
    listed = subprocess.run(
        ["git", "ls-remote", "--exit-code", str(origin), EPOCH_NOTES_REF],
        capture_output=True,
        check=False,
    )
    return listed.returncode == 0


# ---------- backfill: the operator path ----------


def test_backfill_dry_run_validates_and_writes_nothing(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _backfill(clone, shas[0][:7])

    assert code == 0, output
    assert "dry run" in output
    assert f'"boundary_commit": "{shas[0]}"' in output
    assert '"source": "backfill"' in output
    assert '"evidence": "lovspor sync run 33854986231"' in output
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


def test_backfilled_epoch_reaches_a_consumer_through_supported_interfaces(
    tmp_path: Path,
) -> None:
    """Operator backfills from a writable clone; a consumer acquires the
    corpus with fetch_corpus; get_temporal_events then serves outcomes
    2, 4 and 5 — no refspec change, no hand-made note."""
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _backfill(clone, shas[0], "--apply")

    assert code == 0, output
    assert "recorded and pushed" in output
    consumer = tmp_path / "consumer"
    fetch_corpus(consumer, repo_url=f"file://{origin}", full_history=True)
    tool = build_server(consumer)._tool_manager._tools["get_temporal_events"].fn
    assert tool(slug="testloven")["reconciliation"] == "attested"
    assert tool(slug="testloven", recorded_at="2026-05-05")["reconciliation"] == "unattested"
    with pytest.raises(UnattestedGateStateError) as caught:
        tool(slug="testloven", recorded_at="2026-05-10")
    assert caught.value.corpus_commit == shas[1]


def test_backfill_rerun_is_an_idempotent_no_op(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    _backfill(clone, shas[0], "--apply")
    pushed = _git(clone, "rev-parse", EPOCH_NOTES_REF)

    again = _writable_clone(origin, tmp_path / "operator-2")
    code, output = _backfill(again, shas[0], "--apply")

    assert code == 0, output
    assert "identical gate epoch already recorded" in output
    assert _git(again, "rev-parse", EPOCH_NOTES_REF) == pushed


def test_backfill_later_than_the_first_attested_state_is_refused(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _cli(
        clone,
        "backfill",
        "--epoch-at",
        "2026-05-11T12:00:01Z",
        "--boundary-commit",
        shas[0],
        "--sync-run",
        "1",
        "--apply",
    )

    assert code == 1
    assert "earliest attested" in output
    assert not _origin_has_epoch_ref(origin)


def test_backfill_boundary_not_before_the_epoch_is_refused(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _backfill(clone, shas[1], "--apply")

    assert code == 1
    assert "not before the epoch" in output
    assert not _origin_has_epoch_ref(origin)


@pytest.mark.parametrize(
    ("epoch_at", "sync_run"),
    [("2026-05-09T00:00:00", "1"), ("not a date", "1"), (EPOCH_AT, "run-1")],
)
def test_backfill_refuses_malformed_input_before_touching_git(
    tmp_path: Path,
    epoch_at: str,
    sync_run: str,
) -> None:
    code, _output = _cli(
        tmp_path / "missing",
        "backfill",
        "--epoch-at",
        epoch_at,
        "--boundary-commit",
        "abcdef1",
        "--sync-run",
        sync_run,
    )

    assert code == 2


def test_backfill_names_an_unresolvable_boundary(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _backfill(clone, "f" * 40)

    assert code == 1
    assert "cannot resolve commit" in output


# ---------- record-sync-run: the sync's mechanical write ----------


def test_first_run_under_a_version_records_and_pushes_the_epoch(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    before = datetime.now(UTC)

    code, output = _cli(clone, "record-sync-run", "--sync-run", "77")

    assert code == 0, output
    assert _origin_has_epoch_ref(origin)
    record = read_gate_epochs(clone)[TEMPORAL_PARSER_VERSION]
    assert record.boundary_commit == shas[0]
    assert record.source == "sync-run"
    assert record.evidence == "lovspor sync run 77"
    assert before.replace(microsecond=0) <= record.epoch_at <= datetime.now(UTC)
    assert record.epoch_at.microsecond == 0


def test_a_later_run_under_the_same_version_writes_nothing(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    first = _writable_clone(origin, tmp_path / "runner-1")
    _cli(first, "record-sync-run", "--sync-run", "77")
    pushed = _git(first, "rev-parse", EPOCH_NOTES_REF)

    second = _writable_clone(origin, tmp_path / "runner-2")
    code, output = _cli(second, "record-sync-run", "--sync-run", "78")

    assert code == 0, output
    assert "already recorded" in output
    assert json.loads(output[output.index("{") :])["evidence"] == "lovspor sync run 77"
    assert _git(second, "rev-parse", EPOCH_NOTES_REF) == pushed


def test_attestations_without_a_record_warn_and_invent_nothing(tmp_path: Path) -> None:
    """Today's production state for version 2 until the backfill runs."""
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "runner")

    code, output = _cli(clone, "record-sync-run", "--sync-run", "79")

    assert code == 0, output
    assert "warning:" in output
    assert "backfill" in output
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


def test_a_failed_gate_leaves_the_epoch_and_the_next_read_fails_closed(
    tmp_path: Path,
) -> None:
    """The 2026-09-03 scenario: the first run under a version records its
    epoch, then the gate fails counted conformance. The epoch is on
    origin, no attestation exists, and the gate-era state the run built
    is refused rather than served as 'unattested'."""
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = tmp_path / "runner"
    fetch_corpus(clone, repo_url=f"file://{origin}", full_history=True)
    _identity(clone)

    code, output = _cli(clone, "record-sync-run", "--sync-run", "33735548584")
    assert code == 0, output
    head = _state(clone, 2, datetime.now(UTC).isoformat())
    with pytest.raises(TemporalDerivationError, match="count mismatch"):
        _ensure_head_attested(clone, *_mismatched_gate_inputs(), datetime.now(UTC))

    assert _origin_has_epoch_ref(origin)
    fetch_attestations(clone)
    assert read_attestation(clone, head, TEMPORAL_PARSER_VERSION) is None
    tool = build_server(clone)._tool_manager._tools["get_temporal_events"].fn
    with pytest.raises(UnattestedGateStateError):
        tool(slug="testloven")


def _mismatched_gate_inputs() -> tuple[dict[str, _UpstreamDoc], dict[str, ManifestRecord]]:
    """Upstream XML with one changesToParent against a rendering with no
    note: the counted-conformance mismatch class of lovspor#235."""
    upstream = _UpstreamDoc(
        doc_id="doc-a",
        source_dataset="gjeldende-lover",
        xml_bytes=b'<root><div class="changesToParent">x</div></root>',
        xml_hash="h2",
        slug="testloven",
        title="Testloven",
        eu_basis=(),
    )
    record = ManifestRecord.model_validate(_record("testloven", "h2"))
    return {"doc-a": upstream}, {"doc-a": record}
