"""Fetch run schema 3: a normalized digest beside the raw one, nothing else changed (#738, FC1a).

The raw SHA-256 stays authoritative: it names the stored file, the proof checks
it, and a standing policy compares it. ``normalized_sha256`` is recorded for
HTML pages only (listed pages and the terms page, never robots.txt), with the
rule that produced it, so a later comparison can be recomputed from the raw
bytes. Schema 2 records, written before FC1a, are still read as they are.
"""

import hashlib
import json
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.errors import RegjeringenFetchError
from lovspor.forarbeider import release_commands
from lovspor.forarbeider.policy_source import read_source_run
from lovspor.regjeringen_fetch.fetch import (
    FetchSettings,
    check_source_proof,
    prepare_out,
    run_fetch,
    verified_source_bytes,
)
from lovspor.regjeringen_fetch.models import (
    FetchOutcome,
    FetchRun,
    NormalizationRule,
    SourceState,
)
from lovspor.regjeringen_fetch.normalize import NORMALIZATION_RULE, normalized_sha256
from tests.unit.cli_output import said
from tests.unit.test_forarbeider_policy_source import _desk_bound_to, _published, _show
from tests.unit.test_regjeringen_fetch import CH1, CH2, ROBOTS, _Clock

TERMS = "https://www.regjeringen.no/no/om-nettstedet/vilkar/id0000001/"
ROBOTS_BODY = b"User-agent: *\n"
Doc = dict[str, Any]


@pytest.fixture(autouse=True)
def _terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_commands, "_interactive", lambda: True)


def _page(nonce: str) -> bytes:
    return (
        f'<!DOCTYPE html><html><head><meta name="csp-nonce" content="{nonce}">'
        f'<script nonce="{nonce}"></script></head><body>synthetic</body></html>'
    ).encode()


def _fetch(
    httpx_mock: HTTPXMock,
    out: Path,
    pages: dict[str, bytes],
    terms: dict[str, Any] | None = None,
) -> FetchRun:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    if terms is not None:
        httpx_mock.add_response(url=TERMS, **terms)
    for url, body in pages.items():
        httpx_mock.add_response(url=url, content=body)
    terms_url = None if terms is None else TERMS
    settings = FetchSettings(clock=_Clock().as_fetch_clock(), terms_url=terms_url)
    with httpx.Client() as client:
        return run_fetch(client, tuple(pages), prepare_out(out), settings)


def _run_file(out: Path) -> Path:
    (record,) = sorted(out.glob("fetch-run-*.json"))
    return record


