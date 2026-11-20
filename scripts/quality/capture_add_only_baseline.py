#!/usr/bin/env python3
"""Capture the production URL baseline for the ADD-ONLY contract (read-only).

Reads one pinned lovverk commit through the production publisher's own code
(``CorpusSnapshot``, ``build_inventory``, ``build_redirect_map``) and prints a
JSON baseline:

- every current document's canonical URL, with the manifest fields it is
  derived from (``doc_type``, ``slug``, ``status``);
- every removed record's ``doc_type`` and ``slug``;
- the 301/410 map as counts and the sha256 of the bytes the release writes
  (``redirect_map_json``, ``caddy_snippet``).

It runs ``git`` reads only (``cat-file``, ``log``) and writes nothing in the
corpus. ``tests/unit/test_add_only_contract.py`` checks the committed baseline
against the engine in CI. Re-running this script against the same commit must
reproduce the file byte for byte (``--check``).

usage: capture_add_only_baseline.py --corpus <lovverk clone> --sha <commit> [--check FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from lovspor.publish.inventory import build_inventory
from lovspor.publish.pages import document_url
from lovspor.publish.redirects import build_redirect_map, caddy_snippet, redirect_map_json
from lovspor.snapshot import CorpusSnapshot


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def capture(corpus: Path, sha: str) -> dict[str, object]:
    snapshot = CorpusSnapshot(corpus, sha)
    inventory = build_inventory(snapshot.manifest, snapshot.read_text)
    redirect_map = build_redirect_map(corpus, sha, inventory, snapshot.manifest)
    records = snapshot.manifest.documents
    return {
        "lovverk_commit": sha,
        "current": {
            plan.doc_id: {
                "doc_type": records[plan.doc_id].doc_type,
                "slug": plan.slug,
                "url": document_url(plan),
            }
            for plan in inventory.documents
        },
        "removed": {
            doc_id: {"doc_type": record.doc_type, "slug": record.slug}
            for doc_id, record in records.items()
            if record.status != "current"
        },
        "redirects": {
            "count_301": len(redirect_map.redirects),
            "count_410": len(redirect_map.gone),
            "redirect_map_json_sha256": _sha256(redirect_map_json(redirect_map)),
            "caddy_snippet_sha256": _sha256(caddy_snippet(redirect_map)),
        },
    }


def render(baseline: dict[str, object]) -> str:
    return json.dumps(baseline, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--check", type=Path, help="compare with a committed baseline")
    args = parser.parse_args()
    text = render(capture(args.corpus, args.sha))
    if args.check is None:
        sys.stdout.write(text)
        return 0
    same = args.check.read_text(encoding="utf-8") == text
    print("baseline reproduced" if same else "baseline DIFFERS from the engine's output")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())
