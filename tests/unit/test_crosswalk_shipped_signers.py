"""The shipped trusted signers file names exactly the owner's approved key (#840).

This replaces the tripwire that required `approval-signers` to be empty. The owner
added their own public key in a change they made; these tests pin it. A second
signer, another principal, a missing or wider namespace, any other option or one
changed byte of the key turns them red, so a new trusted signer can arrive only
in a reviewed change to this pin as well.

The empty-file refusal (NO_SIGNER) keeps its own isolated test, which writes its
own signers file (#841). No private key is read here: the shipped line is public,
and every signing key below is a throwaway made in the test's temporary directory.
"""

import base64
import subprocess
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

from lovspor.crosswalk import approval as approvals
from lovspor.crosswalk.errors import CrosswalkError
from tests.unit.crosswalk_signing import Signing, new_signer, sign


@dataclass(frozen=True)
class ApprovedSigner:
    principal: str
    options: str
    key_type: str
    fingerprint: str


APPROVED = ApprovedSigner(
    principal="lovspor-owner",
    options=f'namespaces="{approvals.NAMESPACE}"',
    key_type="ssh-ed25519",
    fingerprint="SHA256:e6tAm1h5TbLWA1ob+gT5p9ttREUcR8koFa8QBNPy+gk",
)
ED25519_KEY_BYTES = 32
SHIPPED = approvals.TRUSTED_SIGNERS


def _wire_strings(blob: bytes) -> list[bytes]:
    """The SSH wire-format strings (uint32 length + bytes) a public key blob is made of."""
    fields, at = [], 0
    while at < len(blob):
        if at + 4 > len(blob):
            raise ValueError("truncated length")
        size = int.from_bytes(blob[at : at + 4], "big")
        at += 4
        if at + size > len(blob):
            raise ValueError("truncated field")
        fields.append(blob[at : at + size])
        at += size
    return fields


def fingerprint(blob: bytes) -> str:
    """OpenSSH's SHA256 fingerprint of a public key blob, as `ssh-keygen -l` prints it."""
    return "SHA256:" + base64.b64encode(sha256(blob).digest()).decode().rstrip("=")


def _key_problems(key_type: str, encoded: str) -> list[str]:
    try:
        blob = base64.b64decode(encoded, validate=True)
        fields = _wire_strings(blob)
    except ValueError:
        return ["public key is not valid base64 SSH wire format"]
    if fields != [key_type.encode(), fields[-1]] or len(fields[-1]) != ED25519_KEY_BYTES:
        return ["public key blob is not a single ssh-ed25519 key"]
    if fingerprint(blob) != APPROVED.fingerprint:
        return [f"public key fingerprint {fingerprint(blob)} is not the approved one"]
    return []


def signer_problems(signers: Path) -> list[str]:
    """Why ``signers`` is not exactly the approved owner signer; empty when it is.

    Active lines are judged exactly as the production gate finds them
    (`approval._signer_lines`), so a line this check skips is one the gate skips too.
    """
    active = approvals._signer_lines(signers)
    if len(active) != 1:
        return [f"expected exactly one trusted signer, found {len(active)}"]
    fields = active[0].split()
    if len(fields) != 4:
        return [f"expected principal, options, key type and key, found {len(fields)} fields"]
    principal, options, key_type, encoded = fields
    wanted = (APPROVED.principal, APPROVED.options, APPROVED.key_type)
    if (principal, options, key_type) != wanted:
        return [f"unexpected principal, options or key type: {fields[:3]}"]
    return _key_problems(key_type, encoded)


def _shipped_line() -> str:
    return approvals._signer_lines(SHIPPED)[0]


