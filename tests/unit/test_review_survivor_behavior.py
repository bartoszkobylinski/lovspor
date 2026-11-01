"""Observable diagnostics, canonical bindings and review output regressions."""

import hashlib
import json
import linecache
import os
import pty
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from lovspor.forarbeider import review_commands as commands
from lovspor.forarbeider import review_ledger as ledger
from lovspor.forarbeider import staging
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.review_models import (
    ReviewDecision,
    ReviewReason,
    ReviewStatus,
    subject_key,
    subject_of,
)
from tests.unit.forarbeider_review_support import chapter, saved, withheld
from tests.unit.test_forarbeider_review_ledger import request
from tests.unit.test_forarbeider_review_models import approved


def detail(action: Callable[[], object], expected: str) -> None:
    with pytest.raises(ForarbeiderRefusedError) as caught:
        action()
    assert caught.value.detail == expected


def test_staging_refusals_name_the_real_path(tmp_path: Path) -> None:
    detail(lambda: staging.checked_directory(Path("relative"), ()), "relative")
    path = tmp_path / "shared"
    path.mkdir(mode=0o755)
    detail(lambda: staging.private_directory_exists(path), str(path))
    missing = tmp_path / "absent" / "child"
    detail(lambda: staging.ensure_private_directory(missing), str(missing))
    detail(lambda: staging.read_private(missing, 10), str(missing))
    data = tmp_path / "data"
    data.write_bytes(b"abc")
    data.chmod(0o600)
    detail(lambda: staging.read_private(data, 2), str(data))
    data.chmod(0o644)
    detail(lambda: staging.read_private(data, 3), str(data))


def scope(tmp_path: Path) -> commands.ReviewScope:
    return commands.ReviewScope(
        ledger=tmp_path / "ledger", reviewer="reviewer-1", decided_at="2100-04-02T12:00:00Z"
    )


def test_observation_and_candidate_refusals_name_paths(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{")
    bad.chmod(0o600)
    detail(lambda: commands.read_observation(bad, ()), str(bad))
    missing = tmp_path / "missing"
    detail(lambda: commands.candidates_impl(scope(tmp_path), missing), str(missing))


def test_pins_and_ineligible_approval_name_the_subject(tmp_path: Path) -> None:
    result = withheld(chapter())
    path = saved(result, tmp_path / "observation.json")
    current = scope(tmp_path)
    pin = commands.Pinned(observation=path, expect="0" * 64 + "@0")
    entry = commands.entry_of(ReviewStatus.HELD, ["awaiting_evidence=issue:738"])
    detail(lambda: commands.decide_impl(current, pin, entry), "0" * 64)
    pin = pin.model_copy(update={"expect": subject_key(subject_of(result)) + "@0"})
    attest = tuple(f"{kind.value}=adr:0018" for kind in commands.ALL_ATTESTATIONS)
    detail(lambda: commands.approve_impl(current, pin, attest), result.id)
    assert not current.ledger.exists()


def test_review_output_has_exact_lines_and_separators(tmp_path: Path) -> None:
    current = scope(tmp_path)
    observations = tmp_path / "observations"
    observations.mkdir(mode=0o700)
    result = withheld(chapter())
    first = saved(result, observations / "a.json")
    second = saved(result, observations / "b.json")
    key = subject_key(subject_of(result))
    reasons = ",".join(result.hold_reasons)
    assert commands.candidates_impl(current, observations).splitlines() == [
        f"{result.id} status pending revision 0 reasons {reasons} --expect {key}@0 {path}"
        for path in (first, second)
    ]
    pin = commands.Pinned(observation=first, expect=key + "@0")
    entry = commands.entry_of(
        ReviewStatus.HELD, ["awaiting_evidence=issue:738", "third_party_work=adr:0018"]
    )
    commands.decide_impl(current, pin, entry)
    assert (
        commands.decide_impl(current, pin, entry) == f"unchanged {result.id}: revision 1 "
        "(a review record, not permission to publish or crawl)"
    )
    assert commands.show_impl(current, first).splitlines() == [
        result.id,
        f"source_page_url {result.source_page_url}",
        f"source_sha256 {result.source_sha256}",
        "text_sha256 none",
        f"extractor {result.extractor_version} normalizer {result.normalizer_version}",
        f"hold_reasons {reasons}",
        "status held",
        f"--expect {key}@1",
        "revision 1 held reviewer-1 2100-04-02T12:00:00Z "
        "awaiting_evidence,third_party_work adr:0018,issue:738",
    ]


def test_refused_cli_writes_only_stderr(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        commands.review_app,
        [
            "--ledger",
            str(tmp_path / "ledger"),
            "candidates",
            "--staging",
            str(tmp_path / "missing"),
        ],
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"refused: input_unreadable: {tmp_path / 'missing'}\n"
    assert not (tmp_path / "ledger").exists()


def test_noninteractive_approval_names_the_operation() -> None:
    detail(lambda: commands._require_a_person("approve synthetic"), "approval")


@pytest.mark.parametrize("damage", ["invalid", "format", "chain", "foreign"])
def test_corruption_names_the_damaged_record_or_directory(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "ledger"
    outcome = ledger.record_decision(root, request())
    directory = root / subject_key(outcome.stored.decision.subject)
    path = directory / "000001.json"
    if damage == "invalid":
        path.write_bytes(b"{")
    elif damage == "format":
        path.write_bytes(path.read_bytes() + b"\n")
    elif damage == "chain":
        target = directory / "000002.json"
        path.rename(target)
    else:
        directory = root / "foreign"
        directory.mkdir(mode=0o700)
    expected = str(path) if damage in ("invalid", "format") else str(directory)
    detail(lambda: ledger.verify_ledger(root), expected)


def test_conflict_and_transition_details(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    detail(lambda: ledger.record_decision(root, request(expected_revision=1)), "expected revision")
    detail(
        lambda: ledger.record_decision(
            root, request(status=ReviewStatus.PENDING, reason_codes=(ReviewReason.REOPENED,))
        ),
        "pending",
    )


def test_subject_digest_uses_the_canonical_json_layout() -> None:
    subject = subject_of(chapter())
    canonical = (
        json.dumps(subject.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False)
        + "\n"
    )
    # The subject digest binds JSON itself; the record's trailing newline is separate.
    assert subject_key(subject) == hashlib.sha256(canonical[:-1].encode()).hexdigest()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"attestations": ()}, "an approval needs every attestation, once each"),
        (
            {"reason_codes": (ReviewReason.AWAITING_EVIDENCE,)},
            "an approval states that its attestations are complete",
        ),
        (
            {"subject": subject_of(withheld(chapter()))},
            "a chapter without a body cannot be approved",
        ),
    ],
)
def test_incomplete_approval_explains_the_failed_requirement(
    changes: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError) as caught:
        ReviewDecision(**approved(**changes))
    assert caught.value.errors()[0]["msg"] == "Value error, " + message


def test_terminal_confirmation_prints_the_phrase() -> None:

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from lovspor.forarbeider.review_commands import _require_a_person; "
                '_require_a_person("approve synthetic")',
            ],
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        os.write(master, b"wrong\n")
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode != 0
        assert b"Type to confirm: approve synthetic" in stdout
        assert b"review_not_interactive: approval" in stderr
    finally:
        os.close(master)
        os.close(slave)


