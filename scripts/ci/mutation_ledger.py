#!/usr/bin/env python3
"""Carry mutmut's per-mutant results from one nightly run to the next (#943).

A night measures about a quarter of its range before the budget or a runner
kill ends it, and `mutmut-pr.sh` used to start every run from an empty
`mutants/`. So no range ever completed. The ledger is mutmut's own
`mutants/**/*.meta` files: mutmut 3.8.0 already keeps a mutant's result across
a regeneration while its function's hash is unchanged, and resets it when the
function changed (`create_mutants_for_file`). The lane checkpoints the files
during the run and restores them the next night.

    mutation_ledger.py pack ARCHIVE      tar.gz every mutants/**/*.meta
    mutation_ledger.py unpack ARCHIVE    restore them, keeping only killed results
    mutation_ledger.py select            stdin: mutmut patterns; stdout: those to run

Only a `killed` result is carried. A survivor, timeout, suspicious or untested
mutant is measured again every night, because a test merged since may kill it
and the restored meta carries no record of which tests ran.

`select` needs the shadow tree generated first (`mutation_legacy.py generate`).
All commands run from the repo root.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import tarfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from mutmut.stats import status_by_exit_code

MUTANTS = Path("mutants")
META_SUFFIX = ".meta"
MUTANT_INFIX = "__mutmut_"
REQUIRED_KEYS = ("exit_code_by_key", "durations_by_key", "estimated_durations_by_key")


@dataclass
class Restored:
    files: int = 0
    kept: int = 0
    reset: int = 0
    dropped: int = 0


def meta_files(root: Path) -> list[Path]:
    """Every meta file under the shadow tree, in a stable order."""
    return sorted(p for p in (root / MUTANTS).rglob(f"*{META_SUFFIX}") if p.is_file())


def pack(archive: Path) -> int:
    """Write the archive next to its target, then rename: a reader never sees half of one."""
    partial = archive.with_name(archive.name + ".partial")
    root = Path.cwd()
    with tarfile.open(partial, "w:gz") as tar:
        for path in meta_files(root):
            tar.add(path, arcname=str(path.relative_to(root)))
    partial.replace(archive)
    return len(meta_files(root))


def _is_meta_member(member: tarfile.TarInfo) -> bool:
    parts = Path(member.name).parts
    return (
        member.isfile()
        and len(parts) > 1
        and parts[0] == MUTANTS.name
        and ".." not in parts
        and member.name.endswith(META_SUFFIX)
    )


def _carried(exit_codes: dict[str, int | None]) -> tuple[dict[str, int | None], int]:
    """Keep killed results; every other one becomes None (not measured)."""
    carried: dict[str, int | None] = {}
    reset = 0
    for key, code in exit_codes.items():
        killed = code is not None and status_by_exit_code[code] == "killed"
        carried[key] = code if killed else None
        reset += code is not None and not killed
    return carried, reset


def _read_meta(path: Path) -> dict[str, object] | None:
    """A meta written mid-checkpoint can be cut short; such a file is unreadable, not empty."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or not all(isinstance(meta.get(k), dict) for k in REQUIRED_KEYS):
        return None
    return meta


def _restore_one(path: Path, tally: Restored) -> None:
    meta = _read_meta(path)
    if meta is None:
        path.unlink()
        tally.dropped += 1
        return
    exit_codes = cast("dict[str, int | None]", meta["exit_code_by_key"])
    carried, reset = _carried(exit_codes)
    meta["exit_code_by_key"] = carried
    path.write_text(json.dumps(meta), encoding="utf-8")
    tally.files += 1
    tally.reset += reset
    tally.kept += sum(code is not None for code in carried.values())


def unpack(archive: Path) -> Restored:
    """Extract only mutants/**/*.meta (data filter, CVE-2007-4559), then carry killed results."""
    root = Path.cwd()
    with tarfile.open(archive, "r:gz") as tar:
        members = [m for m in tar.getmembers() if _is_meta_member(m)]
        tar.extractall(root, members=members, filter="data")
    tally = Restored()
    for member in members:
        _restore_one(root / member.name, tally)
    return tally


