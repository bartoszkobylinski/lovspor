"""#792: a release candidate is the gate's output under a signed owner approval, or nothing.

Before this, `manifest --cache` described any file whose current record said
`release_candidate`, so a hand edit of `research_only` was enough. Now a
release_candidate manifest needs the approval proof directory, the approval must
carry a detached SSH signature by a trusted signer, and the gate is replayed:
`promote(approved input, approval, capture)` must give the file byte for byte.
"""

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from lovspor.cli import app
from lovspor.crosswalk import approval as approvals
from lovspor.crosswalk import commands
from lovspor.crosswalk.build import BuildOptions, build
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.commands import manifest_impl, release_candidate_impl
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.manifest import NO_PROOF, build_manifest
from lovspor.crosswalk.release import Approval, promote
from tests.unit.crosswalk_signing import (
    PRINCIPAL,
    Signing,
    approval_body,
    approval_dir,
    new_signer,
    sign,
    trust,
)
from tests.unit.crosswalk_support import PRODUCER, make_capture


@pytest.fixture
def capture_dir(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return make_capture(tmp_path, httpx_mock)


@pytest.fixture
def built(capture_dir: Path) -> bytes:
    return build(capture_dir, BuildOptions(producer_commit=PRODUCER))


@pytest.fixture
def signer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Signing:
    made = new_signer(tmp_path / "keys")
    trust(monkeypatch, made)
    return made


@pytest.fixture
def proof(tmp_path: Path, built: bytes, signer: Signing) -> Path:
    return approval_dir(tmp_path / "proof", built, signer)


@pytest.fixture
def candidate(built: bytes, capture_dir: Path) -> bytes:
    return promote(built, Approval.model_validate(approval_body(built)), capture_dir)[0]


def _write(tmp_path: Path, data: bytes, name: str = "rc.jsonl") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _hand_edited(built: bytes) -> bytes:
    records = parse_records(built)
    next(r for r in records if r["status"] == "attested")["release_state"] = "release_candidate"
    return serialize_records(records)


def _refused(data: Path, tmp_path: Path, cache: Path, proof: Path | None) -> str:
    output = tmp_path / "manifest.json"
    with pytest.raises(CrosswalkError) as caught:
        manifest_impl(data, output, cache, proof)
    assert not output.exists()
    return str(caught.value)


def test_a_hand_edited_release_candidate_gets_no_manifest(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """The #792 reproduction: research_only edited to release_candidate, no approval."""
    data = _write(tmp_path, _hand_edited(built))
    assert _refused(data, tmp_path, capture_dir, None) == NO_PROOF


def test_a_hand_edited_candidate_is_refused_even_with_a_valid_signed_approval(
    built: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    data = _write(tmp_path, _hand_edited(built))
    assert _refused(data, tmp_path, capture_dir, proof) == (
        "release candidates are not the gate's output for this approval and input"
    )


def test_the_gate_output_gets_a_manifest_naming_its_approval(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path, signer: Signing
) -> None:
    data, output = _write(tmp_path, candidate), tmp_path / "manifest.json"
    line = manifest_impl(data, output, capture_dir, proof)
    assert line == f"wrote the manifest of rc.jsonl (release_candidate) to {output}"
    approval = json.loads(output.read_text(encoding="utf-8"))["approval"]
    approval_file = proof / approvals.APPROVAL_FILE
    assert approval == {
        "approved_input_sha256": approval_body(
            (proof / approvals.APPROVED_INPUT_FILE).read_bytes()
        )["input_sha256"],
        "approval_sha256": _sha(approval_file.read_bytes()),
        "signature_sha256": _sha(approvals.signature_path(approval_file).read_bytes()),
        "signature_namespace": "lovspor-crosswalk-approval",
        "signer_principal": PRINCIPAL,
        "trusted_signers_sha256": _sha(signer.signers.read_bytes()),
    }


def _sha(data: bytes) -> str:
    return sha256(data).hexdigest()


def test_a_research_manifest_is_unchanged_and_carries_no_approval(
    built: bytes, capture_dir: Path
) -> None:
    assert "approval" not in build_manifest(built, "x", capture_dir)


def test_a_missing_approval_file_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    (proof / approvals.APPROVAL_FILE).unlink()
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof).startswith("approval unreadable")


def test_a_missing_signature_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    approvals.signature_path(proof / approvals.APPROVAL_FILE).unlink()
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof).startswith("approval signature unreadable")


def test_an_approval_modified_after_signing_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    approval = proof / approvals.APPROVAL_FILE
    approval.write_text(approval.read_text(encoding="utf-8").replace("synthetic", "forged"))
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof) == (
        "approval signature refused: not by a trusted signer"
    )


