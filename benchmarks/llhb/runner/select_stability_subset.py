"""Draw and write the committed stability subset (ruling #26).

Reads the frozen dataset, verifies it against its lock, draws the
30-case stratified subset with the recorded seed and writes
``dataset/frozen/llhb-v1-stability30.json``. The draw is deterministic,
so re-running this script must reproduce the committed file byte for
byte; a diff means the frozen inputs changed, and that is a finding,
not noise.

Usage:
    uv run python benchmarks/llhb/runner/select_stability_subset.py
"""

import json
import sys
from pathlib import Path
from typing import Any

from lovspor.llhb.run_setup import verify_frozen_against_lock
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum, load_cases_jsonl
from lovspor.llhb.stability import select_stability_subset

LLHB_DIR = Path(__file__).resolve().parents[1]
FROZEN_JSONL = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.jsonl"
FROZEN_LOCK = LLHB_DIR / "dataset" / "frozen" / "llhb-v1.lock.json"
SUBSET_PATH = LLHB_DIR / "dataset" / "frozen" / "llhb-v1-stability30.json"

SELECTION_RULE = (
    "proportional largest-remainder allocation over the frozen category "
    "counts; seeded within-category sample over id-sorted rows "
    "(lovspor.llhb.stability); ids listed ascending per category"
)


def draw() -> dict[str, Any]:
    """The subset document, drawn fresh from the verified frozen inputs."""
    cases = load_cases_jsonl(FROZEN_JSONL)
    lock = json.loads(FROZEN_LOCK.read_text(encoding="utf-8"))
    verify_frozen_against_lock(cases, lock)
    subset = select_stability_subset(cases)
    wanted = set(subset.case_ids)
    picked = [case for case in cases if str(case["case_id"]) in wanted]
    return {
        "ruling": "#26",
        "selection_rule": SELECTION_RULE,
        "dataset_sha256": str(lock["dataset_sha256"]),
        "subset_sha256": dataset_checksum(canonical_jsonl(picked)),
        **subset.model_dump(),
    }


def main() -> int:
    document = draw()
    SUBSET_PATH.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {SUBSET_PATH.relative_to(LLHB_DIR)}")
    print(json.dumps(document["allocation"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
