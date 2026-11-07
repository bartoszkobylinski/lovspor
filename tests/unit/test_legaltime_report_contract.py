"""Public report boundaries stated in legaltime.run and decisions.md §21."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

from lovspor.legaltime.archive import INDEX_VERSION
from lovspor.legaltime.commands import ReplayRequest, replay_impl
from tests.unit.test_legaltime_archive import make_archive


def test_report_excludes_provision_payloads_and_commencement_quotes(tmp_path: Path) -> None:
    """run.py promises identities and digests, never provision text or source quotes."""
    archive = make_archive(tmp_path / "announcements.tar.bz2")
    oracle = make_archive(tmp_path / "oracle.tar.bz2", {"nl/act.xml": "consolidated.xml"})
    request = ReplayRequest(
        act="lov/2019-06-14-99",
        archives=(archive,),
        dates=(date(2021, 1, 1), date(2023, 1, 1)),
        oracle=oracle,
    )
    output, _ = replay_impl(request)
    report = json.loads(output)
    assert report["events"]
    assert report["verification"]
    assert report["baseline_timing"]["proofs"]
    for text in (
        "Loven gjelder prøving av fylkesmannen.",
        "Annet ledd nytt annet punktum.",
        "Tredje ledd er nytt.",
        "Innskutt første ledd.",
        "Departementet kan gi overgangsregler.",
        "Lova gjeld frå 1. januar 2021.",
    ):
        assert text not in output
    for event in report["events"]:
        assert set(event["operation"]["payload"]) == {"provision", "ledd"}
        for proof in event["timing"]["proofs"]:
            assert set(proof) == {"member", "refid", "sha256", "quote_sha256"}
            assert len(proof["quote_sha256"]) == 64


def test_report_is_byte_identical_across_date_order_and_index_cache(tmp_path: Path) -> None:
    """The report orders legal dates and does not depend on a warm header index."""
    archive = make_archive(tmp_path / "announcements.tar.bz2")
    cache = archive.with_name(f"{archive.name}.{INDEX_VERSION}.json")
    request = ReplayRequest(
        act="lov/2019-06-14-99",
        archives=(archive,),
        dates=(date(2023, 1, 1), date(2020, 1, 1), date(2021, 1, 1)),
        oracle=None,
    )
    assert not cache.exists()
    cold = replay_impl(request)
    assert cache.is_file()
    reordered = request.model_copy(update={"dates": tuple(reversed(request.dates))})
    assert replay_impl(reordered) == cold
    assert [state["at"] for state in json.loads(cold[0])["states"]] == [
        "2020-01-01",
        "2021-01-01",
        "2023-01-01",
    ]


def test_report_bytes_are_stable_across_python_hash_seeds(tmp_path: Path) -> None:
    """The deterministic JSON contract also holds in independent interpreter sessions."""
    archive = make_archive(tmp_path / "announcements.tar.bz2")
    oracle = make_archive(tmp_path / "oracle.tar.bz2", {"nl/act.xml": "consolidated.xml"})
    script = """
import sys
from datetime import date
from pathlib import Path
from lovspor.legaltime.commands import ReplayRequest, replay_impl

request = ReplayRequest(
    act="lov/2019-06-14-99",
    archives=(Path(sys.argv[1]),),
    dates=(date(2023, 1, 1), date(2021, 1, 1)),
    oracle=Path(sys.argv[2]),
)
report, _ = replay_impl(request)
sys.stdout.buffer.write(report.encode("utf-8"))
"""
    outputs = []
    for seed in ("1", "42"):
        result = subprocess.run(
            [sys.executable, "-c", script, str(archive), str(oracle)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            check=True,
            timeout=30,
        )
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
    report = json.loads(outputs[0])
    assert report["events"]
    assert report["verification"]
    assert report["inputs"][0]["sha256"]
