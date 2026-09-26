"""Whole-tree publication invariants that were only pinned in part (ADR-0013 Validation).

Three rows of the validation list, each asserted over the complete emitted
tree of a real throwaway git corpus rather than over one chosen page:

* **No wall clock** — two builds of one snapshot under two different clocks
  (different days, different time zones) are byte-identical. The clock is
  frozen at the interpreter boundary (``datetime``/``date``/``time``), the
  same kind of environment control as pinning ``COLUMNS``; no publish logic
  is replaced.
* **No invented disambiguation URL** — a document with a reused section id
  publishes its document page only, and no byte anywhere in the tree (pages,
  twins, sitemaps, redirect maps, manifest) names a URL beneath it other than
  its companion and the anchors of its *unique* pids.
* **Companion envelope** — every ``index.json`` in the tree validates against
  the ADR-0013 section-4 minimum envelope and names its own location.
  No versioned schema document exists in the implementation yet (#408), so
  the envelope asserted here is the ADR's, restricted to what the emitter
  promises to carry.
"""

import datetime as datetime_module
import json
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import jsonschema
import pytest

from lovspor.publish import emit, inventory
from lovspor.publish.emit import emit_site
from lovspor.publish.pages import SITE_ORIGIN
from tests.unit.test_publish_emit import _record, _run_git, corpus  # noqa: F401 — fixture reuse

_REAL_DATETIME = datetime_module.datetime
_REAL_DATE = datetime_module.date

PARTLY_DUPLICATED = """---
title: "Delvisloven"
language: "nb"
ref_id: "lov/2023-04-04-7"
retrieved_at: "2026-01-04T00:00:00+00:00"
---

# Delvisloven

### § 1. Første

A.

### § 1. Andre

B.

### § 2. Eneste

C.
"""

CITING = """---
title: "Henvisningsloven"
language: "nb"
ref_id: "lov/2024-05-05-8"
retrieved_at: "2026-01-05T00:00:00+00:00"
---

# Henvisningsloven

### § 1. Henvisninger

Se [delvisloven § 1](lov/2023-04-04-7/§1) og [delvisloven § 2](lov/2023-04-04-7/§2).
"""

HEX40 = "^[0-9a-f]{40}$"
HEX64 = "^[0-9a-f]{64}$"
UTC_INSTANT = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"

_SOURCE = {
    "type": "object",
    "required": ["provider", "dataset", "license", "statement"],
    "properties": {
        "provider": {"const": "Lovdata"},
        "dataset": {"type": "string", "minLength": 1},
        "license": {"const": "NLOD 2.0"},
        "statement": {"type": "string", "pattern": "NLOD 2.0"},
    },
}

_PROVENANCE = {
    "type": "object",
    "required": [
        "source_revision",
        "representation_hash",
        "xml_hash",
        "renderer_version",
        "retrieved_at",
    ],
    "properties": {
        "source_revision": {"type": "string", "pattern": HEX40},
        "representation_hash": {"type": "string", "pattern": HEX64},
        "xml_hash": {"type": "string", "pattern": HEX64},
        "renderer_version": {"type": "integer", "minimum": 1},
        "retrieved_at": {"type": "string", "pattern": UTC_INSTANT},
    },
    "not": {"required": ["corpus_commit"]},
}

_TEMPORAL = {
    "type": "object",
    "required": ["status", "note"],
    "properties": {
        "status": {"const": "current"},
        "note": {"type": "string", "pattern": "not an applicability-at-date"},
    },
}

# ADR-0013 section 4's minimum envelope. Its ``links.source`` and
# ``links.json_schema`` are not required: the emitter names no deep link it
# cannot prove stable (Decision 5), and no schema document is versioned yet (#408).
COMPANION_ENVELOPE = {
    "type": "object",
    "required": [
        "schema_version",
        "canonical_id",
        "canonical_url",
        "type",
        "ref_id",
        "slug",
        "title",
        "language",
        "text",
        "source",
        "provenance",
        "temporal",
        "links",
    ],
    "properties": {
        "schema_version": {"const": "1"},
        "canonical_id": {"type": "string", "minLength": 1},
        "canonical_url": {"type": "string", "pattern": f"^{re.escape(SITE_ORIGIN)}/.+/$"},
        "type": {"enum": ["lov", "forskrift", "paragraf"]},
        "ref_id": {"type": "string", "pattern": "^(lov|forskrift)/"},
        "slug": {"type": "string", "minLength": 1},
        "title": {"type": "string", "minLength": 1},
        "language": {"enum": ["nb", "nn"]},
        "text": {"type": "string"},
        "source": _SOURCE,
        "provenance": _PROVENANCE,
        "temporal": _TEMPORAL,
        "links": {
            "type": "object",
            "required": ["parent"],
            "properties": {"parent": {"type": ["string", "null"]}},
        },
    },
}


