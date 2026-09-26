#!/usr/bin/env python3
"""One digest for a whole published tree, so two machines can be compared (#242).

ADR-0013's determinism golden test asks for a byte-identical output tree
"twice, and across machines". Twice is a unit test; across machines cannot
be, because a unit test only ever sees the machine it runs on. This script
is the half a single machine can do: build a fixed fixture corpus with the
real emitter and reduce the output to one SHA-256. The PR workflow
`publish-determinism.yml` runs it on runners that differ in operating
system and Python, and `--compare` fails when any two digests differ.

The digest covers relative paths and file bytes only — never mtimes,
permissions or directory order — and the paths are hashed exactly as the
filesystem returns them. Normalising them (Unicode form, case) would hide
the very difference a macOS runner is there to expose.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from lovspor.publish.emit import emit_site

DIGEST_FILE = "digest.txt"
LISTING_FILE = "listing.txt"
MIN_MACHINES = 2

_LAW = """---
title: "Testloven"
language: "nb"
ref_id: "lov/2020-01-01-1"
retrieved_at: "2026-01-01T00:00:00+00:00"
---

# Testloven

## Kapittel 1. Innledning

### § 1. Formål

Se [forskriften § 2](forskrift/2021-02-02-2/§2) og [direktivet](eu/32006L0123).

### § 2. Virkeområde

Tekst to — «æøå».
"""

_REGULATION = """---
title: "Testforskriften"
language: "nn"
ref_id: "forskrift/2021-02-02-2"
retrieved_at: "2026-01-02T00:00:00+00:00"
---

# Testforskriften

### § 2. Krav

Krav her.
"""

_DUPLICATE_PID = """---
title: "Dobbeltloven"
language: "nb"
ref_id: "lov/2022-03-03-3"
retrieved_at: "2026-01-03T00:00:00+00:00"
---

# Dobbeltloven

### § 1. En

A.

### § 1. To

B.
"""

# A non-ASCII path is the likeliest cross-machine difference: macOS and Linux
# disagree about Unicode normalisation of file names, and git has a setting
# (core.precomposeunicode) that exists only because of it.
_DOCUMENTS = {
    "doc-1": ("lov", "lover/testloven.md", "testloven", _LAW),
    "doc-2": ("forskrift", "forskrifter/testforskriften.md", "testforskriften", _REGULATION),
    "doc-3": ("lov", "lover/dobbeltloven.md", "dobbeltloven", _DUPLICATE_PID),
    "doc-4": (
        "lov",
        "lover/vimpel-føring.md",
        "vimpel-føring",
        _LAW.replace("Testloven", "Vimpelføringloven").replace(
            "lov/2020-01-01-1", "lov/1933-04-04-4"
        ),
    ),
}


def tree_listing(root: Path) -> str:
    """One `path<TAB>sha256` line per file, sorted by the path's UTF-8 bytes."""
    entries = sorted(
        (
            (path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest())
            for path in root.rglob("*")
            if path.is_file()
        ),
        key=lambda entry: entry[0].encode("utf-8"),
    )
    return "".join(f"{rel}\t{digest}\n" for rel, digest in entries)


def tree_digest(root: Path) -> str:
    return hashlib.sha256(tree_listing(root).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    # The runner's own git configuration must not reach the fixture: a global
    # signing key, hook path or default branch would make the corpus, not the
    # emitter, differ between machines.
    env = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_DATE": "2026-02-01T00:00:00Z",
        "GIT_COMMITTER_DATE": "2026-02-01T00:00:00Z",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    done = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return done.stdout.strip()


def _manifest() -> dict[str, object]:
    documents = {
        doc_id: {
            "doc_type": doc_type,
            "xml_hash": "a" * 64,
            "markdown_path": path,
            "source_dataset": "gjeldende-lover",
            "status": "current",
            "slug": slug,
            "title": slug,
            "renderer_version": 8,
            "last_seen": "2026-01-01T00:00:00Z",
        }
        for doc_id, (doc_type, path, slug, _) in _DOCUMENTS.items()
    }
    return {"version": 1, "generated_at": "2026-01-01T00:00:00Z", "documents": documents}


def build_fixture_corpus(repo: Path) -> str:
    """Write and commit the fixture corpus; return the pinned commit."""
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "--initial-branch=main")
    for _, path, _, text in _DOCUMENTS.values():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    (repo / "manifest.json").write_text(json.dumps(_manifest()), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "fixture")
    return _git(repo, "rev-parse", "HEAD")


def build_fixture_site(work: Path) -> Path:
    """Build the fixture corpus with the real emitter; return the output tree."""
    repo = work / "lovverk"
    out = work / "site"
    emit_site(repo, build_fixture_corpus(repo), out)
    return out


def write_digest(work: Path, dest: Path) -> str:
    site = build_fixture_site(work)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / LISTING_FILE).write_text(tree_listing(site), encoding="utf-8")
    digest = tree_digest(site)
    (dest / DIGEST_FILE).write_text(digest + "\n", encoding="utf-8")
    return digest


def compare(roots: list[Path]) -> list[str]:
    """Problems found across per-machine digest directories; empty means agreement."""
    if len(roots) < MIN_MACHINES:
        return [f"need at least {MIN_MACHINES} digests to compare, got {len(roots)}"]
    digests = {
        root.name: (root / DIGEST_FILE).read_text(encoding="utf-8").strip() for root in roots
    }
    if len(set(digests.values())) == 1:
        return []
    reference = roots[0]
    problems = [f"{name}: {digest}" for name, digest in sorted(digests.items())]
    problems.extend(_listing_differences(reference, roots[1:]))
    return problems


def _listing_differences(reference: Path, others: list[Path]) -> list[str]:
    base = set((reference / LISTING_FILE).read_text(encoding="utf-8").splitlines())
    found: list[str] = []
    for other in others:
        lines = set((other / LISTING_FILE).read_text(encoding="utf-8").splitlines())
        found.extend(f"only on {reference.name}: {line}" for line in sorted(base - lines))
        found.extend(f"only on {other.name}: {line}" for line in sorted(lines - base))
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--work", type=Path, help="scratch directory for the fixture build")
    parser.add_argument("--out", type=Path, help="directory to write digest and listing into")
    parser.add_argument("--compare", type=Path, nargs="+", help="per-machine digest directories")
    args = parser.parse_args(argv)
    if args.compare:
        problems = compare(args.compare)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1 if problems else 0
    if args.work is None or args.out is None:
        parser.error("--work and --out are required unless --compare is given")
    print(write_digest(args.work, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
