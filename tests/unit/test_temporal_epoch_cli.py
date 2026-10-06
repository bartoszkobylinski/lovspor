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
from click.testing import Result
from typer.testing import CliRunner

import lovspor.temporal_gate as temporal_gate_module
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
from lovspor.temporal_gate import (
    BackfillRequest,
    SyncRunRequest,
    UnattestedGateStateError,
    backfill_epoch,
    is_repository_root,
    record_sync_run_epoch,
)
from tests.unit.cli_output import said
from tests.unit.test_mcp_recorded_at import _doc, _manifest, _record

LAW = "## Kapittel 1.\n\n### § 1. Formål\n\nLovtekst {n}.\n"
EPOCH_AT = "2026-05-09T00:00:00Z"
runner = CliRunner()


def test_commit_resolution_requests_text_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout="a" * 40 + "\n", stderr="")

    monkeypatch.setattr(temporal_gate_module.subprocess, "run", run)

    assert temporal_gate_module._resolve_commit(tmp_path, "HEAD") == "a" * 40
    assert calls == [
        (
            ["git", "rev-parse", "--verify", "HEAD^{commit}"],
            {
                "cwd": tmp_path,
                "capture_output": True,
                "text": True,
                "check": False,
            },
        ),
    ]


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

    code, output = _backfill(clone, shas[0])

    assert code == 0, output
    assert output.splitlines()[0] == "dry run: valid; nothing written (pass --apply)"
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
    assert output.splitlines()[0] == "gate epoch recorded and pushed"
    assert '\n  "parser_version":' in output
    assert "\n}" in output
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
    assert output.splitlines()[0] == (
        "identical gate epoch already recorded on origin; nothing to do"
    )
    assert json.loads(output[output.index("{") :])["boundary_commit"] == shas[0]
    assert _git(again, "rev-parse", EPOCH_NOTES_REF) == pushed


