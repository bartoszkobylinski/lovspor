"""Replay the committed study of the 52 acts (#610) from the cached raw bytes, offline.

Raw Lovdata bytes are never committed, and the Stortinget listings and cases of
the study stay in the gitignored cache until the owner decides their retention.
So this runs only where `scripts/study_52.py capture` left the cache. A skip here
means the replay was NOT run on that machine; it is not a pass.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/cache/study-52"
COMMITTED_JSON = ROOT / "docs/research/data/study-52-2026-11-02.json"
COMMITTED_CSV = ROOT / "docs/research/data/study-52-2026-11-02.csv"
pytestmark = pytest.mark.skipif(
    not (CACHE / "capture-manifest.json").exists(),
    reason="replay NOT run: no data/cache/study-52 capture on this machine",
)


def _script():
    spec = importlib.util.spec_from_file_location("study_52", ROOT / "scripts/study_52.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rebuilt() -> tuple[object, dict]:
    script = _script()
    return script, script.build(CACHE)


def test_cache_is_the_capture_the_committed_results_were_built_from(rebuilt) -> None:
    _, results = rebuilt
    committed = json.loads(COMMITTED_JSON.read_text(encoding="utf-8"))
    assert results["capture"] == committed["capture"]


def test_every_derived_result_replays_byte_for_byte(rebuilt) -> None:
    script, results = rebuilt
    assert script.render_json(results) == COMMITTED_JSON.read_text(encoding="utf-8")
    assert script.render_csv(results) == COMMITTED_CSV.read_text(encoding="utf-8")
