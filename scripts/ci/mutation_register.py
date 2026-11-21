#!/usr/bin/env python3
"""Keep what a nightly mutation run found, night after night (#984).

A night's result lived in a run artifact for 90 days and in the rolling issue
until the next night rewrote it. After two completed nights 2 795 mutants were
on record as alive and nothing held the list.

This merges one night into three things that last, as files for the
`mutation-reports` branch (`mutation_reports.sh` commits them):

* `register.jsonl`: every mutant known to be alive, one JSON line each, with
  the change it makes, its kind, when it was first seen and last measured.
* `history.jsonl`: one line per night, failed nights included.
* `nights/<date>-<head>/report.md` and `night.json`: the night in full.

It also writes the two texts the lane puts on the issue: a section for the
body (the register as it stands, the last nights) and a comment for the night.

The register is a record of MEASUREMENTS. A function measured tonight has its
lines replaced by tonight's result, so a mutant a test now kills leaves and a
new survivor arrives; a function not measured keeps its lines. Only a completed
night changes it, the rule the base tag follows: a partial night has measured
part of a function and would drop the rest. An entry is identified by its file,
its function and the lines of the mutation, never by mutmut's id, which
renumbers when the function is edited. A function that is no longer in the
tree takes its entries with it. Registered equivalents are not listed: they
are not work.

Usage:
    mutation_register.py merge --result R --register-in F --history-in F --out DIR
        --date D --run-id N --run-url U --base SHA --head SHA --verdict V --complete true|false
        --repo OWNER/NAME [--night N] [--mutants M] [--claims C] [--root .] [--written-by TEXT]

`--claims` is what `mutation_remeasure.py` wrote: the registered survivors a
commit in the range claims to kill. The report says of each whether the night
found it killed. The claim itself moves nothing.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from mutation_to_json import normalise_mutation

Entry = dict[str, Any]
Function = tuple[str, str | None, str]

BRANCH = "mutation-reports"
# mutmut joins a class and its method with this in a mutant's name.
CLASS_SEPARATOR = "ǁ"
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
REGISTER = "register.jsonl"
HISTORY = "history.jsonl"
BUCKETS = (("survivors", "survived"), ("timed_out", "timeout"), ("suspicious", "suspicious"))
KEY_LENGTH = 16
NIGHTS_SHOWN = 14
FILES_SHOWN = 15
OLDEST_SHOWN = 20
CHANGED_LINES_SHOWN = 6
README = """# mutation-reports

Written by the nightly mutation lane of this repository
(`.github/workflows/nightly-mutation.yml`, `scripts/ci/mutation_register.py`).
Nothing here is edited by hand.

- `register.jsonl`: every mutant known to be alive, one JSON object per line.
  An entry leaves when a night measures its function again and a test kills
  the mutant, or when its function is no longer in the tree.
- `history.jsonl`: one line per night, failed nights included.
- `nights/<date>-<head>/report.md`: one night in full. `night.json` beside it
  has the same night in sums, when the lane recorded them.