def results_by_key(root: Path) -> dict[str, int | None]:
    """Every mutant's exit code across the shadow tree; an unreadable meta adds none."""
    merged: dict[str, int | None] = {}
    for path in meta_files(root):
        meta = _read_meta(path)
        if meta is not None:
            merged.update(cast("dict[str, int | None]", meta["exit_code_by_key"]))
    return merged


def function_prefix(name: str) -> str | None:
    """`pkg.mod.x_f__mutmut_*` -> `pkg.mod.x_f__mutmut_`; None for any other pattern."""
    stem, star, rest = name.partition("*")
    if star and not rest and stem.endswith(MUTANT_INFIX) and not set("?[") & set(stem):
        return stem
    return None


def _by_function(results: dict[str, int | None]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for key in results:
        head, infix, _ = key.rpartition(MUTANT_INFIX)
        if infix:
            grouped[head + infix].append(key)
    return grouped


def _narrow_glob(pattern: str, keys: list[str], results: dict[str, int | None]) -> list[str]:
    """The whole function while none of it is measured; else only its unmeasured mutants."""
    if not keys:
        return [pattern]
    pending = sorted((k for k in keys if results[k] is None), key=_number)
    return [pattern] if len(pending) == len(keys) else pending


def _number(key: str) -> int:
    return int(key.rpartition(MUTANT_INFIX)[2] or 0)


def _narrow(
    pattern: str, results: dict[str, int | None], grouped: dict[str, list[str]]
) -> list[str]:
    prefix = function_prefix(pattern)
    if prefix is not None:
        return _narrow_glob(pattern, grouped.get(prefix, []), results)
    if any(c in pattern for c in "*?["):
        return [pattern]
    return [pattern] if results.get(pattern) is None else []


def _in_scope(patterns: list[str], results: dict[str, int | None]) -> list[str]:
    return sorted(k for k in results if any(k == p or fnmatch.fnmatch(k, p) for p in patterns))


def select(patterns: list[str], results: dict[str, int | None]) -> list[str]:
    """The patterns narrowed to unmeasured mutants.

    With nothing left to measure, one already-measured mutant in scope: `mutmut run`
    with no name runs the whole package, and with one it re-measures that mutant
    and prints the progress line over every carried result, which the report reads.
    """
    grouped = _by_function(results)
    chosen = [name for pattern in patterns for name in _narrow(pattern, results, grouped)]
    if chosen or not patterns:
        return chosen
    in_scope = _in_scope(patterns, results)
    return in_scope[:1] if in_scope else patterns


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["pack", "unpack", "select"])
    parser.add_argument("archive", nargs="?", type=Path)
    args = parser.parse_args(argv)
    if args.command != "select" and args.archive is None:
        parser.error(f"{args.command} needs ARCHIVE")
    return args


def _run_select() -> int:
    patterns = [line.strip() for line in sys.stdin if line.strip()]
    results = results_by_key(Path.cwd())
    chosen = select(patterns, results)
    pending = sum(1 for name in chosen if results.get(name, 0) is None or "*" in name)
    if chosen and not pending:
        print(
            f"ledger: every mutant in scope has a result; re-measuring {chosen[0]}", file=sys.stderr
        )
    print("\n".join(chosen))
    return 0


def main(argv: list[str]) -> int:
    args = _parse(argv)
    if args.command == "pack":
        print(f"ledger: packed {pack(args.archive)} meta file(s) into {args.archive}")
        return 0
    if args.command == "unpack":
        try:
            t = unpack(args.archive)
        except (OSError, tarfile.TarError) as exc:
            print(f"ledger: unreadable archive {args.archive}: {exc}", file=sys.stderr)
            return 1
        print(
            f"ledger: restored {t.files} meta file(s): {t.kept} killed result(s) kept, "
            f"{t.reset} other result(s) to measure again, {t.dropped} unreadable file(s) dropped"
        )
        return 0
    return _run_select()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
