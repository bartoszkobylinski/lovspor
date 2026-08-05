"""Dataset schema and canonicalization utilities (FREEZE.md §4).

Loads case files, validates them against the committed JSON Schema, and
produces the canonical JSONL byte form whose SHA-256 is the dataset
checksum. Canonical form (frozen contract):

* one case per line, lines sorted by ``case_id`` (so the checksum is
  independent of input order);
* each line: JSON with lexicographically sorted keys, compact
  separators, UTF-8 with non-ASCII kept literal;
* LF line endings, trailing LF at end of file;
* duplicate ``case_id`` values are an error — canonicalization fails
  closed rather than producing an ambiguous dataset.

``jsonschema`` is a dev-group dependency (benchmark-only tooling); the
import is lazy so the shipped package does not depend on it. JSON-Schema
``format`` checks are best-effort annotations — the structural checks
and the typed models in the validator are authoritative.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

from lovspor.errors import ConfigError, LovsporError


class DatasetFormatError(LovsporError):
    """A case file or case set violates the canonical dataset contract."""


def load_schema(path: Path) -> dict[str, Any]:
    """Load a JSON Schema document from ``path``."""
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise DatasetFormatError(f"schema at {path} is not a JSON object")
    return schema


def load_cases_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL case file; every non-empty line must be a JSON object."""
    cases: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetFormatError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(case, dict):
            raise DatasetFormatError(f"{path}:{number}: case line is not a JSON object")
        cases.append(case)
    return cases


def validate_case(case: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Deterministically ordered schema-violation messages (empty = valid)."""
    validator = _build_validator(schema)
    errors = sorted(
        validator.iter_errors(case),
        key=lambda error: (list(map(str, error.absolute_path)), error.message),
    )
    return [f"{_json_path(list(error.absolute_path))}: {error.message}" for error in errors]


def canonical_case_line(case: dict[str, Any]) -> str:
    """One case in canonical single-line form (no trailing newline)."""
    return json.dumps(case, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def canonical_jsonl(cases: list[dict[str, Any]]) -> bytes:
    """Canonical dataset bytes: case_id-sorted lines, LF, trailing LF."""
    ids = [str(case.get("case_id", "")) for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise DatasetFormatError(f"duplicate case_id values: {duplicates}")
    if "" in ids:
        raise DatasetFormatError("every case needs a case_id for canonical ordering")
    ordered = sorted(cases, key=lambda case: str(case["case_id"]))
    lines = "".join(canonical_case_line(case) + "\n" for case in ordered)
    return lines.encode("utf-8")


def dataset_checksum(payload: bytes) -> str:
    """SHA-256 hex over canonical dataset bytes — the frozen-dataset identity."""
    return hashlib.sha256(payload).hexdigest()


def _build_validator(schema: dict[str, Any]) -> Any:
    try:
        import jsonschema  # noqa: PLC0415 — lazy on purpose: dev-only dependency
    except ImportError as exc:  # pragma: no cover - dev deps install jsonschema
        raise ConfigError(
            "jsonschema is required for LLHB dataset validation; "
            "install the dev dependency group (uv sync)",
        ) from exc
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def _json_path(parts: list[str]) -> str:
    return "$" + "".join(f".{part}" for part in parts) if parts else "$"