def test_a_new_run_is_schema_3_with_both_digests_per_page(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    body = _page("QUJDRA==")
    out = tmp_path / "pages"
    run = _fetch(httpx_mock, out, {CH1: body})
    (record,) = run.records
    assert run.schema_version == 3
    assert record.sha256 == hashlib.sha256(body).hexdigest()
    assert record.size == len(body)
    assert record.normalized_sha256 == normalized_sha256(body)
    assert record.normalized_sha256 != record.sha256
    assert record.normalization_rule == NORMALIZATION_RULE
    stored = json.loads(_run_file(out).read_bytes())
    assert stored["schema_version"] == 3
    assert stored["records"][0]["normalized_sha256"] == normalized_sha256(body)
    assert stored["records"][0]["normalization_rule"] == "regjeringen-csp-nonce/1"


def test_storage_stays_keyed_by_the_raw_digest_with_the_bytes_as_served(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    first, second = _page("QUJDRA=="), _page("WFlaVzEy")
    out = tmp_path / "pages"
    run = _fetch(httpx_mock, out, {CH1: first, CH2: second})
    one, two = run.records
    assert one.sha256 != two.sha256
    assert one.normalized_sha256 == two.normalized_sha256
    for record, body in ((one, first), (two, second)):
        assert (out / f"{record.sha256}.html").read_bytes() == body
    names = {path.name for path in out.glob("*.html")}
    assert names == {f"{one.sha256}.html", f"{two.sha256}.html"}


def test_robots_never_carries_a_normalized_digest_and_the_proof_refuses_one(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    run = _fetch(httpx_mock, tmp_path / "pages", {CH1: _page("QUJD")})
    assert run.robots.state is SourceState.PUBLISHED
    assert run.robots.normalized_sha256 is None
    assert run.robots.normalization_rule is None
    doc: Doc = json.loads(run.model_dump_json())
    doc["robots"] |= {"normalized_sha256": "0" * 64, "normalization_rule": NORMALIZATION_RULE}
    run = FetchRun.model_validate(doc)
    with pytest.raises(RegjeringenFetchError, match="source_proof_inconsistent: normalized_digest"):
        check_source_proof(run)


def test_published_terms_carry_a_normalized_digest(httpx_mock: HTTPXMock, tmp_path: Path) -> None:
    terms = _page("QUJD")
    run = _fetch(httpx_mock, tmp_path / "pages", {CH1: _page("QUJD")}, {"content": terms})
    assert run.terms is not None
    assert run.terms.sha256 == hashlib.sha256(terms).hexdigest()
    assert run.terms.normalized_sha256 == normalized_sha256(terms)
    assert run.terms.normalization_rule == NORMALIZATION_RULE


def test_unpublished_terms_and_unstored_pages_carry_no_normalized_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    httpx_mock.add_response(url=ROBOTS, content=ROBOTS_BODY)
    httpx_mock.add_response(url=TERMS, status_code=404)
    httpx_mock.add_response(url=CH1, status_code=500)
    settings = FetchSettings(clock=_Clock().as_fetch_clock(), terms_url=TERMS)
    with httpx.Client() as client:
        run = run_fetch(client, (CH1,), prepare_out(tmp_path / "pages"), settings)
    assert run.terms is not None
    assert run.terms.state is SourceState.ABSENT
    assert run.terms.normalized_sha256 is None
    (record,) = run.records
    assert record.outcome is FetchOutcome.HTTP_STATUS
    assert record.normalized_sha256 is None
    assert record.normalization_rule is None


# Schema 2: written before FC1a. Read as it is, never given invented values.

SCHEMA_2: Doc = {
    "schema_version": 2,
    "record_type": "regjeringen_fetch_run",
    "user_agent": "lovspor-forarbeider/1 (+https://lovspor.no; kontakt@lovspor.no)",
    "started_at": "2026-11-09T12:00:00Z",
    "robots": {
        "kind": "robots_txt",
        "requested_url": ROBOTS,
        "hops": [{"url": ROBOTS, "status": 200, "location": None}],
        "state": "published",
        "status": 200,
        "retrieved_at": "2026-11-09T12:00:00Z",
        "sha256": hashlib.sha256(ROBOTS_BODY).hexdigest(),
        "size": len(ROBOTS_BODY),
    },
    "terms": {
        "kind": "website_terms",
        "requested_url": TERMS,
        "hops": [{"url": TERMS, "status": 200, "location": None}],
        "state": "published",
        "status": 200,
        "retrieved_at": "2026-11-09T12:00:05Z",
        "sha256": "b" * 64,
        "size": 10,
    },
    "delay_seconds": 5.0,
    "records": [
        {
            "url": CH1,
            "outcome": "fetched",
            "retrieved_at": "2026-11-09T12:00:10Z",
            "status": 200,
            "sha256": "a" * 64,
            "size": 10,
            "location": None,
        }
    ],
}


def _copy(doc: Doc) -> Doc:
    copied: Doc = json.loads(json.dumps(doc))
    return copied


def test_a_schema_2_record_is_read_without_normalized_values() -> None:
    run = FetchRun.model_validate_json(json.dumps(SCHEMA_2))
    check_source_proof(run)
    assert run.schema_version == 2
    assert run.records[0].normalized_sha256 is None
    assert run.terms is not None
    assert run.terms.normalized_sha256 is None
    assert json.loads(run.model_dump_json(exclude_none=False))["schema_version"] == 2


def test_a_schema_2_record_written_by_the_fetcher_still_reads_through_policy_source(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    """Rewrite a real run as the pre-FC1a fetcher wrote it; the source proof still reads."""
    out = tmp_path / "pages"
    body = _page("QUJD")
    _fetch(httpx_mock, out, {CH1: body}, {"content": _page("WFla")})
    run_file = _run_file(out)
    doc: Doc = json.loads(run_file.read_bytes())
    doc["schema_version"] = 2
    for item in (*doc["records"], doc["terms"], doc["robots"]):
        del item["normalized_sha256"], item["normalization_rule"]
    run_file.write_text(json.dumps(doc), encoding="utf-8")
    source_run = read_source_run(run_file)
    assert source_run.fetched == {(CH1, hashlib.sha256(body).hexdigest())}
    assert source_run.source.terms is not None
    assert source_run.source.terms.normalized_sha256 is None


@pytest.mark.parametrize("where", ["record", "terms"])
def test_the_proof_refuses_a_schema_2_record_with_a_normalized_digest(where: str) -> None:
    doc = _copy(SCHEMA_2)
    target = doc["records"][0] if where == "record" else doc["terms"]
    target |= {"normalized_sha256": "c" * 64, "normalization_rule": NORMALIZATION_RULE}
    run = FetchRun.model_validate(doc)
    with pytest.raises(RegjeringenFetchError, match="source_proof_inconsistent: normalized_digest"):
        check_source_proof(run)


def _schema_3() -> Doc:
    doc = _copy(SCHEMA_2)
    doc["schema_version"] = 3
    for item in (doc["records"][0], doc["terms"]):
        item.update(normalized_sha256="c" * 64, normalization_rule=NORMALIZATION_RULE)
    return doc


def test_a_schema_3_record_validates_and_keeps_raw_provenance() -> None:
    run = FetchRun.model_validate(_schema_3())
    check_source_proof(run)
    record = run.records[0]
    assert (record.url, record.sha256, record.size, record.status) == (CH1, "a" * 64, 10, 200)
    assert record.retrieved_at.isoformat() == "2026-11-09T12:00:10+00:00"


@pytest.mark.parametrize("where", ["record", "terms"])
def test_the_proof_refuses_a_schema_3_page_without_a_normalized_digest(where: str) -> None:
    doc = _schema_3()
    target = doc["records"][0] if where == "record" else doc["terms"]
    del target["normalized_sha256"], target["normalization_rule"]
    run = FetchRun.model_validate(doc)
    with pytest.raises(RegjeringenFetchError, match="source_proof_inconsistent: normalized_digest"):
        check_source_proof(run)


@pytest.mark.parametrize(
    "fields",
    [
        {"normalized_sha256": "c" * 64, "normalization_rule": None},
        {"normalized_sha256": None, "normalization_rule": NORMALIZATION_RULE},
        {"normalized_sha256": "C" * 64},
        {"normalized_sha256": "c" * 63},
        {"normalization_rule": "regjeringen-csp-nonce/2"},
    ],
)
def test_a_malformed_normalized_pair_is_refused(fields: Doc) -> None:
    doc = _schema_3()
    doc["records"][0] |= fields
    with pytest.raises(ValidationError):
        FetchRun.model_validate(doc)


def test_the_proof_refuses_a_normalized_digest_on_an_unstored_page() -> None:
    doc = _schema_3()
    doc["records"][0] |= {"outcome": "http_status", "status": 500, "sha256": None, "size": None}
    run = FetchRun.model_validate(doc)
    with pytest.raises(RegjeringenFetchError, match="source_proof_inconsistent: normalized_digest"):
        check_source_proof(run)


def test_an_unknown_schema_version_is_refused() -> None:
    doc = _schema_3()
    doc["schema_version"] = 4
    with pytest.raises(ValidationError):
        FetchRun.model_validate(doc)


# Behaviour unchanged: verification and the policy comparison still use raw digests.


def test_stored_evidence_is_still_verified_by_its_raw_digest(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    out = tmp_path / "pages"
    run = _fetch(httpx_mock, out, {CH1: _page("QUJD")}, {"content": _page("QUJD")})
    assert run.terms is not None
    stored = out / f"terms-{run.terms.sha256}.html"
    stored.write_bytes(_page("WFla"))
    assert normalized_sha256(stored.read_bytes()) == run.terms.normalized_sha256
    with pytest.raises(RegjeringenFetchError, match="evidence_mismatch"):
        verified_source_bytes(out, run.terms)


def test_a_nonce_only_terms_change_still_denies_automatic_clearance(
    httpx_mock: HTTPXMock, tmp_path: Path
) -> None:
    """FC1a changes no comparison: the policy still compares raw digests (FC1b, after D4)."""
    signed_run = _published(httpx_mock, tmp_path / "signed", _page("QUJD"))
    desk = _desk_bound_to(tmp_path, signed_run)
    sha = desk.signed()
    later = _published(httpx_mock, tmp_path / "later", _page("WFla"))
    signed = FetchRun.model_validate_json(signed_run.read_bytes()).terms
    current = FetchRun.model_validate_json(later.read_bytes()).terms
    assert signed is not None and current is not None
    assert signed.normalized_sha256 == current.normalized_sha256
    result = _show(desk, sha, later)
    assert said("source faults terms_changed", result.output)
    assert said("automatic clearance denied", result.output)


def test_the_rule_written_is_a_rule_the_reader_accepts() -> None:
    assert get_args(NormalizationRule) == (NORMALIZATION_RULE,)


def test_an_earlier_proof_fault_keeps_precedence_over_the_normalized_check() -> None:
    """Placement is checked last: a record malformed before schema 3 is refused as before."""
    doc = _schema_3()
    doc["terms"] |= {"kind": "robots_txt"}
    with pytest.raises(RegjeringenFetchError, match="source_proof_inconsistent: terms_identity"):
        check_source_proof(FetchRun.model_validate(doc))


def test_the_proof_refuses_a_normalized_digest_on_unpublished_terms() -> None:
    doc = _schema_3()
    doc["terms"] |= {"state": "absent", "status": 404, "sha256": None, "size": None}
    doc["terms"]["hops"][0]["status"] = 404
    with pytest.raises(RegjeringenFetchError, match="normalized_digest"):
        check_source_proof(FetchRun.model_validate(doc))
