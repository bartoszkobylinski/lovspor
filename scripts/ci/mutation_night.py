#!/usr/bin/env python3
"""Record every mutant a nightly run measured: verdict, kind of change, time (#984).

The nightly result (`mutation-result.json`) holds the counts and the survivors
with their diffs. It holds nothing about a killed mutant, no kind for any
mutant, and no time for any mutant, although mutmut wrote every one of those
down: the verdict, the duration and the estimate of the tests are in
`mutants/**/*.meta`, and the source of each mutant and of its original is in
the shadow tree, found by the line index mutmut 3.8.0 keeps beside each
mutated file (`*.spans`). All of it is gone when the job ends.

This reads those files while they are still on disk and writes two things:

* `--out-mutants`: one JSON line per measured mutant (gzip): id, file, symbol,
  class, status, exit code, kind, the tokens removed and added, seconds, and
  the estimate of its tests.
* `--out-night`: the same night in sums: by status, by kind, by file, by
  function (with the number of tests mutmut charged to it), the slowest
  mutants, and a comparison with the counts in `mutation-result.json`.

The status is mutmut's own word for the exit code (`mutmut.stats`). The kind is
read off the source by `mutation_kinds.py`; mutmut records none, so it is never
written as `operator`. A mutant whose source cannot be read keeps its verdict
and its time and gets a kind that says so.

It never fails the job: what cannot be read is named under `problems`, and the
verdict of the night is not this program's to give.

Usage:
    mutation_night.py --commit SHA --out-night night.json --out-mutants mutants.jsonl.gz
                      [--result mutation-result.json] [--summary night.md] [--mutants DIR]
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, NamedTuple

from mutation_kinds import describe
from mutation_survivors import split_id

try:
    from mutmut import __version__ as mutmut_version
    from mutmut.stats import status_by_exit_code
except ImportError as exc:
    IMPORT_ERROR: str | None = f"mutmut is not importable ({exc})"
else:
    IMPORT_ERROR = None

SCHEMA_VERSION = 1
META_SUFFIX = ".meta"
SPANS_SUFFIX = ".spans"
SPANS_VERSION = 1
STATS_FILE = "mutmut-stats.json"
MUTANT_MARKER = "__mutmut_"
NO_SOURCE = "unknown: no source in the shadow tree"
KILLED = "killed"
SLOWEST = 50
# The tokens a mutant removed or added, cut to this many characters per side.
CHANGE_WIDTH = 160
# mutation-result.json's count keys beside mutmut's status words.
RESULT_KEYS = {
    "killed": "killed",
    "survived": "survived",
    "timeout": "timeout",
    "no_tests": "no tests",
    "skipped": "skipped",
}


class Mutant(NamedTuple):
    """One measured mutant."""

    id: str
    file: str
    symbol: str
    class_name: str | None
    status: str
    exit_code: int
    kind: str
    removed: str
    added: str
    seconds: float | None
    estimate_seconds: float | None

    @property
    def function(self) -> str:
        """The key mutmut files this mutant's function under."""
        return self.id.rpartition(MUTANT_MARKER)[0]

    def record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file": self.file,
            "symbol": self.symbol,
            "class": self.class_name,
            "status": self.status,
            "exit_code": self.exit_code,
            "kind": self.kind,
            "removed": self.removed,
            "added": self.added,
            "seconds": self.seconds,
            "estimate_seconds": self.estimate_seconds,
        }


def _load(path: Path) -> dict[str, Any] | None:
    """A JSON object from a file, or None when it is absent or not one."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _generated(mutant_id: str) -> tuple[str, str]:
    """The names the shadow tree gives a mutant and the original it was made from."""
    mutant = mutant_id.rpartition(".")[2]
    return mutant, mutant.rpartition(MUTANT_MARKER)[0] + MUTANT_MARKER + "orig"


def sources(root: Path, source_file: str, names: set[str]) -> dict[str, str]:
    """The text of the named generated functions of one mutated file, by its line index."""
    index = _load(root / f"{source_file}{SPANS_SUFFIX}")
    if index is None or index.get("version") != SPANS_VERSION:
        return {}
    try:
        lines = (root / source_file).read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, ValueError):
        return {}
    spans = index.get("spans")
    found = {}
    for name in names:
        span = spans.get(name) if isinstance(spans, dict) else None
        if isinstance(span, list) and len(span) == 2:  # noqa: PLR2004
            found[name] = "".join(lines[span[0] - 1 : span[1]])
    return found


def _number(value: object) -> float | None:
    return round(float(value), 3) if isinstance(value, int | float) else None


def file_mutants(root: Path, meta_path: Path) -> tuple[list[Mutant], int, int]:
    """One mutated file's measured mutants, how many it left unchecked, how many had no source."""
    meta = _load(meta_path) or {}
    codes = meta.get("exit_code_by_key") or {}
    durations = meta.get("durations_by_key") or {}
    estimates = meta.get("estimated_durations_by_key") or {}
    source_file = str(meta_path.relative_to(root))[: -len(META_SUFFIX)]
    measured = sorted(key for key, code in codes.items() if code is not None)
    texts = sources(root, source_file, {name for key in measured for name in _generated(key)})
    mutants, without_source = [], 0
    for key in measured:
        mutant, original = _generated(key)
        _, symbol, class_name = split_id(key)
        if mutant in texts and original in texts:
            kind, removed, added = describe(texts[original], texts[mutant])
        else:
            kind, removed, added = NO_SOURCE, "", ""
            without_source += 1
        mutants.append(
            Mutant(
                id=key,
                file=source_file,
                symbol=symbol,
                class_name=class_name,
                status=status_by_exit_code[codes[key]],
                exit_code=codes[key],
                kind=kind,
                removed=removed[:CHANGE_WIDTH],
                added=added[:CHANGE_WIDTH],
                seconds=_number(durations.get(key)),
                estimate_seconds=_number(estimates.get(key)),
            )
        )
    return mutants, len(codes) - len(measured), without_source


