"""The survey log is UTF-8 because it says so, not because the machine agreed.

Every read and write in :mod:`lovspor.observatory.survey_commands` names its
encoding. Dropping those arguments looks harmless — on a UTF-8 machine the
behaviour is identical, which is why a mutation that removes them survives an
ordinary test run — and it makes the archive's encoding a property of whoever
happened to start the process.

Norwegian municipal data is the wrong place for that: `kunngjøring`, `høring`
and `Bærum` are the words this archive is made of. So the test runs a fresh
interpreter under `LC_ALL=C` with UTF-8 mode off, which is the environment an
unnamed encoding would actually go wrong in.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

NORWEGIAN = "https://example.invalid/høringer-og-kunngjøringer.xml"

HOST = "bærum.invalid"

_SCRIPT = """
import json, sys
from pathlib import Path

from lovspor.observatory.survey import RobotsReadout, read_site_shape
from lovspor.observatory.survey_commands import _domains, _write

target, listing = Path(sys.argv[1]), Path(sys.argv[3])
# Read from a file, not argv: under LC_ALL=C Linux hands argv over as
# ASCII + surrogateescape, so a non-ASCII letter would arrive already broken
# and the write under test would be refusing the harness, not proving its
# own encoding. This script is argv too, so it must stay ASCII itself.
declared = Path(sys.argv[2]).read_bytes().decode("utf-8")

shape = read_site_shape(
    domain="example.invalid",
    robots=RobotsReadout(readable=True, allows_root=True, declared_sitemaps=(declared,)),
    conventional_sitemap=False,
    front_page=b"",
)
_write(target, [shape])
print(json.dumps(_domains(None, listing)))
"""


def _run(tmp_path: Path) -> tuple[Path, list[str]]:
    """Run the script in a fresh interpreter whose locale is not UTF-8."""
    target = tmp_path / "survey.jsonl"
    listing = tmp_path / "domains.txt"
    listing.write_bytes(f"# høringer\n{HOST}\n".encode())
    declared = tmp_path / "declared.txt"
    declared.write_bytes(NORWEGIAN.encode())
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
    }
    environment.pop("PYTHONIOENCODING", None)
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(target), str(declared), str(listing)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return target, json.loads(completed.stdout)


def test_the_log_is_utf_8_even_when_the_locale_is_not(tmp_path: Path) -> None:
    target, _ = _run(tmp_path)

    row = json.loads(target.read_bytes().decode("utf-8"))

    assert row["declared_sitemaps"] == [NORWEGIAN]


def test_a_host_list_is_read_as_utf_8_even_when_the_locale_is_not(tmp_path: Path) -> None:
    _, hosts = _run(tmp_path)

    assert hosts == ["bærum.invalid"]