def _variant(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "approval-signers"
    path.write_text(SHIPPED.read_text(encoding="utf-8") + text, encoding="utf-8")
    return path


def _replaced(tmp_path: Path, old: str, new: str) -> Path:
    """The shipped file with ``old`` replaced by ``new`` in its active line only.

    The comment header holds an example signer line, so a file-wide replace could
    change a comment and leave the real line untouched.
    """
    line = _shipped_line()
    assert old in line
    path = tmp_path / "approval-signers"
    shipped = SHIPPED.read_text(encoding="utf-8")
    path.write_text(shipped.replace(line, line.replace(old, new, 1)), encoding="utf-8")
    assert approvals._signer_lines(path) == [line.replace(old, new, 1)]
    return path


def _flip_one_key_char(line: str) -> str:
    encoded = line.split()[3]
    at = len(encoded) // 2
    swapped = "B" if encoded[at] != "B" else "C"
    return line.replace(encoded, encoded[:at] + swapped + encoded[at + 1 :])


def test_the_shipped_file_names_exactly_the_approved_owner_key() -> None:
    assert signer_problems(SHIPPED) == []


def test_the_shipped_namespace_is_pinned_independently_of_the_verifier() -> None:
    """The exact namespace promised in crosswalk-producer.md cannot drift with NAMESPACE."""
    assert _shipped_line().split()[1] == 'namespaces="lovspor-crosswalk-approval"'
    assert approvals.NAMESPACE == "lovspor-crosswalk-approval"


def test_the_pinned_fingerprint_is_the_one_ssh_keygen_prints(tmp_path: Path) -> None:
    """The owner compares the pin with `ssh-keygen -lf` on their .pub file: both agree."""
    public = tmp_path / "owner.pub"
    public.write_text(" ".join(_shipped_line().split()[2:]) + "\n", encoding="utf-8")
    done = subprocess.run(
        ["ssh-keygen", "-l", "-E", "sha256", "-f", str(public)],
        capture_output=True,
        check=True,
    )
    assert done.stdout.decode().split()[:2] == ["256", APPROVED.fingerprint]


OTHER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.mark.parametrize(
    ("appended", "problem"),
    [
        (f'intruder namespaces="{approvals.NAMESPACE}" {OTHER_KEY}\n', "found 2"),
        (f'lovspor-owner namespaces="{approvals.NAMESPACE}" {OTHER_KEY}\n', "found 2"),
        ("{line}\n", "found 2"),
        (f"intruder {OTHER_KEY}\n", "found 2"),
    ],
    ids=["another-principal", "second-owner-key", "duplicate-line", "unrestricted-extra"],
)
def test_an_additional_signer_is_rejected(tmp_path: Path, appended: str, problem: str) -> None:
    signers = _variant(tmp_path, appended.replace("{line}", _shipped_line()))
    assert any(problem in found for found in signer_problems(signers))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (f' namespaces="{approvals.NAMESPACE}"', ""),
        (f'namespaces="{approvals.NAMESPACE}"', 'namespaces="*"'),
        (f'namespaces="{approvals.NAMESPACE}"', f'namespaces="{approvals.NAMESPACE},git"'),
        (
            f'namespaces="{approvals.NAMESPACE}"',
            f'cert-authority,namespaces="{approvals.NAMESPACE}"',
        ),
        ("lovspor-owner ", "lovspor-owner,intruder "),
        ("lovspor-owner ", "*@example.invalid "),
        ("ssh-ed25519 ", "ssh-rsa "),
    ],
    ids=[
        "missing-namespace",
        "wildcard-namespace",
        "extra-namespace",
        "extra-option",
        "extra-principal",
        "wildcard-principal",
        "key-type-label",
    ],
)
def test_a_changed_principal_namespace_option_or_type_is_rejected(
    tmp_path: Path, old: str, new: str
) -> None:
    assert signer_problems(_replaced(tmp_path, old, new)) != []


def test_one_changed_character_of_the_key_is_detected(tmp_path: Path) -> None:
    line = _shipped_line()
    problems = signer_problems(_replaced(tmp_path, line, _flip_one_key_char(line)))
    assert any("public key" in found for found in problems)


@pytest.mark.parametrize(
    "encoded",
    ["not-base64!", "AAAAC3NzaC1lZDI1NTE5", "AAAAC3NzaC1lZDI1NTE5AAAAEAAAAAAAAAAAAAAAAAAAAAA="],
    ids=["not-base64", "truncated", "short-key"],
)
def test_a_malformed_key_is_rejected(tmp_path: Path, encoded: str) -> None:
    line = _shipped_line()
    problems = signer_problems(_replaced(tmp_path, line.split()[3], encoded))
    assert any("public key" in found for found in problems)


def test_a_trailing_comment_field_is_rejected(tmp_path: Path) -> None:
    line = _shipped_line()
    assert signer_problems(_replaced(tmp_path, line, line + " laptop")) != []


def test_a_file_without_an_active_signer_is_not_the_approved_file(tmp_path: Path) -> None:
    signers = tmp_path / "approval-signers"
    signers.write_text("# no key\n\n", encoding="utf-8")
    assert signer_problems(signers) == ["expected exactly one trusted signer, found 0"]


