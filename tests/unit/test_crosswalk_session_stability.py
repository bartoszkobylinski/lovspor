"""D11 canonical bytes and D8 identity hashes survive process defaults."""

import json
import os
import subprocess
import sys
from hashlib import sha256

import pytest


@pytest.mark.parametrize("seed,utf8", [("1", "0"), ("98765", "1")])
def test_canonical_bytes_and_identity_hash_survive_python_sessions(seed: str, utf8: str) -> None:
    # Build mappings and publication ids from sets so hash randomisation changes
    # insertion order. Escapes keep the child script valid under an ASCII locale.
    script = r"""
import json
from lovspor.crosswalk.canonical import parse_records, serialize_records
from lovspor.crosswalk.identity import identity_sha256, publication_identity

identity = publication_identity("2021-2022", {"vedtak-b", "vedtak-a"})
identity["label"] = "bl\u00e5 \u00f8"
record = {
    key: value for key, value in {
        ("amending_lov_id", "LOV-2022-06-17-63"),
        ("record_revision", 1),
        ("reason", "bl\u00e5 \u00f8"),
    }
}
data = serialize_records([record])
assert serialize_records(parse_records(data)) == data
print(json.dumps({"hex": data.hex(), "digest": identity_sha256(identity)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONHASHSEED": seed,
            "PYTHONUTF8": utf8,
            "PYTHONCOERCECLOCALE": "0",
            "LC_ALL": "C",
        },
        capture_output=True,
        check=True,
        timeout=20,
    )
    expected_bytes = (
        '{"amending_lov_id":"LOV-2022-06-17-63","reason":"blå ø","record_revision":1}\n'
    ).encode()
    expected_identity = (
        '{"label":"blå ø","lovvedtak":["vedtak-a","vedtak-b"],"session_id":"2021-2022"}'
    ).encode()
    assert json.loads(result.stdout) == {
        "hex": expected_bytes.hex(),
        "digest": sha256(expected_identity).hexdigest(),
    }
