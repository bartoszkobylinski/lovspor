"""The documented private signature copy binds verification to the recorded digest."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk import approval as approvals
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.release import Approval
from tests.unit.crosswalk_signing import approval_body, new_signer, sign


@pytest.mark.parametrize("initially_valid", [True, False])
def test_signature_replacement_cannot_change_the_bytes_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initially_valid: bool
) -> None:
    """A replacement can neither invalidate a read signature nor rescue a bad one."""
    signer = new_signer(tmp_path / "keys")
    raw = json.dumps(approval_body(b"")).encode("utf-8")
    approval = Approval.model_validate_json(raw)
    message = tmp_path / "approval.json"
    message.write_bytes(raw)
    signature = sign(signer, message)
    valid = signature.read_bytes()
    invalid = b"not an SSH signature\n"
    original, replacement = (valid, invalid) if initially_valid else (invalid, valid)
    signature.write_bytes(original)
    real_verify = approvals.verify_signature
    copies: list[Path] = []

    def replace_then_verify(data: bytes, snapshot: Path, signers: Path) -> approvals.Signer:
        copies.append(snapshot)
        assert snapshot != signature
        assert snapshot.read_bytes() == original
        signature.write_bytes(replacement)
        return real_verify(data, snapshot, signers)

    monkeypatch.setattr(approvals, "verify_signature", replace_then_verify)
    if initially_valid:
        signed = approvals.signed_approval(raw, approval, signature, signer.signers)
        assert signed.signature_sha256 == sha256(original).hexdigest()
        assert signed.approval_sha256 == sha256(raw).hexdigest()
        assert signed.approval == approval
    else:
        with pytest.raises(CrosswalkError, match="approval signature refused"):
            approvals.signed_approval(raw, approval, signature, signer.signers)

    assert signature.read_bytes() == replacement
    assert len(copies) == 1
    assert not copies[0].parent.exists()