def test_a_signature_by_an_untrusted_key_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    stranger = new_signer(tmp_path / "stranger", "stranger")
    approval = proof / approvals.APPROVAL_FILE
    approvals.signature_path(approval).unlink()
    sign(stranger, approval)
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof).startswith("approval signature refused")


def test_a_signature_in_another_namespace_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path, signer: Signing
) -> None:
    approval = proof / approvals.APPROVAL_FILE
    approvals.signature_path(approval).unlink()
    sign(signer, approval, namespace="git")
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof).startswith("approval signature refused")


def test_the_shipped_trusted_signers_file_names_no_key(tmp_path: Path) -> None:
    """Release stays blocked until the owner adds their own key in a reviewed change."""
    shipped = Path(approvals.__file__).with_name("approval-signers")
    lines = [line.strip() for line in shipped.read_text(encoding="utf-8").splitlines()]
    assert [line for line in lines if line and not line.startswith("#")] == []
    message = _write(tmp_path, b"{}", "approval.json")
    with pytest.raises(CrosswalkError) as caught:
        approvals.verify_signature(b"{}", approvals.signature_path(message), shipped)
    assert str(caught.value) == approvals.NO_SIGNER


def test_the_gate_refuses_an_approval_nobody_trusted_signed(
    built: bytes, capture_dir: Path, tmp_path: Path
) -> None:
    """With the shipped signers file, release-candidate refuses before promoting anything."""
    proof = approval_dir(tmp_path / "proof", built, new_signer(tmp_path / "keys"))
    data, output = _write(tmp_path, built, "in.jsonl"), tmp_path / "out.jsonl"
    with pytest.raises(CrosswalkError) as caught:
        release_candidate_impl(data, proof / approvals.APPROVAL_FILE, capture_dir, output)
    assert str(caught.value) == approvals.NO_SIGNER
    assert not output.exists()


def test_the_gate_promotes_under_a_trusted_signed_approval(
    built: bytes, candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    data, output = _write(tmp_path, built, "in.jsonl"), tmp_path / "out.jsonl"
    release_candidate_impl(data, proof / approvals.APPROVAL_FILE, capture_dir, output)
    assert output.read_bytes() == candidate


def test_an_approval_for_other_input_bytes_is_refused(
    built: bytes, candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    # Another research_only file: the approval binds the bytes, not the shape.
    other = serialize_records(parse_records(built)[1:])
    (proof / approvals.APPROVED_INPUT_FILE).write_bytes(other)
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof) == (
        "the approval names other bytes than this input"
    )


def test_an_altered_history_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    """A rewritten approved revision: the gate never produced these bytes."""
    records = parse_records(candidate)
    first = next(r for r in records if r["release_state"] == "research_only")
    first["source_pins"]["producer_commit"] = "c" * 40
    data = _write(tmp_path, serialize_records(records))
    assert _refused(data, tmp_path, capture_dir, proof) == (
        "release candidates are not the gate's output for this approval and input"
    )


@pytest.mark.parametrize("where", ["evidence", "pin"])
def test_a_candidate_with_drift_or_a_wrong_source_pin_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path, where: str
) -> None:
    records = parse_records(candidate)
    target = next(r for r in records if r["release_state"] == "release_candidate")
    if where == "evidence":
        target["evidence"][0]["body_sha256"] = "f" * 64
    else:
        target["source_pins"]["lovdata_archives"][0]["sha256"] = "f" * 64
    data = _write(tmp_path, serialize_records(records))
    # The replay runs first and rebuilds from the capture, so drift can never match it.
    assert _refused(data, tmp_path, capture_dir, proof) == (
        "release candidates are not the gate's output for this approval and input"
    )


def test_a_proof_needs_a_capture(candidate: bytes, tmp_path: Path, proof: Path) -> None:
    data = _write(tmp_path, candidate)
    with pytest.raises(CrosswalkError) as caught:
        manifest_impl(data, tmp_path / "m.json", None, proof)
    assert str(caught.value) == (
        "--approval-proof replays the gate against a capture: give --cache"
    )


