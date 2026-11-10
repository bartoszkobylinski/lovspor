"""Audit every operation whose act attribution differs between two engine versions (#861 4B).

Two steps, so the two versions never share a process:

    # at each commit, from its checkout, against the same pinned archives
    uv run python scripts/legal_time_attribution_audit.py dump CACHE_DIR OUT.json
    # then, once, from the newer checkout
    uv run python scripts/legal_time_attribution_audit.py diff BEFORE.json AFTER.json CACHE_DIR

``dump`` reads every amender of the eight coverage acts once (a member amending several of
them is one member) and records each operation's id, act, kind and instruction, plus every
sentence of the member that opens or closes an amendment block. ``diff`` prints one JSON
document: every operation whose act, kind, instruction or presence changed, with the block
sentence that governs it in the newer reading, the acts that sentence cites by day, month,
year and number, whether it cites exactly one and the member's header lists it
(``identity_proven``), and a class:

- ``new_proven``: unattributed before, bound now to the one and only act the governing
  sentence cites, which the header lists;
- ``rebound_proven``: bound before to an act the governing sentence does not cite, bound now
  to the act it does cite, which the header lists;
- ``unbound_was_false``: bound before to an act the governing sentence does not cite,
  unattributed now;
- ``intro_not_an_operation``: a proven intro sentence that was counted as an operation;
- ``check``: anything else. Zero is the merge condition.

Nothing here decides an attribution; it restates the readings and the evidence beside them.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from lovspor.legaltime import instructions
from lovspor.legaltime.archive import collect, read_members
from lovspor.legaltime.instructions import interpret
from lovspor.legaltime.text import split_sentences

ACTS = (
    "lov/2008-05-15-35", "lov/2005-05-20-28", "lov/2005-06-17-62", "lov/2003-07-04-84",
    "lov/2005-06-17-90", "lov/2009-06-19-58", "lov/2009-06-19-100", "lov/2021-06-18-97",
)  # fmt: skip
ARCHIVES = ("lovtidend-avd1-2001-2025.tar.bz2", "lovtidend-avd1-2026.tar.bz2")
# ``dump`` also runs at the older commit, where ``lovspor.legaltime.attribution`` does not exist
# yet: only ``diff`` imports it, at run time.
_ATTRIBUTION = "lovspor.legaltime.attribution"


def _governing(document: Any, op_id: str) -> str:
    """The last block sentence at or before the operation's node, in the newer reading."""
    attribution = importlib.import_module(_ATTRIBUTION)

    tag = op_id.split("#", 1)[1]
    part, rest = tag.rsplit(":", 1)
    nodes = document.parts[int(part.split(":")[0])].nodes
    changes = document.header.changes_to_documents
    index = int(rest.split(".")[0])
    for node in reversed(nodes[: index + 1]):
        if node.host_section != nodes[index].host_section:
            return ""  # an article boundary: no sentence of another article governs it
        text = node.text
        if attribution.is_intro(text):
            return text
        if instructions._unbound_intro(text) or attribution.bind(text, changes, True).opens:
            return split_sentences(text)[-1]
    return ""


def dump(cache: Path, out: Path) -> None:
    documents: dict[str, Any] = {}
    for act in ACTS:
        for document in collect(act, [cache / name for name in ARCHIVES]).amenders:
            documents.setdefault(document.header.refid, document)
    result = {}
    for refid, document in sorted(documents.items()):
        operations = interpret(document).operations
        result[refid] = {
            "member": document.member,
            "changes": list(document.header.changes_to_documents),
            "ops": [[op.id, op.act, op.kind, op.instruction] for op in operations],
        }
    out.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=1) + "\n")


def _class(before: list[Any] | None, after: list[Any] | None, cited: list[str], proof: bool) -> str:
    old, new = (before or [None, None])[1], (after or [None, None])[1]
    if after is None:
        return "intro_not_an_operation" if old is None and proof and cited else "check"
    if before is None:
        return "check"
    names_new = bool(cited) and cited[0] == new and proof
    if old is None and names_new:
        return "new_proven"
    if old is not None and old not in cited[:1] and (new is None or names_new):
        return "unbound_was_false" if new is None else "rebound_proven"
    return "check"


def _changed(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """Member refid → ids of operations whose reading differs between the two dumps."""
    result: dict[str, list[str]] = {}
    for refid in sorted(after):
        old = {op[0]: op for op in before[refid]["ops"]}
        new = {op[0]: op for op in after[refid]["ops"]}
        ids = [i for i in sorted(old.keys() | new.keys()) if old.get(i) != new.get(i)]
        if ids:
            result[refid] = ids
    return result


def _documents(after: dict[str, Any], refids: list[str], cache: Path) -> dict[str, Any]:
    """Read every member once per archive: one pass over each tarball, not one per member."""
    documents: dict[str, Any] = {}
    for index, name in enumerate(ARCHIVES):
        wanted = [
            after[r]["member"]
            for r in refids
            if after[r]["member"].startswith("lti/2026/") == bool(index)
        ]
        read = read_members(cache / name, wanted) if wanted else {}
        documents.update(
            {r: read[after[r]["member"]] for r in refids if after[r]["member"] in read}
        )
    return documents


def _row(
    before_op: list[Any] | None, after_op: list[Any] | None, sentence: str, changes: list[str]
) -> dict[str, Any]:
    citations = importlib.import_module(_ATTRIBUTION).citations
    cited = citations(sentence.split("§", maxsplit=1)[0] if sentence[:1].isdigit() else sentence)
    proof = len(cited) == 1 and cited[0] in changes  # one act, and the header lists it
    o, n = before_op, after_op
    return {
        "op": (n or o)[0], "before": o and o[1], "after": n and n[1], "kind": (n or o)[2],
        "instruction": (n or o)[3], "governing": sentence, "cited": cited,
        "identity_proven": proof, "class": _class(o, n, cited, proof),
    }  # fmt: skip


def diff(before_path: Path, after_path: Path, cache: Path) -> dict[str, Any]:
    before, after = (json.loads(p.read_text()) for p in (before_path, after_path))
    changed = _changed(before, after)
    documents = _documents(after, list(changed), cache)
    rows = []
    for refid, ids in changed.items():
        old = {op[0]: op for op in before[refid]["ops"]}
        new = {op[0]: op for op in after[refid]["ops"]}
        for op_id in ids:
            sentence = _governing(documents[refid], op_id)
            rows.append(_row(old.get(op_id), new.get(op_id), sentence, after[refid]["changes"]))
    classes: dict[str, int] = {}
    for row in rows:
        classes[row["class"]] = classes.get(row["class"], 0) + 1
    return {"classes": dict(sorted(classes.items())), "changed": len(rows), "operations": rows}


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        dump(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        report = diff(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
        print(json.dumps(report, ensure_ascii=False, indent=1))
