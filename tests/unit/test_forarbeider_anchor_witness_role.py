"""AR5a: the anchor witness is its own trust-root role, never the owner's or the bot's (#738)."""

from pathlib import Path

import pytest

from lovspor.forarbeider import policy_trust
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_trust import (
    POLICY_NAMESPACE,
    RELEASE_NAMESPACE,
    SignedBytes,
    check_signature,
)
from tests.unit.forarbeider_authorization_support import (
    CHECKED_AT,
    new_key,
    sign,
    signer_line,
    trust_root,
)

ANCHOR = "lovspor-forarbeider-anchor"


def test_the_anchor_namespace_is_the_witness_role(tmp_path: Path) -> None:
    witness = new_key(tmp_path / "k", "witness")
    root = trust_root(tmp_path / "r", [signer_line("test-witness", witness, ANCHOR)])

    assert policy_trust.ANCHOR_NAMESPACE == ANCHOR
    assert root.entries[0].role == "anchor_witness"
    signed = SignedBytes(b"anchor\n", sign(witness, b"anchor\n", ANCHOR))
    assert check_signature(root, signed, ANCHOR, CHECKED_AT).verified


@pytest.mark.parametrize("other", [POLICY_NAMESPACE, RELEASE_NAMESPACE])
def test_a_witness_key_holding_another_role_refuses_the_root(tmp_path: Path, other: str) -> None:
    key = new_key(tmp_path / "k", "mixed")

    with pytest.raises(ForarbeiderRefusedError):
        trust_root(tmp_path / "r", [signer_line("test-mixed", key, f"{ANCHOR},{other}")])


@pytest.mark.parametrize("namespace", [POLICY_NAMESPACE, RELEASE_NAMESPACE])
def test_an_owner_or_bot_key_cannot_sign_an_anchor(tmp_path: Path, namespace: str) -> None:
    key = new_key(tmp_path / "k", "other")
    witness = new_key(tmp_path / "k", "witness")
    lines = [
        signer_line("test-other", key, namespace),
        signer_line("test-witness", witness, ANCHOR),
    ]
    root = trust_root(tmp_path / "r", lines)

    check = check_signature(
        root, SignedBytes(b"a\n", sign(key, b"a\n", ANCHOR)), ANCHOR, CHECKED_AT
    )

    assert not check.verified
    assert check.reason == "signer_role_not_permitted"