def _tree(root: Path) -> dict[Path, bytes]:
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _frozen_classes(instant: datetime_module.datetime) -> tuple[type, type]:
    class FrozenDatetime(_REAL_DATETIME):
        @classmethod
        def now(cls, tz: datetime_module.tzinfo | None = None) -> "FrozenDatetime":
            moment = instant.astimezone(tz)
            if tz is None:
                moment = moment.replace(tzinfo=None)
            return cls.fromisoformat(moment.isoformat())

        @classmethod
        def utcnow(cls) -> "FrozenDatetime":
            return cls.now(datetime_module.UTC).replace(tzinfo=None)

        @classmethod
        def today(cls) -> "FrozenDatetime":
            return cls.now()

    class FrozenDate(_REAL_DATE):
        @classmethod
        def today(cls) -> "FrozenDate":
            return cls.fromisoformat(instant.astimezone().date().isoformat())

    return FrozenDatetime, FrozenDate


def _freeze_clock(
    monkeypatch: pytest.MonkeyPatch,
    instant: datetime_module.datetime,
    zone: str,
) -> None:
    """Every clock the interpreter offers now reads ``instant`` in ``zone``."""
    monkeypatch.setenv("TZ", zone)
    time.tzset()
    frozen_datetime, frozen_date = _frozen_classes(instant)
    monkeypatch.setattr(datetime_module, "datetime", frozen_datetime)
    monkeypatch.setattr(datetime_module, "date", frozen_date)
    for module in [m for name, m in sys.modules.items() if name.startswith("lovspor")]:
        for attribute, value in list(vars(module).items()):
            if value is _REAL_DATETIME:
                monkeypatch.setattr(module, attribute, frozen_datetime)
            elif value is _REAL_DATE:
                monkeypatch.setattr(module, attribute, frozen_date)
    stamp = instant.timestamp()
    monkeypatch.setattr(time, "time", lambda: stamp)
    monkeypatch.setattr(time, "time_ns", lambda: int(stamp * 1_000_000_000))


def _thaw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.undo()
    time.tzset()


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """A clock-freezing monkeypatch whose teardown also re-reads the real TZ."""
    yield monkeypatch
    _thaw(monkeypatch)


def _build_under_clock(
    clock: pytest.MonkeyPatch,
    snapshot: tuple[Path, str],
    moment: tuple[datetime_module.datetime, str],
    out: Path,
) -> str:
    """Build under a frozen clock; return what the interpreter's clock read."""
    repo, sha = snapshot
    _freeze_clock(clock, *moment)
    emit_site(repo, sha, out)
    seen = f"{datetime_module.datetime.now().isoformat()} {time.strftime('%Z')}"
    _thaw(clock)
    return seen