def test_a_proof_for_other_bytes_or_another_capture_is_refused(
    built: bytes, candidate: bytes, capture_dir: Path, proof: Path
) -> None:
    signed = approvals.signed_approval(
        (proof / approvals.APPROVAL_FILE).read_bytes(),
        Approval.model_validate(approval_body(built)),
        approvals.signature_path(proof / approvals.APPROVAL_FILE),
        approvals.TRUSTED_SIGNERS,
    )
    good = approvals.prove_candidates(candidate, built, signed, capture_dir)
    other_bytes = approvals.CandidateProof("0" * 64, good.capture_manifest_sha256, good.block)
    other_capture = approvals.CandidateProof(good.candidate_sha256, "0" * 64, good.block)
    with pytest.raises(
        CrosswalkError, match="^the approval proof is for other bytes than this file$"
    ):
        build_manifest(candidate, "x", capture_dir, other_bytes)
    with pytest.raises(
        CrosswalkError, match="^the approval proof was replayed against another capture$"
    ):
        build_manifest(candidate, "x", capture_dir, other_capture)
    assert build_manifest(candidate, "x", capture_dir, good)["approval"] == good.block


def test_the_candidate_keeps_the_approved_input_byte_identical(
    built: bytes, candidate: bytes
) -> None:
    """Append-only: every approved revision survives; candidates are new revisions only."""
    before = {(r["amending_lov_id"], r["record_revision"]) for r in parse_records(built)}
    after = parse_records(candidate)
    kept = [r for r in after if (r["amending_lov_id"], r["record_revision"]) in before]
    assert serialize_records(kept) == built
    added = [r for r in after if (r["amending_lov_id"], r["record_revision"]) not in before]
    assert added and {r["release_state"] for r in added} == {"release_candidate"}
    assert "released" not in {r["release_state"] for r in after}


