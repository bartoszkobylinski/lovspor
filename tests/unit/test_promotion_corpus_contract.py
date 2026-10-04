"""What ``promote local`` writes satisfies the ``lovverk`` dataset contract (ADR-0016 3).

Two ways to say so. When ``LOVVERK_CHECKOUT`` names a ``lovverk`` checkout
carrying the local dataset (lovverk PR #11), its own
``scripts/check_corpus_integrity.py`` is run against the written corpus.
Independently of that, the same invariants are asserted here directly, so
the contract is held in CI where no ``lovverk`` checkout exists.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.corpus import LocalManifest, LocalRecord, manifest_text
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

FRONT_MATTER_KEYS = (
    "id",
    "slug",
    "type",
    "ref_id",
    "title",
    "authority",
    "hjemmel",
    "vedtatt",
    "vedtatt_av",
    "ikraft",
    "ikraft_text",
    "version",
    "content_hash",
    "observed_at_first",
    "source_url",
    "source_sha256",
    "source_provider",
    "source_license",
    "basis",
    "asserted",
    "language",
)
RECORD_FIELDS = {
    "doc_type",
    "source_dataset",
    "status",
    "slug",
    "title",
    "markdown_path",
    "renderer_version",
    "last_seen",
    "removed_reason",
    "authority_id",
    "authority_type",
    "content_hash",
    "version",
    "extractor_version",
}
AUDIT_FIELDS = {
    "decision",
    "reviewed_by_role",
    "decided_at",
    "reason",
    "reviewed_in_sample",
    "classifier",
    "extractor_version",
    "renderer_version",
    "source_form",
    "identity",
    "observations_through",
    "observations",
}
SECOND_URL = "https://eksempel.kommune.invalid/forskrifter/renovasjon-2021"
LATER_DATED = (
    REGULATION_LINES[0],
    REGULATION_LINES[1].replace("12.12.2019", "15.06.2021"),
    *REGULATION_LINES[2:],
)


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A corpus holding two promoted documents of one authority, both committed, with history."""
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    checkout = make_corpus(tmp_path)
    first = store(observatory, html_page())
    second = store(
        observatory, html_page(LATER_DATED), url=SECOND_URL, observed_at=FIRST_SEEN + timedelta(1)
    )
    for sha256 in (first, second):
        assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
        assert promote("local", sha256, checkout).exit_code == 0
        git(checkout, "add", "-A")
        git(checkout, "commit", "-q", "-m", f"promote(lokal-forskrift): {AUTHORITY}/{sha256} v1")
    assert invoke("promote", "history", "--corpus", str(checkout)).exit_code == 0
    return checkout


def _local_manifest(corpus: Path) -> dict[str, object]:
    return json.loads((corpus / "lokale-forskrifter" / "manifest.json").read_text("utf-8"))


