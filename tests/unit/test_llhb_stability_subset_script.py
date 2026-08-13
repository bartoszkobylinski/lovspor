"""The committed stability-subset artifact and the script that draws it.

Loaded via importlib (the run_arm precedent). These tests pin three
things: the draw is deterministic, the document carries verifiable
provenance (frozen checksum + subset checksum), and the committed file
is exactly what the script would write today — a drift between them
means the frozen inputs changed underneath the subset.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from lovspor.llhb.schema import canonical_jsonl, dataset_checksum, load_cases_jsonl
from lovspor.llhb.stability import STABILITY_SELECTION_SEED

_RUNNER = Path(__file__).parents[2] / "benchmarks" / "llhb" / "runner"
_SCRIPT = _RUNNER / "select_stability_subset.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("select_stability_subset_script", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


script = _load()


def test_draw_is_deterministic_and_stratified() -> None:
    first = script.draw()
    second = script.draw()

    assert first == second
    assert first["size"] == 30
    assert first["seed"] == STABILITY_SELECTION_SEED
    assert first["allocation"] == {
        "C1": 6,
        "C2": 5,
        "C3": 4,
        "C4": 4,
        "C5": 2,
        "C6": 4,
        "C7": 3,
        "C8": 2,
    }
    assert len(first["case_ids"]) == 30


def test_draw_provenance_is_verifiable() -> None:
    document = script.draw()
    frozen = load_cases_jsonl(script.FROZEN_JSONL)
    lock = json.loads(script.FROZEN_LOCK.read_text(encoding="utf-8"))

    assert document["dataset_sha256"] == lock["dataset_sha256"]
    wanted = set(document["case_ids"])
    picked = [case for case in frozen if str(case["case_id"]) in wanted]
    assert len(picked) == 30
    assert document["subset_sha256"] == dataset_checksum(canonical_jsonl(picked))


def test_committed_artifact_matches_a_fresh_draw() -> None:
    committed = json.loads(script.SUBSET_PATH.read_text(encoding="utf-8"))

    assert committed == script.draw()
