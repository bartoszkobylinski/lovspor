"""Exercise the private signature copy through real SSH verification."""

from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk.approval import signed_approval
from lovspor.crosswalk.errors import CrosswalkError
from lovspor.crosswalk.release import Approval
from tests.unit.crosswalk_signing import PRINCIPAL, approval_body, new_signer, sign


def test_private_copy_preserves_signed_bytes_and_refuses_changed_message(tmp_path: Path) -> None:
    signer = new_signer(tmp_path / "keys")
    raw = b"exact approved bytes\n"
    message = tmp_path / "approval.json"
    message.write_bytes(raw)
    signature = sign(signer, message)
    signature_bytes = signature.read_bytes()
    approval = Approval.model_validate(approval_body(b"input"))

    result = signed_approval(raw, approval, signature, signer.signers)
    assert result.approval == approval
    assert result.approval_sha256 == sha256(raw).hexdigest()
    assert result.signature_sha256 == sha256(signature_bytes).hexdigest()
    assert result.signer.principal == PRINCIPAL
    assert result.signer.signers_sha256 == sha256(signer.signers.read_bytes()).hexdigest()
    with pytest.raises(CrosswalkError, match="approval signature refused"):
        signed_approval(raw + b"changed", approval, signature, signer.signers)
    assert signature.read_bytes() == signature_bytes
    assert message.read_bytes() == raw
