"""Stage 5 results store (PROPOSAL §13.3).

Layout on disk, one directory per run::

    results/runs/<run-id>/
    ├── run-metadata.json   # run_metadata.schema.json, one document
    └── records.jsonl       # result_record.schema.json, one line per case

Fail-closed by design: every document validates against the committed
JSON Schema before any byte reaches disk; records are append-only and
never edited after capture; one (case_id, repeat_index) pair may appear
at most once per run; finalization may touch completion fields only.
"""

import json
import re
from pathlib import Path
from typing import Any

from lovspor.errors import LovsporError
from lovspor.llhb.schema import canonical_case_line, load_schema, validate_case

_RUN_ID_RE = re.compile(r"^llhb-v1-run-[0-9]{8}-[a-z0-9]{4,12}$")
_FINALIZABLE = frozenset(
    {"finished_at", "cases_total", "cases_completed", "errors_total", "notes", "evaluator_version"}
)
_MATCHED_FIELDS = ("run_id", "provider", "model_id", "condition")
_METADATA_FILE = "run-metadata.json"
_RECORDS_FILE = "records.jsonl"

_DedupKey = tuple[str, int | None]


class ResultsStoreError(LovsporError):
    """A run artifact violates the results-store contract."""


def new_run_id(date_utc: str, suffix: str) -> str:
    """Build a schema-valid run id from a YYYYMMDD date and a short suffix."""
    run_id = f"llhb-v1-run-{date_utc}-{suffix}"
    if not _RUN_ID_RE.match(run_id):
        raise ResultsStoreError(
            f"invalid run id {run_id!r}: need a YYYYMMDD date and a [a-z0-9]{{4,12}} suffix"
        )
    return run_id


class ResultsStore:
    """Validated, append-only storage for one runs root directory."""

    def __init__(self, runs_root: Path, schema_dir: Path) -> None:
        self._runs_root = runs_root
        self._metadata_schema = load_schema(schema_dir / "run_metadata.schema.json")
        self._record_schema = load_schema(schema_dir / "result_record.schema.json")
        self._seen: dict[str, set[_DedupKey]] = {}

    def open_run(self, metadata: dict[str, Any]) -> Path:
        """Validate metadata, create the run directory, write run-metadata.json."""
        self._check("run metadata", metadata, self._metadata_schema)
        run_dir = self._run_dir(str(metadata["run_id"]))
        if run_dir.exists():
            raise ResultsStoreError(f"run directory {run_dir} already exists")
        run_dir.mkdir(parents=True)
        _write_metadata(run_dir / _METADATA_FILE, metadata)
        return run_dir

    def append_record(self, record: dict[str, Any]) -> None:
        """Validate one result record and append it to the run's JSONL."""
        self._check("result record", record, self._record_schema)
        run_id = str(record["run_id"])
        self._check_matches_run(record, self._load_metadata(run_id))
        key: _DedupKey = (str(record["case_id"]), record.get("repeat_index"))
        seen = self._seen_keys(run_id)
        if key in seen:
            raise ResultsStoreError(f"duplicate record for case {key[0]!r} (repeat_index={key[1]})")
        with (self._run_dir(run_id) / _RECORDS_FILE).open("a", encoding="utf-8") as handle:
            handle.write(canonical_case_line(record) + "\n")
        seen.add(key)

    def read_records(self, run_id: str) -> list[dict[str, Any]]:
        """All records of a run, in append order."""
        self._load_metadata(run_id)
        return self._read_lines(run_id)

    def finalize_run(self, run_id: str, updates: dict[str, Any]) -> None:
        """Update completion fields on an existing run's metadata."""
        unknown = sorted(set(updates) - _FINALIZABLE)
        if unknown:
            raise ResultsStoreError(f"fields not finalizable: {unknown}")
        merged = {**self._load_metadata(run_id), **updates}
        self._check("run metadata", merged, self._metadata_schema)
        _write_metadata(self._run_dir(run_id) / _METADATA_FILE, merged)

    def _run_dir(self, run_id: str) -> Path:
        if not _RUN_ID_RE.match(run_id):
            raise ResultsStoreError(f"invalid run id {run_id!r}")
        path = self._runs_root / run_id
        if path.resolve().parent != self._runs_root.resolve():
            raise ResultsStoreError(f"run id {run_id!r} escapes the runs root")
        return path

    def _check(self, label: str, document: dict[str, Any], schema: dict[str, Any]) -> None:
        errors = validate_case(document, schema)
        if errors:
            raise ResultsStoreError(f"invalid {label}: " + "; ".join(errors))

    def _check_matches_run(self, record: dict[str, Any], metadata: dict[str, Any]) -> None:
        for field in _MATCHED_FIELDS:
            if record[field] != metadata[field]:
                raise ResultsStoreError(
                    f"record {field} {record[field]!r} does not match "
                    f"run metadata {metadata[field]!r}"
                )

    def _load_metadata(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / _METADATA_FILE
        if not path.is_file():
            raise ResultsStoreError(f"unknown run {run_id!r}: {path} not found")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ResultsStoreError(f"{path} is not a JSON object")
        return loaded

    def _seen_keys(self, run_id: str) -> set[_DedupKey]:
        if run_id not in self._seen:
            self._seen[run_id] = {
                (str(record["case_id"]), record.get("repeat_index"))
                for record in self._read_lines(run_id)
            }
        return self._seen[run_id]

    def _read_lines(self, run_id: str) -> list[dict[str, Any]]:
        path = self._run_dir(run_id) / _RECORDS_FILE
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            loaded = json.loads(line)
            if not isinstance(loaded, dict):
                raise ResultsStoreError(f"{path}:{number}: record line is not a JSON object")
            records.append(loaded)
        return records


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    payload = json.dumps(metadata, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")