def test_a_signers_file_with_comments_only_still_means_no_signer(tmp_path: Path) -> None:
    """What the removed tripwire also checked, now on the test's own file (#841)."""
    signers = tmp_path / "approval-signers"
    signers.write_text("# no key\n\n   \n", encoding="utf-8")
    with pytest.raises(CrosswalkError) as caught:
        approvals.verify_signature(b"{}", tmp_path / "approval.json.sig", signers)
    assert str(caught.value) == approvals.NO_SIGNER


def _message(tmp_path: Path) -> Path:
    message = tmp_path / "approval.json"
    message.write_bytes(b"{}")
    return message


@pytest.mark.parametrize("use_default", [False, True], ids=["explicit-file", "default-file"])
def test_the_shipped_file_refuses_a_signature_by_any_other_key(
    tmp_path: Path, use_default: bool
) -> None:
    stranger = new_signer(tmp_path / "stranger", "stranger", "lovspor-owner")
    signature = sign(stranger, _message(tmp_path))
    with pytest.raises(CrosswalkError) as caught:
        if use_default:
            approvals.verify_signature(b"{}", signature)
        else:
            approvals.verify_signature(b"{}", signature, SHIPPED)
    assert str(caught.value) == "approval signature refused: not by a trusted signer"


def _beside_the_owner(tmp_path: Path, line_options: str) -> Signing:
    """A throwaway key trusted in a copy of the shipped file, after the owner's line."""
    made = new_signer(tmp_path / "keys", "helper")
    public = " ".join(made.key.with_name("helper.pub").read_text(encoding="utf-8").split()[:2])
    return Signing(made.key, _variant(tmp_path, f"helper {line_options}{public}\n"))


def test_the_owner_line_leaves_the_file_usable_by_ssh_keygen(tmp_path: Path) -> None:
    """A trusted signature verifies against the shipped line plus a throwaway one.

    If ssh-keygen could not read the owner's line, this verification would fail.
    """
    helper = _beside_the_owner(tmp_path, f'namespaces="{approvals.NAMESPACE}" ')
    signature = sign(helper, _message(tmp_path))
    assert approvals.verify_signature(b"{}", signature, helper.signers).principal == "helper"


@pytest.mark.parametrize(
    "line_options", [f'namespaces="{approvals.NAMESPACE}" ', ""], ids=["restricted", "unrestricted"]
)
def test_a_signature_in_another_namespace_stays_refused(tmp_path: Path, line_options: str) -> None:
    """Even a signer line with no namespace restriction cannot pass another namespace."""
    helper = _beside_the_owner(tmp_path, line_options)
    signature = sign(helper, _message(tmp_path), namespace="git")
    with pytest.raises(CrosswalkError) as caught:
        approvals.verify_signature(b"{}", signature, helper.signers)
    assert str(caught.value).startswith("approval signature refused")


@pytest.mark.parametrize("namespace", ["lovspor-crosswalk-approval", "git"])
def test_shipped_owner_options_enforce_the_approval_namespace(
    tmp_path: Path, namespace: str
) -> None:
    """Exercise the shipped principal/options with OpenSSH using only a temporary key.

    crosswalk-producer.md promises that the owner's line is restricted to the
    approval namespace. Replace only its public key in a scratch copy so this
    restriction is exercised without access to the owner's private key.
    """
    owner = new_signer(tmp_path / "owner", principal="lovspor-owner")
    public = owner.key.with_suffix(".pub").read_text(encoding="utf-8").split()[1]
    signers = _replaced(tmp_path, _shipped_line().split()[3], public)
    signature = sign(owner, _message(tmp_path), namespace=namespace)

    if namespace == "lovspor-crosswalk-approval":
        verified = approvals.verify_signature(b"{}", signature, signers)
        assert verified.principal == "lovspor-owner"
        assert verified.signers_sha256 == sha256(signers.read_bytes()).hexdigest()
    else:
        with pytest.raises(CrosswalkError, match="approval signature refused"):
            approvals.verify_signature(b"{}", signature, signers)
        # Check the allowed_signers restriction independently of the verifier's
        # fixed namespace: even requesting verification as "git" must fail.
        checked = subprocess.run(
            [
                "ssh-keygen",
                "-Y",
                "verify",
                "-f",
                str(signers),
                "-I",
                "lovspor-owner",
                "-n",
                namespace,
                "-s",
                str(signature),
            ],
            input=b"{}",
            capture_output=True,
            check=False,
        )
        assert checked.returncode != 0