class TestNoWallClock:
    def test_builds_on_two_days_in_two_zones_are_byte_identical(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        clock: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        first = (_REAL_DATETIME(2026, 1, 1, 3, 0, tzinfo=datetime_module.UTC), "UTC")
        second = (
            _REAL_DATETIME(2031, 7, 15, 22, 30, tzinfo=datetime_module.UTC),
            "Pacific/Chatham",
        )

        seen_first = _build_under_clock(clock, corpus, first, tmp_path / "one")
        seen_second = _build_under_clock(clock, corpus, second, tmp_path / "two")

        assert seen_first != seen_second
        one, two = _tree(tmp_path / "one"), _tree(tmp_path / "two")
        assert sorted(one) == sorted(two)
        assert [path for path in one if one[path] != two[path]] == []

    def test_the_frozen_clock_reaches_the_publish_modules_own_names(
        self,
        clock: pytest.MonkeyPatch,
    ) -> None:
        # The byte-identity test is only as strong as the freeze: a publish
        # module whose bound ``datetime`` escaped it would read the real clock
        # in both builds and agree vacuously.
        moment = _REAL_DATETIME(2031, 7, 15, 22, 30, tzinfo=datetime_module.UTC)
        _freeze_clock(clock, moment, "UTC")

        assert emit.datetime.now(datetime_module.UTC) == moment
        assert inventory.datetime.now(datetime_module.UTC) == moment
        assert inventory.date.today() == moment.date()
        assert time.time() == moment.timestamp()


def _add_partly_duplicated_documents(repo: Path) -> str:
    (repo / "lover/delvisloven.md").write_text(PARTLY_DUPLICATED, encoding="utf-8")
    (repo / "lover/henvisningsloven.md").write_text(CITING, encoding="utf-8")
    manifest_path = repo / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"]["doc-5"] = _record(
        "lov", "lover/delvisloven.md", "delvisloven", "Delvisloven"
    )
    manifest["documents"]["doc-6"] = _record(
        "lov", "lover/henvisningsloven.md", "henvisningsloven", "Henvisningsloven"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-q", "-m", "partly duplicated")
    return _head(repo)


def _urls_beneath(tree: dict[Path, bytes], slug_path: str) -> set[str]:
    """Every suffix any file in the tree appends to ``slug_path``."""
    pattern = re.compile(re.escape(slug_path.encode("utf-8")) + rb"([^\"'<>\s)]*)")
    return {match.decode("utf-8") for data in tree.values() for match in pattern.findall(data)}


class TestDuplicatePidWholeTree:
    def test_no_file_names_a_url_beneath_a_duplicate_pid_document(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, _ = corpus
        sha = _add_partly_duplicated_documents(repo)
        emit_site(repo, sha, tmp_path / "site")
        tree = _tree(tmp_path / "site")

        beneath = _urls_beneath(tree, "/lov/delvisloven/")
        assert beneath <= {"", "index.json", "#paragraf-2"}
        assert {"", "index.json"} <= beneath
        assert _urls_beneath(tree, "/lov/dobbeltloven/") <= {"", "index.json"}

    def test_the_duplicate_pid_document_emits_its_document_page_only(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, _ = corpus
        sha = _add_partly_duplicated_documents(repo)
        emit_site(repo, sha, tmp_path / "site")
        tree = _tree(tmp_path / "site")

        own = {path for path in tree if path.parts[:2] == ("lov", "delvisloven")}
        assert own == {Path("lov/delvisloven/index.html"), Path("lov/delvisloven/index.json")}
        page = tree[Path("lov/delvisloven/index.html")]
        assert b'id="paragraf-2"' in page
        assert b'id="paragraf-1"' not in page

    def test_a_citation_of_a_duplicate_pid_document_lands_on_its_document_page(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, _ = corpus
        sha = _add_partly_duplicated_documents(repo)
        emit_site(repo, sha, tmp_path / "site")
        tree = _tree(tmp_path / "site")

        citing = tree[Path("lov/henvisningsloven/index.html")]
        assert citing.count(b'href="/lov/delvisloven/"') == 2


def _companions(tree: dict[Path, bytes]) -> dict[Path, dict[str, object]]:
    return {path: json.loads(data) for path, data in tree.items() if path.name == "index.json"}


class TestCompanionEnvelopeWholeTree:
    def test_every_companion_validates_against_the_minimum_envelope(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        emit_site(repo, sha, tmp_path / "site")
        companions = _companions(_tree(tmp_path / "site"))

        validator = jsonschema.Draft202012Validator(COMPANION_ENVELOPE)
        faults = {
            str(path): [error.message for error in validator.iter_errors(envelope)]
            for path, envelope in companions.items()
        }
        assert {path: errors for path, errors in faults.items() if errors} == {}
        assert len(companions) >= 8

    def test_every_companion_names_its_own_location(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        emit_site(repo, sha, tmp_path / "site")
        companions = _companions(_tree(tmp_path / "site"))

        misplaced = {
            str(path): envelope["canonical_url"]
            for path, envelope in companions.items()
            if envelope["canonical_url"] != f"{SITE_ORIGIN}/{path.parent.as_posix()}/"
        }
        assert misplaced == {}

    def test_every_provision_twin_names_an_emitted_parent(
        self,
        corpus: tuple[Path, str],  # noqa: F811
        tmp_path: Path,
    ) -> None:
        repo, sha = corpus
        emit_site(repo, sha, tmp_path / "site")
        companions = list(_companions(_tree(tmp_path / "site")).values())

        documents = [e for e in companions if e["type"] != "paragraf"]
        provisions = [e for e in companions if e["type"] == "paragraf"]
        parents = {e["canonical_url"] for e in documents}
        assert provisions
        assert all(e["links"] == {"parent": None} for e in documents)
        assert all(e["links"]["parent"] in parents for e in provisions)