def test_missing_time_is_refused_without_writing(tmp_path: Path) -> None:
    current = scope(tmp_path).model_copy(update={"decided_at": None})
    result = chapter()
    path = saved(result, tmp_path / "observation.json")
    pin = commands.Pinned(observation=path, expect=subject_key(subject_of(result)) + "@0")
    entry = commands.entry_of(ReviewStatus.HELD, ["awaiting_evidence=issue:738"])
    with pytest.raises(ForarbeiderRefusedError) as caught:
        commands.decide_impl(current, pin, entry)
    assert caught.value.reason.value == "invalid_timestamp"
    assert caught.value.detail == ""
    assert not current.ledger.exists()


def test_attestation_with_extra_equals_is_refused(tmp_path: Path) -> None:
    result = chapter()
    path = saved(result, tmp_path / "observation.json")
    pin = commands.Pinned(observation=path, expect=subject_key(subject_of(result)) + "@0")
    detail(
        lambda: commands.approve_impl(scope(tmp_path), pin, ("privacy_basis=adr:0018=extra",)),
        "attest",
    )


def test_directory_probe_does_not_depend_on_child_name(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for name in ("child", "CHILD", "XXchildXX"):
        (root / name).mkdir(exist_ok=True)
        (root / name / ".git").mkdir(exist_ok=True)
    assert staging.checked_directory(root, ()) == root
    detail(lambda: staging.checked_directory(root, (root,)), str(root))


def test_subject_dump_modes_have_the_same_json_values() -> None:
    subject = subject_of(chapter())
    for mode in (None, "XXjsonXX", "JSON"):
        assert subject.model_dump(mode=mode) == subject.model_dump(mode="json")


def during_line(
    function: Callable[[], object], filename: str, line_text: str, edit: Callable[[], None]
) -> object:
    """Synchronize a real concurrent file edit without replacing application code.

    A trace hook pauses at a race window; only bytes on disk are changed.
    """

    edited = False

    def trace(frame: FrameType, event: str, arg: object) -> object:
        nonlocal edited
        if (
            not edited
            and event == "line"
            and frame.f_code.co_filename.endswith(filename)
            and line_text in linecache.getline(frame.f_code.co_filename, frame.f_lineno)
        ):
            edited = True
            edit()
        return trace

    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        result = function()
    finally:
        sys.settrace(previous)
    assert edited, "The file-edit race window must be reached"
    return result


def test_private_read_caps_a_file_that_grows_after_stat(tmp_path: Path) -> None:
    path = tmp_path / "growing"
    path.write_bytes(b"abc")
    path.chmod(0o600)
    result = during_line(
        lambda: staging.read_private(path, 3),
        "staging.py",
        "return stream.read(",
        lambda: path.write_bytes(b"abcdef"),
    )
    assert result == b"abc"
    assert path.read_bytes() == b"abcdef"


def test_concurrent_ledger_writer_conflict_names_the_record(tmp_path: Path) -> None:
    root = tmp_path / "ledger"
    path = root / subject_key(request().subject) / "000001.json"

    def competing_writer() -> None:
        path.write_bytes(b"competing writer")
        path.chmod(0o600)

    detail(
        lambda: during_line(
            lambda: ledger.record_decision(root, request()),
            "review_ledger.py",
            "written = write_observation(",
            competing_writer,
        ),
        str(path),
    )
    assert path.read_bytes() == b"competing writer"