def _front_matter(text: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    end = lines.index("---", 1)
    return [tuple(line.split(": ", 1)) for line in lines[1:end]]  # type: ignore[misc]


@pytest.mark.skipif(not os.environ.get("LOVVERK_CHECKOUT"), reason="LOVVERK_CHECKOUT is not set")
def test_lovverk_integrity_check_passes_on_the_written_corpus(corpus: Path) -> None:
    script = Path(os.environ["LOVVERK_CHECKOUT"]) / "scripts" / "check_corpus_integrity.py"
    spec = importlib.util.spec_from_file_location("lovverk_corpus_integrity", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.check(corpus) == []


def test_documents_sit_at_their_authority_and_slug(corpus: Path) -> None:
    documents = _local_manifest(corpus)["documents"]
    assert isinstance(documents, dict)
    for record in documents.values():
        assert record["markdown_path"] == f"lokale-forskrifter/{AUTHORITY}/{record['slug']}.md"
        assert (corpus / record["markdown_path"]).is_file()


def test_two_documents_with_one_title_get_distinct_slugs(corpus: Path) -> None:
    documents = _local_manifest(corpus)["documents"]
    assert isinstance(documents, dict)
    slugs = sorted(record["slug"] for record in documents.values())
    assert slugs == [
        "forskrift-om-renovasjon-og-slam-eksempel-kommune",
        "forskrift-om-renovasjon-og-slam-eksempel-kommune-2021-06-15",
    ]


def test_records_carry_exactly_the_local_fields_and_no_xml_hash(corpus: Path) -> None:
    documents = _local_manifest(corpus)["documents"]
    assert isinstance(documents, dict)
    for doc_id, record in documents.items():
        assert re.fullmatch(r"lk-0301-[0-9a-f]{12}", doc_id)
        assert set(record) == RECORD_FIELDS
        assert record["doc_type"] == "lokal-forskrift"
        assert record["source_dataset"] == "lokale-forskrifter"
        assert record["status"] == "current"


def test_front_matter_has_the_fixed_keys_in_order_and_agrees_with_the_record(
    corpus: Path,
) -> None:
    documents = _local_manifest(corpus)["documents"]
    assert isinstance(documents, dict)
    for doc_id, record in documents.items():
        pairs = _front_matter((corpus / record["markdown_path"]).read_text("utf-8"))
        values = {key: json.loads(raw) if key != "authority" else raw for key, raw in pairs}
        assert tuple(key for key, _ in pairs) == FRONT_MATTER_KEYS
        assert values["id"] == doc_id
        assert values["slug"] == record["slug"]
        assert values["version"] == record["version"]
        assert values["content_hash"] == record["content_hash"]
        assert values["source_license"] == "åndsverkloven § 14"
        assert (values["basis"], values["asserted"], values["type"]) == (
            "observed",
            False,
            "lokal-forskrift",
        )


def test_nothing_in_the_dataset_mentions_nlod(corpus: Path) -> None:
    for path in (corpus / "lokale-forskrifter").rglob("*"):
        if path.is_file():
            assert "nlod" not in path.read_text("utf-8").casefold(), path


def test_the_audit_record_says_who_approved_what_and_from_which_observations(
    corpus: Path,
) -> None:
    directory = corpus / "lokale-forskrifter" / AUTHORITY / "observations"
    path = directory / "forskrift-om-renovasjon-og-slam-eksempel-kommune.json"
    observations = json.loads(path.read_text("utf-8"))
    [version] = observations["versions"]
    audit = version["promotion"]
    assert version["primary_url"] == PAGE_URL
    assert version["observed_at_first"] == "2026-08-19T15:17:23Z"
    assert audit["decision"] == "approve"
    assert audit["reviewed_by_role"] == "project owner"
    assert set(audit) == AUDIT_FIELDS
    assert audit["reviewed_in_sample"] is True
    assert audit["extractor_version"] == 3
    assert audit["renderer_version"] == 1
    assert audit["identity"]["scheme"] == "lk"
    assert audit["observations"] == [
        {
            "observed_at": "2026-08-19T15:17:23Z",
            "url": PAGE_URL,
            "sha256": version["source_sha256s"][0],
        }
    ]


def test_history_is_written_as_json_only(corpus: Path) -> None:
    history = sorted((corpus / "lokale-forskrifter" / AUTHORITY / "history").iterdir())
    assert [path.suffix for path in history] == [".json", ".json"]


def _record(title: str) -> LocalRecord:
    return LocalRecord(
        status="current",
        slug="slamtomming",
        title=title,
        markdown_path="lokale-forskrifter/0301/slamtomming.md",
        renderer_version=1,
        last_seen="2026-08-19T15:17:23Z",
        authority_id="0301",
        authority_type="kommune",
        content_hash="0" * 64,
        version=1,
        extractor_version=1,
    )


def test_the_manifest_text_is_sorted_indented_unescaped_and_newline_terminated() -> None:
    manifest = LocalManifest(documents={"lk-0301-000000000000": _record("Forskrift om tømming")})

    text = manifest_text(manifest)

    assert text == (
        json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    assert '      "title": "Forskrift om tømming",\n' in text
    assert text.index('"authority_id"') < text.index('"doc_type"') < text.index('"version": 1')


def test_a_field_a_newer_writer_added_is_kept_as_json() -> None:
    extended = LocalManifest(refreshed_at=datetime(2026, 8, 19, 15, 17, 23, tzinfo=UTC))

    text = manifest_text(extended)

    assert json.loads(text)["refreshed_at"] == "2026-08-19T15:17:23Z"
