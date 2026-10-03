"""The files one promotion writes, byte for byte, and the refusals that guard them (ADR-0016 4d).

Every file is written the one way ``lovverk`` writes JSON — sorted keys,
two-space indent, the text itself rather than ``\\u`` escapes, a final
newline — so an unchanged input is byte-identical on disk and a rerun is a
no-op. A version is appended only behind the versions already promoted.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import BaseModel

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.decisions import DECISIONS_FILENAME
from lovspor.promotion.writer import _json
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    Decision,
    approve,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LOCAL = "lokale-forskrifter"
CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
NON_ASCII_REASON = "Vedtatt forskrift; kilden lest på nett i sin helhet."


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path)


def _approve(sha256: str, tmp_path: Path, reason: str = Decision.reason) -> None:
    assert approve(sha256, Decision(reason=reason).write(tmp_path)).exit_code == 0


def _promoted(root: Path, corpus: Path, tmp_path: Path, payload: bytes = b"") -> str:
    sha256 = store(root, payload or html_page())
    _approve(sha256, tmp_path, NON_ASCII_REASON)
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return sha256


def _only(directory: Path) -> Path:
    [path] = directory.glob("*.json")
    return path


def _canonical(text: str) -> str:
    return json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _decided_at(root: Path) -> str:
    lines = (root / DECISIONS_FILENAME).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if '"kind":"decision"' in line][-1]["decided_at"]


class TestCanonicalJson:
    def test_the_observations_file_is_sorted_indented_unescaped_and_newline_terminated(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)

        text = _only(corpus / LOCAL / AUTHORITY / "observations").read_text(encoding="utf-8")

        assert text == _canonical(text)
        assert "lest på nett" in text
        assert text.startswith('{\n  "authority_id": "0301",\n')

    def test_the_local_manifest_is_sorted_indented_unescaped_and_newline_terminated(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        titled = ("Forskrift om tømming av slamavskillere, Eksempel kommune", *REGULATION_LINES[1:])
        _promoted(root, corpus, tmp_path, html_page(titled))

        text = (corpus / LOCAL / "manifest.json").read_text(encoding="utf-8")

        assert text == _canonical(text)
        assert '"title": "Forskrift om tømming av slamavskillere, Eksempel kommune"' in text

    def test_a_model_is_dumped_in_json_mode(self) -> None:
        class Stamped(BaseModel):
            at: datetime
            tags: frozenset[str]

        stamped = Stamped(at=datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC), tags=frozenset({"a"}))

        assert (
            _json(stamped) == '{\n  "at": "2026-08-19T15:17:23Z",\n  "tags": [\n    "a"\n  ]\n}\n'
        )


class TestAudit:
    def test_the_audit_names_the_central_ref_id_an_lf_id_was_read_from(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        lines = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
        _promoted(root, corpus, tmp_path, html_page(lines))

        observations = json.loads(_only(corpus / LOCAL / AUTHORITY / "observations").read_text())
        identity = observations["versions"][0]["promotion"]["identity"]

        assert identity["doc_id"] == "lf-20191212-2077"
        assert identity["ref_id"] == "forskrift/2019-12-12-2077"

    def test_the_manifest_is_dated_by_the_approval_not_the_clock(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)

        manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))

        assert manifest["generated_at"] == _decided_at(root)
        assert set(manifest) == {"documents", "generated_at", "version"}

    def test_the_source_status_of_a_retrieved_page_says_retrieved(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)

        observations = json.loads(_only(corpus / LOCAL / AUTHORITY / "observations").read_text())

        assert observations["source_status"] == {
            "outcome": "retrieved",
            "http_status": 200,
            "observed_at": "2026-08-19T15:17:23Z",
        }


class TestApply:
    def test_a_rerun_after_an_interrupted_write_completes_only_what_is_missing(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        sha256 = _promoted(root, corpus, tmp_path)
        manifest = corpus / LOCAL / "manifest.json"
        complete = manifest.read_bytes()
        manifest.write_text('{"documents": {}, "generated_at": null, "version": 1}\n', "utf-8")

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        assert [line for line in result.stdout.splitlines() if line.startswith("wrote ")] == [
            f"wrote {LOCAL}/manifest.json"
        ]
        assert manifest.read_bytes() == complete

    def test_a_first_promotion_creates_every_directory_it_writes_into(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        titled = ("Renovasjonsforskrift for Eksempel kommune", *REGULATION_LINES[1:])
        _promoted(root, corpus, tmp_path, html_page(titled))

        written = sorted(path for path in tree(corpus) if path.startswith(f"{LOCAL}/{AUTHORITY}/"))

        slug = "renovasjonsforskrift-for-eksempel-kommune"
        assert slug > "observations"
        assert written == [
            f"{LOCAL}/{AUTHORITY}/observations/{slug}.json",
            f"{LOCAL}/{AUTHORITY}/{slug}.md",
        ]

    def test_files_already_on_disk_are_compared_as_utf8_under_any_locale(
        self, root: Path, corpus: Path, tmp_path: Path, c_locale: None
    ) -> None:
        _promoted(root, corpus, tmp_path)
        sha256 = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + timedelta(days=5))
        _approve(sha256, tmp_path, NON_ASCII_REASON)

        result = promote("local", sha256, corpus)

        assert result.exit_code == 0, result.output
        assert "v2 ->" in result.stdout


def _observations_path(corpus: Path) -> Path:
    return _only(corpus / LOCAL / AUTHORITY / "observations")


class TestEarlierVersions:
    def _second(self, root: Path, tmp_path: Path, observed_at: datetime) -> str:
        sha256 = store(root, html_page(CHANGED), observed_at=observed_at)
        _approve(sha256, tmp_path)
        return sha256

    def test_an_observations_file_that_does_not_read_is_refused_by_path_and_version(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        path = _observations_path(corpus)
        path.write_text("{}", encoding="utf-8")
        sha256 = self._second(root, tmp_path, FIRST_SEEN + timedelta(days=5))

        result = promote("local", sha256, corpus)

        assert result.stderr == (
            f"Refused: {path.resolve()} does not read; version 2 cannot be appended to it\n"
        )

    def test_an_observations_file_missing_a_version_is_refused_with_the_range_it_needs(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        path = _observations_path(corpus)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["versions"][0]["version"] = 2
        path.write_text(json.dumps(document), encoding="utf-8")
        sha256 = self._second(root, tmp_path, FIRST_SEEN + timedelta(days=5))
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.stderr == f"Refused: {path.resolve()} does not list versions 1..1\n"
        assert tree(corpus) == before

    def test_content_first_observed_at_the_same_instant_as_the_current_version_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _promoted(root, corpus, tmp_path)
        sha256 = self._second(root, tmp_path, FIRST_SEEN)
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.stderr == (
            "Refused: this content was first observed before the current version; "
            "backfill orders that\n"
        )
        assert tree(corpus) == before