def tally(mutants: list[Mutant]) -> dict[str, Any]:
    """How many mutants, by status, and the time they took."""
    by_status = Counter(mutant.status for mutant in mutants)
    return {
        "mutants": len(mutants),
        "by_status": dict(sorted(by_status.items())),
        "seconds": round(sum(mutant.seconds or 0.0 for mutant in mutants), 3),
    }


def _grouped(mutants: list[Mutant], key: str) -> dict[str, list[Mutant]]:
    groups: dict[str, list[Mutant]] = defaultdict(list)
    for mutant in mutants:
        groups[getattr(mutant, key)].append(mutant)
    return dict(sorted(groups.items()))


def functions(mutants: list[Mutant], tests: dict[str, Any] | None) -> list[dict[str, Any]]:
    """One row per function, the ones with most mutants left alive first."""
    rows = []
    for name, group in _grouped(mutants, "function").items():
        first = group[0]
        charged = tests.get(name) if tests is not None else None
        rows.append(
            {
                "function": name,
                "file": first.file,
                "symbol": first.symbol,
                "class": first.class_name,
                "tests": len(charged) if isinstance(charged, list) else None,
                "estimate_seconds": max(
                    (m.estimate_seconds for m in group if m.estimate_seconds is not None),
                    default=None,
                ),
                **tally(group),
            }
        )
    rows.sort(key=lambda row: (-_alive(row), row["function"]))
    return rows


def _alive(row: dict[str, Any]) -> int:
    """Mutants of a tally that a test did not kill."""
    return int(row["mutants"]) - int(row["by_status"].get(KILLED, 0))


def result_check(night: dict[str, int], result_path: Path | None) -> dict[str, Any] | None:
    """Where this night's counts and mutation-result.json's differ; None without that file."""
    result = _load(result_path) if result_path is not None else None
    counts = result.get("mutants") if result is not None else None
    if not isinstance(counts, dict):
        return None
    differences = {
        key: {"result": counts.get(key), "night": night.get(status, 0)}
        for key, status in RESULT_KEYS.items()
        if counts.get(key) != night.get(status, 0)
    }
    return {"agrees": not differences, "differences": differences}


def build(root: Path, commit: str, result_path: Path | None) -> tuple[dict[str, Any], list[Mutant]]:
    """Read the shadow tree; return the night in sums and every measured mutant."""
    problems: list[str] = []
    mutants: list[Mutant] = []
    not_checked = without_source = 0
    metas = sorted(p for p in root.rglob(f"*{META_SUFFIX}") if p.is_file())
    if not metas:
        problems.append(f"no {root}/**/*{META_SUFFIX}: the shadow tree was not kept")
    for meta_path in metas:
        found, unchecked, sourceless = file_mutants(root, meta_path)
        mutants += found
        not_checked += unchecked
        without_source += sourceless
    if without_source:
        problems.append(f"{without_source} measured mutant(s) had no readable source: kind unknown")
    stats = _load(root / STATS_FILE)
    tests = stats.get("tests_by_mangled_function_name") if stats is not None else None
    if not isinstance(tests, dict):
        tests = None
        problems.append(f"no {root}/{STATS_FILE}: the tests charged to a function are not known")
    measured = tally(mutants)
    check = result_check(measured["by_status"], result_path)
    if check is None:
        problems.append("no mutation-result.json to compare the counts with")
    slowest = sorted(mutants, key=lambda m: (-(m.seconds or 0.0), m.id))[:SLOWEST]
    night = {
        "schema_version": SCHEMA_VERSION,
        "commit": commit,
        "written_by": "scripts/ci/mutation_night.py",
        "mutmut": mutmut_version,
        "kinds": "read off each mutant's source by scripts/ci/mutation_kinds.py; "
        "mutmut records no operator",
        "problems": problems,
        "measured": measured,
        "not_checked": not_checked,
        "estimate_seconds": round(sum(m.estimate_seconds or 0.0 for m in mutants), 3),
        "by_kind": {k: tally(g) for k, g in _grouped(mutants, "kind").items()},
        "by_file": {k: tally(g) for k, g in _grouped(mutants, "file").items()},
        "by_function": functions(mutants, tests),
        "slowest": [m.record() for m in slowest],
        "result_check": check,
    }
    return night, mutants


