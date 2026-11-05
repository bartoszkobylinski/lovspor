"""Observable approval boundary behavior for the listed surviving mutations."""

import locale
import os
import tracemalloc
from pathlib import Path

import pytest

from lovspor.crosswalk.approval import (
    MAX_SIGNATURE_BYTES,
    _read_signature,
    _signer_lines,
    _ssh_keygen,
    verify_signature,
)
from lovspor.crosswalk.errors import CrosswalkError
from tests.unit.crosswalk_signing import PRINCIPAL, new_signer, sign


def verifier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    """Replace the external executable, leaving the Python verification code real."""
    executable = tmp_path / "ssh-keygen"
    executable.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])


@pytest.mark.parametrize(
    "principal", ["eier-æ@example.invalid", "eier-\u65e5\u672c@example.invalid"]
)
def test_signers_are_utf8_even_in_ascii_locale(tmp_path: Path, principal: str) -> None:
    signers = tmp_path / "signers"
    signers.write_bytes(f"# trusted owners\n\n {principal} key \n".encode())
    previous = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
        assert _signer_lines(signers) == [f"{principal} key"]
    finally:
        locale.setlocale(locale.LC_CTYPE, previous)


@pytest.mark.parametrize("content", [None, b"\xff"])
def test_unreadable_signers_name_the_path(tmp_path: Path, content: bytes | None) -> None:
    signers = tmp_path / "signers"
    if content is not None:
        signers.write_bytes(content)
    with pytest.raises(CrosswalkError) as caught:
        _signer_lines(signers)
    assert str(caught.value) == f"trusted signers unreadable: {signers}"


def test_verifier_replaces_invalid_output_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    verifier(tmp_path, monkeypatch, "printf '\\377'")
    assert _ssh_keygen(["find-principals"], b"") == "\ufffd"


@pytest.mark.parametrize("exit_code", [1, 2, 255])
def test_verifier_refuses_a_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    verifier(tmp_path, monkeypatch, f"printf 'untrusted signer'; exit {exit_code}")
    with pytest.raises(CrosswalkError) as caught:
        _ssh_keygen(["find-principals"], b"")
    assert str(caught.value) == "approval signature refused: not by a trusted signer"


@pytest.mark.parametrize("principals", ["", "one two"])
def test_signature_requires_exactly_one_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, principals: str
) -> None:
    verifier(tmp_path, monkeypatch, f"printf '%s' '{principals}'")
    signers = tmp_path / "signers"
    signers.write_text("owner key\n", encoding="utf-8")
    with pytest.raises(CrosswalkError) as caught:
        verify_signature(b"message", tmp_path / "signature", signers)
    assert str(caught.value) == "approval signature refused: not exactly one trusted signer"


@pytest.mark.parametrize(
    "size",
    [
        0,
        MAX_SIGNATURE_BYTES,
        MAX_SIGNATURE_BYTES + 1,
        MAX_SIGNATURE_BYTES + 2,
        MAX_SIGNATURE_BYTES + 4096,
    ],
)
def test_signature_size_boundary(tmp_path: Path, size: int) -> None:
    signature = tmp_path / "signature"
    data = b"x" * size
    signature.write_bytes(data)
    if size <= MAX_SIGNATURE_BYTES:
        assert _read_signature(signature) == data
    else:
        with pytest.raises(CrosswalkError) as caught:
            _read_signature(signature)
        assert str(caught.value) == f"approval signature too large: {signature}"


def test_oversized_signature_is_not_loaded_whole(tmp_path: Path) -> None:
    signature = tmp_path / "signature"
    with signature.open("wb") as stream:
        stream.truncate(8 * 1024 * 1024)
    tracemalloc.start()
    try:
        with pytest.raises(CrosswalkError, match="approval signature too large"):
            _read_signature(signature)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # A generous allocation bound distinguishes a capped read from loading the
    # whole 8 MiB file, without pinning the stream's read() call arguments.
    assert peak < 1024 * 1024


@pytest.mark.parametrize("data", [b"", b"approval bytes", b"\x00\xff\n" * 1024])
def test_real_signature_verification(tmp_path: Path, data: bytes) -> None:
    signer = new_signer(tmp_path / "keys")
    message = tmp_path / "message"
    message.write_bytes(data)
    signature = sign(signer, message)
    assert verify_signature(message.read_bytes(), signature, signer.signers).principal == PRINCIPAL
    with pytest.raises(CrosswalkError, match="approval signature refused"):
        verify_signature(data + b"tampered", signature, signer.signers)
