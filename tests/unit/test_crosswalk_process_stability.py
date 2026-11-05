"""Crosswalk canonical bytes and identity pins survive a new Python process."""

import os
import subprocess
import sys
from hashlib import sha256

import pytest


@pytest.mark.parametrize(("seed", "utf8"), [("1", "0"), ("98765", "1")])
def test_crosswalk_bytes_and_identity_are_stable_across_processes(
    seed: str,
    utf8: str,
) -> None:
    # ASCII source also runs when argv is decoded under the C locale. A set
    # deliberately varies production order with PYTHONHASHSEED.
    script = r"""
import sys
from lovspor.crosswalk.canonical import serialize_records
from lovspor.crosswalk.identity import identity_sha256, publication_identity

ids = {"vedtak-b", "vedtak-a"}
identity = publication_identity("2021-2022", ids)
records = [
    {"amending_lov_id": law, "record_revision": 1, "reason": "bl\u00e5"}
    for law in {"LOV-2022-06-17-64", "LOV-2022-06-17-63"}
]
sys.stdout.buffer.write(identity_sha256(identity).encode("ascii") + b"\n")
sys.stdout.buffer.write(serialize_records(records))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "LC_ALL": "C",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONUTF8": utf8,
            "PYTHONHASHSEED": seed,
        },
        capture_output=True,
        check=True,
        timeout=20,
    )
    identity_bytes = b'{"lovvedtak":["vedtak-a","vedtak-b"],"session_id":"2021-2022"}'
    expected = sha256(identity_bytes).hexdigest().encode("ascii") + b"\n"
    for number in (63, 64):
        expected += (
            f'{{"amending_lov_id":"LOV-2022-06-17-{number}","reason":"blå","record_revision":1}}\n'
        ).encode()
    assert result.stdout == expected