def _duration(seconds: float) -> str:
    if seconds < 60:  # noqa: PLR2004
        return f"{seconds:.0f} s"
    minutes = int(seconds // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"  # noqa: PLR2004


def _row(name: str, counts: dict[str, Any]) -> str:
    by_status = counts["by_status"]
    killed, survived = by_status.get(KILLED, 0), by_status.get("survived", 0)
    other = counts["mutants"] - killed - survived
    share = 100.0 * killed / counts["mutants"]
    return (
        f"| {name} | {counts['mutants']} | {killed} | {survived} | {other} "
        f"| {share:.1f} % | {_duration(counts['seconds'])} |"
    )


_HEADER = [
    "| mutants | killed | survived | other | killed % | time |",
    "|---:|---:|---:|---:|---:|---:|",
]


def _table(title: str, rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    head = [f"| {title} {_HEADER[0]}", f"|---{_HEADER[1]}"]
    return [*head, *(_row(name, counts) for name, counts in rows), ""]


def summary(night: dict[str, Any], files: int = 15, slowest: int = 10) -> str:
    """The night as Markdown for the job summary: totals, kinds, worst files, slowest mutants."""
    measured = night["measured"]
    statuses = ", ".join(f"{count} {status}" for status, count in measured["by_status"].items())
    lines = [
        "### Every mutant of this night (#984)",
        "",
        f"- Measured: **{measured['mutants']}**{f' ({statuses})' if statuses else ''}; "
        f"not checked: {night['not_checked']}",
        f"- Mutant time: {_duration(measured['seconds'])} "
        f"against an estimate of {_duration(night['estimate_seconds'])}",
    ]
    check = night["result_check"]
    if check is not None and not check["agrees"]:
        lines.append(f"- **Counts differ from mutation-result.json:** `{check['differences']}`")
    lines += [f"- Problem: {problem}" for problem in night["problems"]]
    lines.append("")
    if not measured["mutants"]:
        return "\n".join(lines)
    kinds = sorted(night["by_kind"].items(), key=lambda item: (-item[1]["mutants"], item[0]))
    worst = sorted(night["by_file"].items(), key=lambda item: (-_alive(item[1]), item[0]))
    lines += _table("kind (read off the source, not reported by mutmut)", kinds)
    lines += _table(f"file, the {files} with most mutants left alive", worst[:files])
    lines += ["| slowest mutants | status | kind | seconds | estimate |", "|---|---|---|---:|---:|"]
    lines += [
        f"| `{m['id']}` | {m['status']} | {m['kind']} | {m['seconds']} | {m['estimate_seconds']} |"
        for m in night["slowest"][:slowest]
    ]
    return "\n".join([*lines, ""])


def write(night: dict[str, Any], mutants: list[Mutant], args: argparse.Namespace) -> None:
    args.out_night.write_text(json.dumps(night, indent=1, ensure_ascii=False), encoding="utf-8")
    lines = "".join(json.dumps(m.record(), ensure_ascii=False) + "\n" for m in mutants)
    # No time and no file name in the gzip header: the same mutants give the same bytes.
    with (
        args.out_mutants.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as handle,
    ):
        handle.write(lines.encode("utf-8"))
    if args.summary is not None:
        args.summary.write_text(summary(night), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", required=True)
    parser.add_argument("--out-night", required=True, type=Path)
    parser.add_argument("--out-mutants", required=True, type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--mutants", type=Path, default=Path("mutants"))
    args = parser.parse_args(argv)
    if IMPORT_ERROR is not None:
        print(f"no per-mutant record: {IMPORT_ERROR}")
        return 0
    night, mutants = build(args.mutants, args.commit, args.result)
    write(night, mutants, args)
    print(
        f"wrote {args.out_night} and {args.out_mutants}: {len(mutants)} measured mutant(s), "
        f"{len(night['by_kind'])} kind(s), {len(night['problems'])} problem(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
