"""Received provenance in ``observations/<slug>.json`` (ADR-0016 Amendment 1, A3-A5).

A version received under offentleglova carries a ``received`` list and none of
the observation axis's keys; a file holding one is schema version 2. A file
of observed versions keeps schema version 1 and every byte it had. Every
reader of the observation axis refuses, or passes through, a received
version explicitly. States that no supported command writes yet (a received
version is promoted by a later slice) are made by editing the file a
supported command wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.errors import LocalCorpusError, PromotionRefusedError
from lovspor.local_corpus import LocalDataset
from lovspor.observation_history import observation_history
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from lovspor.promotion.archive import authority_fetches
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import (
    ArtifactKey,
    DecisionLog,
    ReceivedKey,
    WithdrawalDocument,
    WithdrawalRecord,
)
from lovspor.promotion.intervals import reproduction, with_intervals
from lovspor.promotion.models import RemovedReason
from lovspor.promotion.versions import read_primary
from lovspor.promotion.withdraw import locate_document, refuse_withdrawn, withdrawal_record
from lovspor.promotion.writer import (
    ObservationsFile,
    ReceivedVersion,
    VersionObservations,
    observations_text,
)
from tests.unit.backfilled_corpus_fixtures import backfilled_corpus, local_address
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    invoke,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page
from tests.unit.test_promotion_migrate import _migrate_one, _promoted_once

LOCAL = "lokale-forskrifter"
CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
OBSERVED_KEYS = {
    "content_hash",
    "corroborating_urls",
    "observation_count",
    "observed_at_first",
    "observed_at_last",
    "primary_url",
    "promotion",
    "source_sha256s",
    "version",
}
RECEIVED = {
    "received_at": "2026-10-07T12:45:06Z",
    "innsyn_request": "4646-20261005T074425Z",
    "source_sha256": "a" * 64,
    "received_form": "lovdata_print",
}
GOLDEN = """\
{
  "authority_id": "0301",
  "doc_id": "lk-0301-aa8ae774921d",
  "schema_version": 1,
  "slug": "forskrift-om-renovasjon-og-slam-eksempel-kommune",
  "source_status": {
    "http_status": 200,
    "observed_at": "2026-08-19T15:17:23Z",
    "outcome": "retrieved"
  },
  "versions": [
    {
      "content_hash": "dfb7398bbe1104824f761e18661422687b23f02bd368a23efa246e89a3c08f53",
      "corroborating_urls": [],
      "observation_count": 1,
      "observed_at_first": "2026-08-19T15:17:23Z",
      "observed_at_last": "2026-08-19T15:17:23Z",
      "primary_url": "https://eksempel.kommune.invalid/forskrifter/renovasjon",
      "promotion": {
        "classifier": null,
        "decided_at": "2026-10-17T06:18:00.422017Z",
        "decision": "approve",
        "extractor_version": 7,
        "identity": {
          "candidates": [],
          "doc_id": "lk-0301-aa8ae774921d",
          "ref_id": null,
          "scheme": "lk"
        },
        "observations": [
          {
            "observed_at": "2026-08-19T15:17:23Z",
            "sha256": "f0505364b8903f2bf5e944c1d5df25707fa9aefa9518841817aa0cc88c1c3abb",
            "url": "https://eksempel.kommune.invalid/forskrifter/renovasjon"
          }
        ],
        "observations_through": "2026-10-17T06:18:00.422017Z",
        "reason": "Vedtatt forskrift; kilden lest på nett i sin helhet.",
        "renderer_version": 1,
        "reviewed_by_role": "project owner",
        "reviewed_in_sample": true,
        "source_form": "html"
      },
      "source_sha256s": [
        "f0505364b8903f2bf5e944c1d5df25707fa9aefa9518841817aa0cc88c1c3abb"
      ],
      "version": 1
    }
  ]
}
"""


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


def _received(entry: dict[str, Any]) -> dict[str, Any]:
    """The received twin of an observed version entry: same text, same audit."""
    kept = {key: entry[key] for key in ("version", "content_hash", "promotion")}
    return {**kept, "received": [RECEIVED]}


def _received_only(document: dict[str, Any]) -> dict[str, Any]:
    versions = [_received(entry) for entry in document["versions"]]
    without_status = {k: v for k, v in document.items() if k != "source_status"}
    return {**without_status, "schema_version": 2, "versions": versions}


def _golden() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(GOLDEN)
    return document


def _observations_path(corpus: Path) -> Path:
    [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
    return path


def _rewrite(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _promoted(root: Path, corpus: Path, tmp_path: Path) -> Path:
    sha256 = store(root, html_page())
    assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
    result = promote("local", sha256, corpus)
    assert result.exit_code == 0, result.output
    return _observations_path(corpus)


def _receive_on_disk(path: Path) -> None:
    _rewrite(path, _received_only(json.loads(path.read_text(encoding="utf-8"))))


class TestObservedFileUnchanged:
    def test_an_observed_file_rereads_and_redumps_byte_identically(self) -> None:
        document = ObservationsFile.model_validate_json(GOLDEN)

        assert observations_text(document) == GOLDEN
        assert document.schema_version == 1
        assert all(isinstance(v, VersionObservations) for v in document.versions)

    def test_promote_local_still_writes_schema_1_with_only_the_observed_keys(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        text = _promoted(root, corpus, tmp_path).read_text(encoding="utf-8")
        written = json.loads(text)

        assert written["schema_version"] == 1
        assert "source_status" in written
        assert [set(v) for v in written["versions"]] == [OBSERVED_KEYS]
        assert observations_text(ObservationsFile.model_validate_json(text)) == text


class TestReceivedFile:
    def test_a_received_version_dumps_without_observed_keys_or_source_status(self) -> None:
        document = ObservationsFile.model_validate(_received_only(_golden()))

        written = json.loads(observations_text(document))

        assert isinstance(document.versions[0], ReceivedVersion)
        assert written["schema_version"] == 2
        assert "source_status" not in written
        assert set(written["versions"][0]) == {"version", "content_hash", "received", "promotion"}
        assert written["versions"][0]["received"] == [RECEIVED]

    def test_a_received_version_rereads_byte_identically(self) -> None:
        text = observations_text(ObservationsFile.model_validate(_received_only(_golden())))

        assert observations_text(ObservationsFile.model_validate_json(text)) == text

    def test_a_received_version_and_an_observed_one_parse_as_their_own_kind(self) -> None:
        golden = _golden()
        document = {
            **_received_only(golden),
            "source_status": golden["source_status"],
            "versions": [_received(golden["versions"][0]), {**golden["versions"][0], "version": 2}],
        }

        parsed = ObservationsFile.model_validate(document)

        assert [type(v) for v in parsed.versions] == [ReceivedVersion, VersionObservations]

    def test_a_received_version_with_no_provenance_is_refused(self) -> None:
        document = _received_only(_golden())
        document["versions"][0]["received"] = []

        with pytest.raises(ValidationError, match="received"):
            ObservationsFile.model_validate(document)

    def test_a_received_version_with_an_observed_key_is_refused(self) -> None:
        document = _received_only(_golden())
        document["versions"][0]["primary_url"] = PAGE_URL

        with pytest.raises(ValidationError):
            ObservationsFile.model_validate(document)

    @pytest.mark.parametrize(
        ("field", "value"),
        [("innsyn_request", "4646-2026-10-05"), ("source_sha256", "A" * 64)],
    )
    def test_malformed_received_provenance_is_refused(self, field: str, value: str) -> None:
        document = _received_only(_golden())
        document["versions"][0]["received"] = [{**RECEIVED, field: value}]

        with pytest.raises(ValidationError, match=field):
            ObservationsFile.model_validate(document)

    def test_schema_2_without_a_received_version_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="schema_version 2"):
            ObservationsFile.model_validate({**_golden(), "schema_version": 2})

    def test_schema_1_with_a_received_version_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="schema_version 2"):
            ObservationsFile.model_validate({**_received_only(_golden()), "schema_version": 1})

    def test_an_observed_version_without_source_status_is_refused(self) -> None:
        document = {k: v for k, v in _golden().items() if k != "source_status"}

        with pytest.raises(ValidationError, match="source_status"):
            ObservationsFile.model_validate(document)

    def test_a_mixed_file_without_source_status_is_refused(self) -> None:
        golden = _golden()
        document = {
            **_received_only(golden),
            "versions": [_received(golden["versions"][0]), {**golden["versions"][0], "version": 2}],
        }

        with pytest.raises(ValidationError, match="source_status"):
            ObservationsFile.model_validate(document)


class TestIntervals:
    def test_a_received_version_passes_through_a_refresh_unchanged(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path)
        _receive_on_disk(path)
        existing = ObservationsFile.model_validate_json(path.read_bytes())
        log = ObservationLog(ObservatoryRoot(root, []))
        history = read_primary(log, authority_fetches(log, AUTHORITY), PAGE_URL, None)

        refreshed = with_intervals(existing, history)

        assert refreshed.versions == existing.versions

    def test_reproduction_names_a_received_version_as_having_no_observation_axis(self) -> None:
        entry = ObservationsFile.model_validate(_received_only(_golden())).versions[0]

        assert reproduction(entry, None) == "a received-only version has no observation axis"

    def test_observe_skips_a_received_only_document(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        path = _promoted(root, corpus, tmp_path)
        _receive_on_disk(path)
        before = path.read_bytes()

        result = invoke("promote", "observe", "--corpus", str(corpus))

        assert result.exit_code == 0, result.output
        assert result.stdout.splitlines()[0] == (
            f"{json.loads(before)['doc_id']}: "
            "skipped: a received-only document has no observation axis"
        )
        assert path.read_bytes() == before


class TestWriterEarlierVersions:
    def test_an_observed_version_after_a_received_one_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _receive_on_disk(_promoted(root, corpus, tmp_path))
        sha256 = store(root, html_page(CHANGED), observed_at=FIRST_SEEN + timedelta(days=5))
        assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
        before = tree(corpus)

        result = promote("local", sha256, corpus)

        assert result.exit_code != 0
        assert "switching the primary channel is a recorded decision (ADR-0016 A4)" in (
            result.stderr
        )
        assert tree(corpus) == before


class TestMigrate:
    def test_a_received_version_is_refused_not_migrated(
        self, root: Path, corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _promoted_once(root, corpus, tmp_path, monkeypatch)
        _receive_on_disk(_observations_path(corpus))
        before = tree(corpus)

        result = _migrate_one(corpus)

        assert result.exit_code == 1, result.output
        assert "received version" in result.output
        assert tree(corpus) == before


class TestWithdraw:
    def test_withdrawing_a_received_version_names_its_received_key(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        _receive_on_disk(_promoted(root, corpus, tmp_path))
        received = ReceivedKey(
            authority_id=AUTHORITY,
            innsyn_request=RECEIVED["innsyn_request"],
            sha256=RECEIVED["source_sha256"],
        )

        withdrawal = _withdraw(CorpusCheckout(corpus, []))

        assert withdrawal.artifacts == (received,)
        decisions = DecisionLog(ObservatoryRoot(root, []))
        decisions.append(withdrawal)
        with pytest.raises(PromotionRefusedError, match="never promoted again"):
            refuse_withdrawn(decisions, received, None)

    def test_a_mixed_document_names_each_version_by_its_own_channel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout = backfilled_corpus(tmp_path, monkeypatch)
        path = _observations_path(checkout)
        document = json.loads(path.read_text(encoding="utf-8"))
        observed = document["versions"][0]
        document["versions"][-1] = _received(document["versions"][-1])
        _rewrite(path, {**document, "schema_version": 2})

        withdrawal = _withdraw(CorpusCheckout(checkout, []))

        assert withdrawal.artifacts[0] == ArtifactKey(
            authority_id=withdrawal.authority_id,
            sha256=observed["source_sha256s"][0],
            source_url=observed["primary_url"],
        )
        assert withdrawal.artifacts[-1] == ReceivedKey(
            authority_id=withdrawal.authority_id,
            innsyn_request=RECEIVED["innsyn_request"],
            sha256=RECEIVED["source_sha256"],
        )


def _withdraw(checkout: CorpusCheckout) -> WithdrawalRecord:
    manifest = checkout.local_manifest()
    [record] = manifest.documents.values()
    located = locate_document(manifest, record.authority_id, record.slug)
    document = WithdrawalDocument(
        decision="withdraw",
        removed_reason=RemovedReason.WITHDRAWN_MISCLASSIFIED,
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        reason="Klassifisert feil.",
    )
    return withdrawal_record(checkout, located, document, datetime.now(UTC))


class TestLocalReaders:
    def test_a_received_current_version_is_named_received_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout = backfilled_corpus(tmp_path, monkeypatch)
        path = _observations_path(checkout)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["versions"][-1] = _received(document["versions"][-1])
        _rewrite(path, {**document, "schema_version": 2})

        with pytest.raises(LocalCorpusError, match="received-only"):
            LocalDataset(checkout).document(local_address(checkout))

    def test_observation_history_names_a_received_version_received_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout = backfilled_corpus(tmp_path, monkeypatch)
        path = _observations_path(checkout)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["versions"][0] = _received(document["versions"][0])
        _rewrite(path, {**document, "schema_version": 2})
        address = local_address(checkout)

        with pytest.raises(LocalCorpusError, match=f"{address}.*received-only"):
            observation_history(LocalDataset(checkout), address, None, False)
