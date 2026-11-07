"""The external SSH verifier has a finite process timeout."""

from pathlib import Path

import pytest

from lovspor.crosswalk.approval import _ssh_keygen
from lovspor.crosswalk.errors import CrosswalkError
from tests.unit.test_crosswalk_approval_mutants import verifier


def test_verifier_enforces_its_thirty_second_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # approval.py's _SSH_KEYGEN_TIMEOUT specifies 30 s. The real process finishes
    # after 31 s even if timeout enforcement is removed, so this cannot hang.
    verifier(tmp_path, monkeypatch, "exec sleep 31")
    with pytest.raises(CrosswalkError) as caught:
        _ssh_keygen(["find-principals"], b"")
    assert str(caught.value) == "approval signature could not be checked: ssh-keygen"