def test_backfill_retry_publishes_a_note_left_by_a_rejected_push(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    first_code, first_output = _backfill(clone, shas[0], "--apply")

    assert first_code == 1
    assert "failed to push" in first_output
    assert read_gate_epochs(clone)
    assert not _origin_has_epoch_ref(origin)

    hook.unlink()
    retry_code, retry_output = _backfill(clone, shas[0], "--apply")

    assert retry_code == 0, retry_output
    assert _origin_has_epoch_ref(origin)


def test_a_published_retry_says_the_record_was_only_local(tmp_path: Path) -> None:
    """The retry's report names what happened: the record existed, only in
    this clone, and is now pushed — not 'already on origin'."""
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _backfill(clone, shas[0], "--apply")
    hook.unlink()

    code, output = _backfill(clone, shas[0], "--apply")

    assert code == 0, output
    assert output.splitlines()[0] == (
        "identical gate epoch already recorded; it was only local — now pushed"
    )


def test_a_published_sync_run_retry_says_the_record_was_only_local(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _cli(clone, "record-sync-run", "--sync-run", "77")
    hook.unlink()

    code, output = _cli(clone, "record-sync-run", "--sync-run", "77")

    assert code == 0, output
    assert output.splitlines()[0] == "gate epoch already recorded; it was only local — now pushed"


def test_backfill_dry_run_never_publishes_a_local_only_record(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    _backfill(clone, shas[0], "--apply")
    hook.unlink()

    code, output = _backfill(clone, shas[0])

    assert code == 0, output
    assert "identical gate epoch already recorded" in output
    assert not _origin_has_epoch_ref(origin)


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
    [
        ("2026-05-09T00:00:00", "1"),
        ("2026-05-09T02:00:00+02:00", "1"),
        ("not a date", "1"),
        (EPOCH_AT, "run-1"),
    ],
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


@pytest.mark.parametrize("sync_run", ["run-77", ""])
def test_record_sync_run_refuses_a_non_numeric_workflow_run_id(
    tmp_path: Path,
    sync_run: str,
) -> None:
    """Both supported writers record a GitHub workflow run id as evidence;
    malformed evidence must be rejected before the immutable note is written."""
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")

    code, _output = _cli(clone, "record-sync-run", "--sync-run", sync_run)

    assert code == 2
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


def test_first_run_under_a_version_records_and_pushes_the_epoch(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    before = datetime.now(UTC)

    code, output = _cli(clone, "record-sync-run", "--sync-run", "77")

    assert code == 0, output
    assert output.splitlines()[0] == "gate epoch recorded and pushed"
    emitted = json.loads(output[output.index("{") :])
    assert emitted["source"] == "sync-run"
    assert _origin_has_epoch_ref(origin)
    record = read_gate_epochs(clone)[TEMPORAL_PARSER_VERSION]
    assert record.boundary_commit == shas[0]
    assert record.source == "sync-run"
    assert record.evidence == "lovspor sync run 77"
    assert before.replace(microsecond=0) <= record.epoch_at <= datetime.now(UTC)
    assert record.epoch_at.microsecond == 0


def test_record_sync_run_retry_publishes_a_note_left_by_a_rejected_push(
    tmp_path: Path,
) -> None:
    """The workflow command promises to push the epoch at run start.

    A transient push rejection leaves the immutable note in the runner clone;
    retrying the supported command must publish that note rather than mistake
    local-only state for a record already present on origin.
    """
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    hook = origin / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    first_code, first_output = _cli(clone, "record-sync-run", "--sync-run", "77")

    assert first_code == 1
    assert "failed to push" in first_output
    assert read_gate_epochs(clone)
    assert not _origin_has_epoch_ref(origin)

    hook.unlink()
    retry_code, retry_output = _cli(clone, "record-sync-run", "--sync-run", "77")

    assert retry_code == 0, retry_output
    assert _origin_has_epoch_ref(origin)


def test_a_later_run_under_the_same_version_writes_nothing(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    first = _writable_clone(origin, tmp_path / "runner-1")
    _cli(first, "record-sync-run", "--sync-run", "77")
    pushed = _git(first, "rev-parse", EPOCH_NOTES_REF)

    second = _writable_clone(origin, tmp_path / "runner-2")
    code, output = _cli(second, "record-sync-run", "--sync-run", "78")

    assert code == 0, output
    assert output.splitlines()[0] == "gate epoch already recorded on origin; nothing to do"
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


# ---------- input validation: every argument, before any git work ----------


def _backfill_args(**overrides: str) -> list[str]:
    values = {
        "--epoch-at": EPOCH_AT,
        "--boundary-commit": "a" * 40,
        "--sync-run": "33854986231",
        **overrides,
    }
    return ["backfill", *(item for pair in values.items() for item in pair)]


@pytest.mark.parametrize(
    "overrides",
    [
        {"--boundary-commit": "abcdef1"},
        {"--boundary-commit": "HEAD"},
        {"--boundary-commit": "main"},
        {"--boundary-commit": "A" * 40},
        {"--boundary-commit": "a" * 41},
        {"--boundary-commit": f" {'a' * 40}"},
        {"--boundary-commit": ""},
        {"--epoch-at": ""},
        {"--epoch-at": " "},
        {"--epoch-at": "2026-05-09"},
        {"--sync-run": ""},
        {"--sync-run": " 1"},
        {"--sync-run": "1 "},
        {"--sync-run": "0"},
        {"--sync-run": "-5"},
        {"--sync-run": "007"},
        {"--sync-run": "1.5"},
        {"--sync-run": "1e3"},
        {"--sync-run": "\uff11\uff12"},  # fullwidth digits: str.isdigit() says yes
        {"--sync-run": "9" * 21},
    ],
)
def test_backfill_malformed_argument_is_a_usage_error_with_nothing_written(
    tmp_path: Path,
    overrides: dict[str, str],
) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, output = _cli(clone, *_backfill_args(**overrides), "--apply")

    assert code == 2, output
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


@pytest.mark.parametrize(
    "args",
    [
        _backfill_args(**{"--sync-run": "not-a-run-id"}),
        _backfill_args(**{"--epoch-at": "2026-05-09"}),
        _backfill_args(**{"--boundary-commit": "HEAD"}),
        ["record-sync-run", "--sync-run", "not-a-run-id"],
    ],
    ids=["backfill-sync-run", "backfill-epoch-at", "backfill-boundary-commit", "record-sync-run"],
)
def test_malformed_argument_is_rejected_before_any_git_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    """The CLI contract says malformed input exits 2 before git runs."""
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    real_run = subprocess.run
    git_commands: list[list[str]] = []

    def observe_git(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = args[0]
        if isinstance(command, list) and command[:1] == ["git"]:
            git_commands.append(command)
        return real_run(*args, **kwargs)  # type: ignore[arg-type, return-value]

    monkeypatch.setattr(subprocess, "run", observe_git)

    code, output = _cli(clone, *args)

    assert code == 2, output
    assert git_commands == []


@pytest.mark.parametrize(
    ("args", "request_model", "field"),
    [
        (_backfill_args(**{"--sync-run": "bad"}), "BackfillRequest", "sync_run"),
        (_backfill_args(**{"--epoch-at": "2026-05-09"}), "BackfillRequest", "epoch_at"),
        (_backfill_args(**{"--boundary-commit": "HEAD"}), "BackfillRequest", "boundary_commit"),
        (["record-sync-run", "--sync-run", "bad"], "SyncRunRequest", "sync_run"),
    ],
)
def test_argument_validation_precedes_invalid_corpus_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    request_model: str,
    field: str,
) -> None:
    """Malformed input must fail before even the corpus's read-only git check."""

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("argument validation ran a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    result = _invoke(tmp_path / "missing", *args)

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert result.stderr.startswith(f"error: 1 validation error for {request_model}")
    assert f"\n{field}\n" in result.stderr
    assert "--corpus-path" not in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        ["record-sync-run"],
        ["backfill", "--epoch-at", EPOCH_AT, "--sync-run", "77"],
    ],
)
def test_missing_required_option_is_rejected_before_any_git_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
) -> None:
    """Click's required-option validation also precedes the corpus check."""

    def unexpected_run(*args: object, **kwargs: object) -> None:
        pytest.fail("missing-option validation ran a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    result = _invoke(tmp_path / "missing", *args)

    assert result.exit_code == 2, result.output
    assert result.stdout == ""
    assert said("Missing option", result.stderr)
    missing_option = "--sync-run" if args[0] == "record-sync-run" else "--boundary-commit"
    assert said(missing_option, result.stderr)
    assert "not the top level" not in result.stderr


@pytest.mark.parametrize("sync_run", ["0", "-5", " 77", "77 ", "007", "77\n", "9" * 21, "1_000"])
def test_record_sync_run_malformed_run_id_is_a_usage_error(tmp_path: Path, sync_run: str) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")

    code, output = _cli(clone, "record-sync-run", "--sync-run", sync_run)

    assert code == 2, output
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


def test_the_largest_accepted_run_id_is_recorded_verbatim(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")

    code, output = _cli(clone, "record-sync-run", "--sync-run", "9" * 20)

    assert code == 0, output
    assert (
        read_gate_epochs(clone)[TEMPORAL_PARSER_VERSION].evidence == f"lovspor sync run {'9' * 20}"
    )


def test_the_run_id_is_never_read_from_the_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The workflow passes "$GITHUB_RUN_ID" explicitly; the command has no
    env fallback, so an absent option is a usage error even when the
    variable is set, and a set variable never overrides the option."""
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")

    code, _output = _cli(clone, "record-sync-run")
    assert code == 2
    assert read_gate_epochs(clone) == {}

    code, output = _cli(clone, "record-sync-run", "--sync-run", "77")
    assert code == 0, output
    assert read_gate_epochs(clone)[TEMPORAL_PARSER_VERSION].evidence == "lovspor sync run 77"


def test_parser_version_is_the_engines_own_and_not_an_argument(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    code, _output = _cli(
        clone, *_backfill_args(**{"--boundary-commit": shas[0]}), "--parser-version", "3"
    )
    assert code == 2

    code, output = _cli(clone, *_backfill_args(**{"--boundary-commit": shas[0]}))
    assert code == 0, output
    assert f'"parser_version": {TEMPORAL_PARSER_VERSION}' in output


@pytest.mark.parametrize("command", ["record-sync-run", "backfill"])
def test_corpus_path_must_be_the_top_of_a_git_clone(tmp_path: Path, command: str) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    plain = tmp_path / "plain"
    plain.mkdir()
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    args = (
        ["record-sync-run", "--sync-run", "1"] if command == "record-sync-run" else _backfill_args()
    )

    not_a_corpus = tmp_path / "other-repo"
    not_a_corpus.mkdir()
    _git(not_a_corpus, "init", "-b", "main")

    for corpus in (plain, clone / "lover", a_file, tmp_path / "missing", not_a_corpus):
        result = _invoke(corpus, *args)
        assert result.exit_code == 2, (corpus, result.output)
        assert result.stdout == ""
        assert said("--corpus-path", result.stderr)
        assert said("Invalid value for --corpus-path:", result.stderr)
        assert said(
            f"{corpus} is not the top level of a lovverk corpus clone (git + manifest.json)",
            result.stderr,
        )
    assert read_gate_epochs(clone) == {}
    assert not _origin_has_epoch_ref(origin)


def test_a_relative_corpus_path_resolves_against_the_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pinned inside tmp_path: '.' is whatever the caller stands in, and
    only a corpus clone there may be touched."""
    origin, shas = _origin(tmp_path, gate_ran=True)
    _writable_clone(origin, tmp_path / "operator")
    monkeypatch.chdir(tmp_path)

    code, output = _cli(Path(), "record-sync-run", "--sync-run", "1")
    assert code == 2, output

    monkeypatch.chdir(tmp_path / "operator")
    code, output = _cli(Path(), *_backfill_args(**{"--boundary-commit": shas[0]}))
    assert code == 0, output


def test_corpus_path_expands_the_home_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    _writable_clone(origin, tmp_path / "operator")
    monkeypatch.setenv("HOME", str(tmp_path))

    code, output = _cli(Path("~/operator"), *_backfill_args(**{"--boundary-commit": shas[0]}))

    assert code == 0, output
    assert "dry run" in output


# ---------- which stream says what (real clones, no stubs) ----------


def _invoke(corpus: Path, *args: str) -> Result:
    return runner.invoke(app, ["temporal-epoch", "--corpus-path", str(corpus), *args])


def test_a_usage_error_is_on_stderr_only(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")

    result = _invoke(clone, "record-sync-run", "--sync-run", "bad")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.startswith("error: 1 validation error for SyncRunRequest")


def test_a_refusal_is_on_stderr_only(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")

    result = _invoke(clone, *_backfill_args(**{"--boundary-commit": "f" * 40}))

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.startswith(f"error: cannot resolve commit '{'f' * 40}'")


def test_a_warning_is_on_stderr_and_carries_its_prefix(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "runner")

    result = _invoke(clone, "record-sync-run", "--sync-run", "79")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr.startswith("warning: attestations under temporal parser version")


def test_a_report_is_on_stdout_with_no_prefix(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")

    result = _invoke(clone, "record-sync-run", "--sync-run", "77")

    assert result.exit_code == 0
    assert result.stderr == ""
    assert result.stdout.startswith("gate epoch recorded and pushed\n{\n")
    assert json.loads(result.stdout[result.stdout.index("{") :])["evidence"] == (
        "lovspor sync run 77"
    )


# ---------- what each report says it did ----------


def test_reports_say_whether_they_wrote(tmp_path: Path) -> None:
    origin, shas = _origin(tmp_path, gate_ran=True)
    clone = _writable_clone(origin, tmp_path / "operator")
    request = {"epoch_at": EPOCH_AT, "boundary_commit": shas[0], "sync_run": "1"}
    now = datetime(2026, 9, 27, tzinfo=UTC)

    dry = backfill_epoch(clone, BackfillRequest.model_validate(request), now)
    applied = backfill_epoch(clone, BackfillRequest.model_validate({**request, "apply": True}), now)
    again = backfill_epoch(clone, BackfillRequest.model_validate({**request, "apply": True}), now)

    assert (dry.written, applied.written, again.written) == (False, True, False)
    assert not any(report.warning for report in (dry, applied, again))


def test_sync_run_reports_say_whether_they_wrote(tmp_path: Path) -> None:
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    now = datetime.now(UTC)

    first = record_sync_run_epoch(clone, SyncRunRequest(sync_run="5"), now)
    second = record_sync_run_epoch(clone, SyncRunRequest(sync_run="6"), now)

    assert (first.written, second.written) == (True, False)
    assert (first.warning, second.warning) == (False, False)


def test_repository_root_is_the_top_level_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standing inside a plain directory, git fails and prints nothing: an
    empty top-level must not resolve to the working directory and pass."""
    origin, _shas = _origin(tmp_path, gate_ran=False)
    clone = _writable_clone(origin, tmp_path / "runner")
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.chdir(plain)

    assert is_repository_root(clone) is True
    assert is_repository_root(clone / "lover") is False
    assert is_repository_root(plain) is False
    assert is_repository_root(tmp_path / "missing") is False
    assert is_repository_root(clone / "manifest.json") is False