def test_cli_manifest_refuses_a_hand_edited_candidate_and_writes_nothing(
    built: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    data, output = _write(tmp_path, _hand_edited(built)), tmp_path / "m.json"
    args = ["ops", "crosswalk", "manifest", "--input", str(data), "--output", str(output)]
    args += ["--cache", str(capture_dir), "--approval-proof", str(proof)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1
    assert "refused: release candidates are not the gate's output" in result.output
    assert not output.exists()


def test_cli_manifest_accepts_the_proven_gate_output(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    data, output = _write(tmp_path, candidate), tmp_path / "m.json"
    args = ["ops", "crosswalk", "manifest", "--input", str(data), "--output", str(output)]
    args += ["--cache", str(capture_dir), "--approval-proof", str(proof)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8"))["release_state"] == "release_candidate"


def test_signed_candidate_manifest_is_byte_deterministic(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    """The manifest has no wall clock, including its new approval block."""
    data = _write(tmp_path, candidate)
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    manifest_impl(data, first, capture_dir, proof)
    manifest_impl(data, second, capture_dir, proof)
    assert first.read_bytes() == second.read_bytes()


def test_a_missing_approved_input_is_refused(
    candidate: bytes, capture_dir: Path, tmp_path: Path, proof: Path
) -> None:
    (proof / approvals.APPROVED_INPUT_FILE).unlink()
    assert _refused(_write(tmp_path, candidate), tmp_path, capture_dir, proof).startswith(
        "approved input unreadable"
    )


def test_an_oversized_signature_is_refused_before_verification(
    built: bytes,
    capture_dir: Path,
    tmp_path: Path,
    proof: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signature = approvals.signature_path(proof / approvals.APPROVAL_FILE)
    signature.write_bytes(b"x" * (approvals.MAX_SIGNATURE_BYTES + 1))

    def unexpected_verification(*args: object, **kwargs: object) -> str:
        pytest.fail("oversized signatures must not reach ssh-keygen")

    monkeypatch.setattr(approvals, "_ssh_keygen", unexpected_verification)
    output = tmp_path / "out.jsonl"
    with pytest.raises(CrosswalkError, match="approval signature too large"):
        release_candidate_impl(
            _write(tmp_path, built), proof / approvals.APPROVAL_FILE, capture_dir, output
        )
    assert not output.exists()


@pytest.mark.parametrize("stage", ["find-principals", "verify"])
@pytest.mark.parametrize("failure", ["missing", "timeout"])
def test_cli_gate_refuses_when_signature_verifier_cannot_run(
    built: bytes,
    capture_dir: Path,
    tmp_path: Path,
    proof: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    failure: str,
) -> None:
    real_run = subprocess.run

    def unavailable(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if argv[:3] == ["ssh-keygen", "-Y", stage]:
            if failure == "missing":
                raise FileNotFoundError("ssh-keygen unavailable")
            raise subprocess.TimeoutExpired(argv, 30)
        return real_run(argv, **kwargs)

    monkeypatch.setattr(approvals.subprocess, "run", unavailable)
    data, output = _write(tmp_path, built), tmp_path / "out.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "ops",
            "crosswalk",
            "release-candidate",
            "--input",
            str(data),
            "--approval",
            str(proof / approvals.APPROVAL_FILE),
            "--cache",
            str(capture_dir),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "refused: approval signature could not be checked: ssh-keygen" in result.output
    assert not output.exists()


def test_proof_replay_checks_capture_manifest(
    built: bytes, candidate: bytes, capture_dir: Path, signer: Signing, proof: Path
) -> None:
    approval_file = proof / approvals.APPROVAL_FILE
    signed = approvals.signed_approval(
        approval_file.read_bytes(),
        Approval.model_validate(approval_body(built)),
        approvals.signature_path(approval_file),
        signer.signers,
    )
    # Replay must read the capture, not merely hash its manifest as opaque bytes.
    (capture_dir / "capture-manifest.json").write_bytes(b"{")
    with pytest.raises(CrosswalkError) as caught:
        approvals.prove_candidates(candidate, built, signed, capture_dir)
    assert str(caught.value) == f"capture manifest unreadable in {capture_dir}"


@pytest.mark.parametrize("command", ["manifest", "release-candidate"])
@pytest.mark.parametrize("damage", ["empty", "garbage", "truncated", "reformatted"])
def test_cli_requires_the_exact_signed_approval_bytes(
    built: bytes,
    candidate: bytes,
    capture_dir: Path,
    tmp_path: Path,
    proof: Path,
    command: str,
    damage: str,
) -> None:
    """The documented detached signature covers exact bytes, not parsed JSON."""
    approval = proof / approvals.APPROVAL_FILE
    signature = approvals.signature_path(approval)
    if damage == "reformatted":
        original = json.loads(approval.read_bytes())
        approval.write_text(json.dumps(original, indent=2), encoding="utf-8")
        assert json.loads(approval.read_bytes()) == original
    else:
        broken = {"empty": b"", "garbage": b"not an SSH signature\n"}
        signature.write_bytes(broken.get(damage, signature.read_bytes()[:40]))

    data = _write(tmp_path, candidate if command == "manifest" else built)
    output = tmp_path / "output.json"
    args = ["ops", "crosswalk", command, "--input", str(data), "--output", str(output)]
    args += ["--cache", str(capture_dir)]
    args += (
        ["--approval-proof", str(proof)]
        if command == "manifest"
        else [
            "--approval",
            str(approval),
        ]
    )
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1, result.output
    # This is a plain typer.echo refusal, rather than a Rich usage panel.
    assert "refused: approval signature refused" in result.output
    assert not output.exists()


@pytest.mark.parametrize("name", [approvals.APPROVAL_FILE, approvals.APPROVED_INPUT_FILE])
def test_manifest_bounds_each_proof_input_before_replay(
    candidate: bytes,
    capture_dir: Path,
    tmp_path: Path,
    proof: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    """The new regular-file reader must retain the command's input size limits."""
    if name == approvals.APPROVAL_FILE:
        limit = commands.MAX_APPROVAL_BYTES
        label = "approval"
    else:
        # Keep the candidate readable while bounding the separate approved input.
        limit = len(candidate)
        monkeypatch.setattr(commands, "MAX_PREVIOUS_BYTES", limit)
        label = "approved input"
    path = proof / name
    path.write_bytes(b" " * (limit + 1))
    data = _write(tmp_path, candidate)
    assert _refused(data, tmp_path, capture_dir, proof) == f"{label} too large: {path}"


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b"{}"])
def test_a_trusted_signature_does_not_make_an_invalid_approval_valid(
    candidate: bytes,
    capture_dir: Path,
    tmp_path: Path,
    proof: Path,
    signer: Signing,
    raw: bytes,
) -> None:
    """Signed proof inputs still obey the existing Approval JSON/schema contract."""
    approval = proof / approvals.APPROVAL_FILE
    approval.write_bytes(raw)
    approvals.signature_path(approval).unlink()
    sign(signer, approval)
    error = _refused(_write(tmp_path, candidate), tmp_path, capture_dir, proof)
    assert error.startswith("approval refused: invalid ")