A line whose `written_by` starts with `seed:` was merged from a run artifact
outside the lane.
"""


def read_lines(path: Path | None) -> list[Entry]:
    """The JSON objects of a .jsonl or .jsonl.gz file; nothing when it is absent or empty."""
    if path is None or not path.is_file():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_object(path: Path | None) -> Entry | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def function_of(entry: Entry) -> Function:
    return entry["file"], entry.get("class"), entry["symbol"]


def entry_key(entry: Entry) -> str:
    """What identifies a mutation across nights: its file, its function, its changed lines."""
    file, class_name, symbol = function_of(entry)
    change = "\n".join(normalise_mutation(entry.get("diff") or ""))
    text = "\0".join((file, class_name or "", symbol, change or entry.get("id", "")))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def alive(result: Entry | None, kinds: dict[str, Entry]) -> tuple[list[Entry], int]:
    """The mutants no test killed, as register entries; and how many equivalents were skipped."""
    entries, equivalents, seen = [], 0, Counter[str]()
    for bucket, status in BUCKETS:
        for found in (result or {}).get(bucket) or []:
            if not isinstance(found, dict) or not found.get("file") or not found.get("symbol"):
                continue
            if found.get("equivalent"):
                equivalents += 1
                continue
            key = entry_key(found)
            seen[key] += 1
            described = kinds.get(found.get("id", ""), {})
            entries.append(
                {
                    # Two identical changes in one function are two mutants.
                    "key": key if seen[key] == 1 else f"{key}-{seen[key]}",
                    "file": found["file"],
                    "class": found.get("class"),
                    "symbol": found["symbol"],
                    "symbol_line": found.get("symbol_line"),
                    "status": status,
                    "kind": described.get("kind"),
                    "removed": described.get("removed"),
                    "added": described.get("added"),
                    "diff": found.get("diff"),
                    "mutant": found.get("id"),
                }
            )
    return entries, equivalents


def still_there(root: Path, entry: Entry) -> bool:
    """Whether the entry's function is still in the tree.

    A file that is there and cannot be parsed proves nothing, so its entries
    stay. No mutmut import: this runs wherever the register is read.
    """
    file, class_name, symbol = function_of(entry)
    try:
        scope: ast.AST | None = ast.parse((root / file).read_text(encoding="utf-8"))
    except OSError:
        return False
    except (SyntaxError, ValueError):
        return True
    for name in (*(class_name.split(CLASS_SEPARATOR)[-1:] if class_name else ()), symbol):
        scope = next(
            (n for n in ast.walk(scope) if isinstance(n, _DEFINITIONS) and n.name == name), None
        )
        if scope is None:
            return False
    return True


def merge(
    register: list[Entry], tonight: list[Entry], measured: set[Function], stamp: Entry, root: Path
) -> tuple[list[Entry], dict[str, int]]:
    """Replace the measured functions' entries with tonight's; drop functions that are gone."""
    cleared = [entry for entry in register if function_of(entry) in measured]
    kept = [entry for entry in register if function_of(entry) not in measured]
    first_seen = {entry["key"]: entry["first_seen"] for entry in cleared}
    fresh = [
        {**entry, "first_seen": first_seen.get(entry["key"], stamp), "last_measured": stamp}
        for entry in tonight
    ]
    present = [entry for entry in kept if still_there(root, entry)]
    merged = sorted(
        [*present, *fresh],
        key=lambda e: (e["file"], e.get("class") or "", e["symbol"], e["key"]),
    )
    back = {entry["key"] for entry in fresh}
    movement = {
        "open": len(merged),
        "files": len({entry["file"] for entry in merged}),
        "new": sum(entry["key"] not in first_seen for entry in fresh),
        "left": sum(entry["key"] not in back for entry in cleared),
        "gone": len(kept) - len(present),
    }
    return merged, movement


KILLED = "killed"
STILL_ALIVE = "still alive"
UNDECIDED = "not decided: the night did not measure its function"


def claim_outcomes(
    claims: list[Entry], before: list[Entry], after: list[Entry], measured: set[Function]
) -> list[Entry]:
    """What the night found for each claimed key.

    A claim is `killed` only when the night measured the entry's function and
    the entry is no longer alive. A key that was never in the register, or
    whose function is gone, keeps the state `mutation_remeasure.py` gave it.
    """
    was = {entry["key"]: entry for entry in before}
    alive_now = {entry["key"] for entry in after}
    outcomes = []
    for claim in claims:
        entry = was.get(claim["key"])
        if entry is None or claim.get("pattern") is None:
            outcome = claim.get("state") or "unknown"
        elif function_of(entry) not in measured:
            outcome = UNDECIDED
        else:
            outcome = STILL_ALIVE if claim["key"] in alive_now else KILLED
        outcomes.append({**claim, "outcome": outcome})
    return outcomes


def _claims(outcomes: list[Entry]) -> list[str]:
    if not outcomes:
        return []
    rows = [
        [
            f"`{claim['key']}`",
            f"`{claim['file']}` `{claim['symbol']}`" if claim.get("file") else "",
            f"`{claim['commit'][:8]}`",
            claim["outcome"],
        ]
        for claim in outcomes
    ]
    heading = f"## Claimed killed by a commit in this range ({len(outcomes)})"
    return [heading, "", *table(["key", "function", "commit", "this night found"], rows)]


def unchanged(register: list[Entry]) -> dict[str, int]:
    """The movement of a night that did not touch the register."""
    files = len({entry["file"] for entry in register})
    return {"open": len(register), "files": files, "new": 0, "left": 0, "gone": 0}


def history_line(args: argparse.Namespace, result: Entry | None, night: Entry | None) -> Entry:
    kinds = None
    if night is not None:
        kinds = {
            name: {"mutants": row["mutants"], "killed": row["by_status"].get("killed", 0)}
            for name, row in night["by_kind"].items()
        }
    return {
        "date": args.date,
        "run": args.run_id,
        "run_url": args.run_url,
        "base": args.base,
        "head": args.head,
        "verdict": args.verdict,
        "complete": args.complete,
        "written_by": args.written_by,
        "result": (result or {}).get("mutants"),
        "score": (result or {}).get("score"),
        "seconds": night["measured"]["seconds"] if night is not None else None,
        "by_kind": kinds,
    }


def first_merge(history: list[Entry], line: Entry, movement: dict[str, int]) -> dict[str, int]:
    """The movement of this night as it was the first time the night was merged.

    A run written again finds its own entries already in the register and
    would report nothing new; what it brought is what the first merge counted.
    """
    for old in history:
        earlier = old.get("register")
        if (old.get("run"), old.get("head")) == (line["run"], line["head"]) and earlier:
            return {**movement, **{key: earlier[key] for key in ("new", "left", "gone")}}
    return movement


def with_night(history: list[Entry], line: Entry) -> list[Entry]:
    """The history with this night in it once: a run written again replaces its line."""
    others = [
        old for old in history if (old.get("run"), old.get("head")) != (line["run"], line["head"])
    ]
    return sorted([*others, line], key=lambda old: (old["date"], str(old["run"])))


def table(headers: list[str], rows: list[list[object]]) -> list[str]:
    rule = "|---" + "|---:" * (len(headers) - 1) + "|"
    body = ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return ["| " + " | ".join(headers) + " |", rule, *body, ""]


def _share(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f} %" if whole else "n/a"


def _counted(register: list[Entry], field: str, limit: int | None = None) -> list[list[object]]:
    counts = Counter(str(entry.get(field) or "not recorded") for entry in register)
    return [[name, count] for name, count in sorted(counts.items(), key=lambda c: (-c[1], c[0]))][
        :limit
    ]


def _changed(entry: Entry) -> list[str]:
    lines = list(normalise_mutation(entry.get("diff") or ""))
    return lines[:CHANGED_LINES_SHOWN] or ["(no diff recovered)"]


def _kinds(night: Entry | None) -> list[str]:
    if night is None:
        return ["Not recorded: the night has no `mutation-night.json`.", ""]
    rows = [
        [
            name,
            row["mutants"],
            row["by_status"].get("killed", 0),
            row["by_status"].get("survived", 0),
            _share(row["by_status"].get("killed", 0), row["mutants"]),
            f"{row['seconds']:.0f} s",
        ]
        for name, row in sorted(night["by_kind"].items(), key=lambda k: (-k[1]["mutants"], k[0]))
    ]
    return table(["kind", "mutants", "killed", "survived", "killed %", "time"], rows)


def _trend(history: list[Entry]) -> list[str]:
    rows = []
    for line in history[-NIGHTS_SHOWN:][::-1]:
        counts = line.get("result") or {}
        total, killed = counts.get("total") or 0, counts.get("killed") or 0
        rows.append(
            [
                line["date"],
                line["verdict"],
                total,
                _share(killed, total),
                counts.get("survived", "n/a"),
                counts.get("timeout", "n/a"),
                (line.get("register") or {}).get("open", "n/a"),
            ]
        )
    return table(["night", "verdict", "mutants", "killed %", "survived", "timeout", "open"], rows)


def _links(args: argparse.Namespace, night_dir: str) -> dict[str, str]:
    base = f"https://github.com/{args.repo}/blob/{BRANCH}"
    return {
        "report": f"{base}/nights/{night_dir}/report.md",
        "register": f"{base}/{REGISTER}",
        "history": f"{base}/{HISTORY}",
    }


def _moved(movement: dict[str, int]) -> str:
    return (
        f"**{movement['open']}** open in {movement['files']} file(s); this night "
        f"{movement['new']} new, {movement['left']} left, "
        f"{movement['gone']} gone with their function"
    )


def issue_section(
    register: list[Entry], history: list[Entry], movement: dict[str, int], links: dict[str, str]
) -> str:
    """The register as it stands and the last nights, for the body of the issue."""
    lines = [
        "## Mutants left alive, all nights",
        "",
        f"- {_moved(movement)}",
        f"- [This night in full]({links['report']}) · [register]({links['register']}) · "
        f"[history]({links['history']})",
        "",
        *table(["kind", "open"], _counted(register, "kind", FILES_SHOWN)),
        *table(["file", "open"], _counted(register, "file", FILES_SHOWN)),
        *_trend(history),
    ]
    return "\n".join(lines)


def comment(line: Entry, movement: dict[str, int], links: dict[str, str]) -> str:
    """The night in a few lines, for a comment on the issue."""
    counts = line.get("result") or {}
    total, killed = counts.get("total") or 0, counts.get("killed") or 0
    claims = line.get("claims") or {}
    claimed_line = (
        [
            f"- Claims by commits in the range: {claims['claimed']}, of them killed "
            f"{claims.get(KILLED, 0)}, still alive {claims.get(STILL_ALIVE, 0)}"
        ]
        if claims
        else []
    )
    return "\n".join(
        [
            f"**{line['date']}** · verdict **{line['verdict']}** · head `{line['head'][:8]}`",
            "",
            f"- Mutants: {total}, killed {killed} ({_share(killed, total)}), "
            f"survived {counts.get('survived', 'n/a')}, timeout {counts.get('timeout', 'n/a')}",
            f"- Register: {_moved(movement)}",
            *claimed_line,
            f"- [Report]({links['report']}) · [run]({line['run_url']})",
            "",
        ]
    )


def _not_killed(tonight: list[Entry]) -> list[str]:
    lines = [f"## Not killed this night ({len(tonight)})", ""]
    by_file: dict[str, list[Entry]] = {}
    for entry in tonight:
        by_file.setdefault(entry["file"], []).append(entry)
    for file, entries in sorted(by_file.items()):
        lines += [f"<details><summary><code>{file}</code>: {len(entries)}</summary>", ""]
        for entry in entries:
            where = f"{entry['class']}." if entry.get("class") else ""
            lines += [
                f"- `{where}{entry['symbol']}` · {entry['status']} · "
                f"{entry.get('kind') or 'kind not recorded'} · `{entry['key']}`",
                "  ```diff",
                *(f"  {line}" for line in _changed(entry)),
                "  ```",
            ]
        lines += ["", "</details>", ""]
    return lines


def report(
    line: Entry,
    night: Entry | None,
    tonight: list[Entry],
    register: list[Entry],
    movement: dict[str, int],
    notes: list[str],
    outcomes: list[Entry],
) -> str:
    """The night in full, as Markdown."""
    counts = line.get("result") or {}
    lines = [
        f"# Nightly mutation, {line['date']}",
        "",
        f"- Verdict: **{line['verdict']}**"
        f"{'' if line['complete'] else ' (not every mutant in the range was measured)'}",
        f"- Range: `{line['base']}...{line['head']}`",
        f"- Run: {line['run_url']}",
        f"- Written by: {line['written_by']}",
        f"- Counts: `{json.dumps(counts, sort_keys=True)}`, score {line.get('score')}",
        *(f"- Note: {note}" for note in notes),
        "",
        "## By kind",
        "",
        *_kinds(night),
    ]
    if night is not None:
        files = sorted(
            night["by_file"].items(),
            key=lambda f: (f[1]["by_status"].get("killed", 0) - f[1]["mutants"], f[0]),
        )
        lines += ["## By file", ""]
        lines += table(
            ["file", "mutants", "killed", "killed %", "time"],
            [
                [
                    name,
                    row["mutants"],
                    row["by_status"].get("killed", 0),
                    _share(row["by_status"].get("killed", 0), row["mutants"]),
                    f"{row['seconds']:.0f} s",
                ]
                for name, row in files
            ],
        )
        lines += ["## Functions with mutants left alive", ""]
        lines += table(
            ["function", "tests", "alive", "mutants", "time"],
            [
                [
                    f"`{row['function']}`",
                    row["tests"],
                    row["mutants"] - row["by_status"].get("killed", 0),
                    row["mutants"],
                    f"{row['seconds']:.0f} s",
                ]
                for row in night["by_function"]
                if row["mutants"] > row["by_status"].get("killed", 0)
            ],
        )
        lines += ["## Slowest mutants", ""]
        lines += table(
            ["mutant", "status", "kind", "seconds", "estimate"],
            [
                [f"`{m['id']}`", m["status"], m["kind"], m["seconds"], m["estimate_seconds"]]
                for m in night["slowest"]
            ],
        )
    lines += _claims(outcomes)
    lines += _not_killed(tonight)
    lines += ["## The register after this night", "", f"- {_moved(movement)}", ""]
    lines += table(["kind", "open"], _counted(register, "kind"))
    lines += table(["file", "open"], _counted(register, "file"))
    oldest = sorted(register, key=lambda e: (e["first_seen"]["date"], e["file"], e["key"]))[
        :OLDEST_SHOWN
    ]
    lines += table(
        ["oldest entries", "first seen", "kind"],
        [
            [
                f"`{e['file']}` `{e['symbol']}` `{e['key']}`",
                e["first_seen"]["date"],
                e.get("kind") or "not recorded",
            ]
            for e in oldest
        ],
    )
    return "\n".join(lines)


def write_lines(path: Path, entries: list[Entry]) -> None:
    text = "".join(
        json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries
    )
    path.write_text(text, encoding="utf-8")


def run_merge(args: argparse.Namespace) -> int:
    result = read_object(args.result)
    night = read_object(args.night)
    mutants = read_lines(args.mutants)
    register = read_lines(args.register_in)
    history = read_lines(args.history_in)
    notes: list[str] = []
    kinds = {record["id"]: record for record in mutants}
    tonight, equivalents = alive(result, kinds)
    if equivalents:
        notes.append(f"{equivalents} registered equivalent(s) are not in the register")
    measured: set[Function] = set()
    if not args.complete:
        merged, movement = register, unchanged(register)
        notes.append("the register was not changed: only a completed night changes it")
    else:
        # A function with a mutant left alive tonight was measured tonight,
        # whatever the per-mutant record holds. Left out of `measured`, its
        # entries of earlier nights stayed beside tonight's: one key, two lines (#993).
        measured = {function_of(record) for record in mutants}
        measured |= {function_of(entry) for entry in tonight}
        if not mutants:
            notes.append(
                "no per-mutant record: only functions with a mutant left alive were replaced, "
                "so a function a test now kills completely keeps its old entries"
            )
        stamp = {"date": args.date, "commit": args.head, "run": args.run_id}
        merged, movement = merge(register, tonight, measured, stamp, args.root)
    claims = (read_object(args.claims) or {}).get("claims") or []
    outcomes = claim_outcomes(claims, register, merged, measured)
    line = {**history_line(args, result, night), "notes": notes}
    if outcomes:
        line["claims"] = {"claimed": len(outcomes), **Counter(c["outcome"] for c in outcomes)}
    movement = first_merge(history, line, movement)
    line["register"] = movement
    history = with_night(history, line)
    night_dir = f"{args.date}-{args.head[:8]}"
    links = _links(args, night_dir)
    branch = args.out / "branch"
    (branch / "nights" / night_dir).mkdir(parents=True, exist_ok=True)
    write_lines(branch / REGISTER, merged)
    write_lines(branch / HISTORY, history)
    (branch / "nights" / night_dir / "report.md").write_text(
        report(line, night, tonight, merged, movement, notes, outcomes), encoding="utf-8"
    )
    if args.night is not None and args.night.is_file():
        shutil.copyfile(args.night, branch / "nights" / night_dir / "night.json")
    (args.out / "issue-section.md").write_text(
        issue_section(merged, history, movement, links), encoding="utf-8"
    )
    (args.out / "comment.md").write_text(comment(line, movement, links), encoding="utf-8")
    (branch / "README.md").write_text(README, encoding="utf-8")
    (args.out / "commit-message.txt").write_text(
        f"night {night_dir}: {args.verdict}, {movement['open']} open\n", encoding="utf-8"
    )
    print(f"register: {_moved(movement).replace('**', '')}; night {night_dir}")
    return 0


def _flag(text: str) -> bool:
    if text not in ("true", "false"):
        raise argparse.ArgumentTypeError(f"must be true or false, got {text!r}")
    return text == "true"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    merge_args = commands.add_parser("merge")
    for name in ("--result", "--register-in", "--history-in", "--out"):
        merge_args.add_argument(name, required=True, type=Path)
    for name in ("--night", "--mutants", "--claims"):
        merge_args.add_argument(name, type=Path)
    for name in ("--date", "--run-id", "--run-url", "--base", "--head", "--verdict", "--repo"):
        merge_args.add_argument(name, required=True)
    merge_args.add_argument("--complete", required=True, type=_flag)
    merge_args.add_argument("--root", type=Path, default=Path())
    merge_args.add_argument("--written-by", default="the nightly-mutation lane")
    return run_merge(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
