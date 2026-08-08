"""Stage 5 run setup: pilot case selection and run-metadata composition.

Builds the ``run_metadata.schema.json`` document for a control-arm run
from explicit, verifiable inputs. ``dataset_checksum`` is always the
SHA-256 over the canonical bytes of the case set actually being run —
for the pilot that is a slice of discarded candidates, never the frozen
250, and the ``notes`` field must say so.
"""

import hashlib
from typing import Any

from pydantic import BaseModel

from lovspor.errors import LovsporError
from lovspor.llhb.schema import canonical_jsonl, dataset_checksum


class RunSetupError(LovsporError):
    """A run cannot be composed from the given inputs."""


class ControlRunSpec(BaseModel):
    """Explicit inputs for one control-arm run's metadata document."""

    run_id: str
    model_id: str
    system_prompt_text: str
    system_prompt_path: str
    lovspor_commit: str
    lovverk_commit: str
    runner_commit: str
    case_order_seed: int
    started_at: str
    notes: str | None = None


def sha256_text(text: str) -> str:
    """SHA-256 hex over the UTF-8 bytes of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pilot_cases(
    candidates: list[dict[str, Any]], frozen_ids: set[str], limit: int
) -> list[dict[str, Any]]:
    """The first ``limit`` discarded candidates, sorted by case_id.

    Fail-closed: the frozen dataset is never touched before Stage 9, so
    every candidate whose id is in ``frozen_ids`` is excluded, and a
    limit the drop pool cannot satisfy is an error rather than a
    silently smaller pilot.
    """
    drops = [case for case in candidates if str(case.get("case_id")) not in frozen_ids]
    drops.sort(key=lambda case: str(case["case_id"]))
    if limit < 1 or limit > len(drops):
        raise RunSetupError(f"limit {limit} outside the drop pool (1..{len(drops)})")
    return drops[:limit]


def compose_control_metadata(spec: ControlRunSpec, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """A schema-valid run-metadata document for the control condition."""
    return {
        "run_id": spec.run_id,
        "llhb_version": "1.0",
        "dataset_checksum": dataset_checksum(canonical_jsonl(cases)),
        "provider": "anthropic",
        "model_id": spec.model_id,
        "api_version": None,
        "condition": "control",
        "system_prompt_sha256": sha256_text(spec.system_prompt_text),
        "system_prompt_path": spec.system_prompt_path,
        "tool_config": None,
        "sampling": {"temperature": None},
        "max_turns": None,
        "started_at": spec.started_at,
        "finished_at": None,
        "lovspor_commit": spec.lovspor_commit,
        "lovverk_commit": spec.lovverk_commit,
        "runner_commit": spec.runner_commit,
        "evaluator_version": None,
        "case_order_seed": spec.case_order_seed,
        "notes": spec.notes,
    }
