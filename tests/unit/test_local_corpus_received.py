"""A received-only local document as the read side serves it (ADR-0016 A5, A6; slice I2).

The corpus is written only through the supported commands: the innsyn
workspace with the innsyn CLI, the document with ``lovspor promote received
approve`` and ``local`` (the fixtures and helpers of
``test_promotion_received_commands``). The front-matter refusals edit a
written file by hand, because no supported command can write a mixed or
incomplete channel — which is the point of refusing one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lovspor.errors import LocalCorpusError
from lovspor.local_corpus import (
    RECEIVED_NOTICE,
    LocalDataset,
    ObservationLabel,
    ReceivedObservation,
    _given,
)
from tests.unit.local_dataset_fixtures import add_local_dataset, build_central
from tests.unit.test_local_corpus import ADDRESS, OBSERVATION
from tests.unit.test_promotion_received_commands import (  # noqa: F401 — fixture reuse
    FJALER,
    LOCAL,
    RECEIVED_AT,
    _promoted,
    corpus,
    root,
    workspace,
)

NOTICE = (
    "Mottatt fra myndigheten etter innsynskrav (offentleglova); ikke kontrollert mot Norsk "
    "Lovtidend eller Lovdata. Lovspor hevder ikke at forskriften gjelder. / Received from the "
    "authority under an offentleglova request; not verified against Norsk Lovtidend or Lovdata. "
    "Lovspor does not assert that this regulation is in force."
)
AUTHORITY = {"id": "4646", "type": "kommune", "name": "Fjaler"}


@pytest.fixture
def received(workspace: Path, corpus: Path, tmp_path: Path) -> Path:  # noqa: F811
    _, result = _promoted(workspace, corpus, tmp_path)
    assert result.exit_code == 0, result.output
    return corpus


def _address(checkout: Path) -> str:
    manifest = json.loads((checkout / LOCAL / "manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    return f"{record['authority_id']}/{record['slug']}"


def _markdown_file(checkout: Path) -> Path:
    [path] = (checkout / LOCAL / "4646").glob("*.md")
    return path


def _edit_front_matter(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


class TestReceivedLabel:
    def test_it_is_served_with_a_received_observation(self, received: Path) -> None:
        served = LocalDataset(received).document(_address(received))
        assert isinstance(served.observation, ReceivedObservation)
        assert served.observation.label() == {
            "basis": "received",
            "asserted": False,
            "authority": AUTHORITY,
            "received_at": RECEIVED_AT,
            "innsyn_request": FJALER,
            "notice": NOTICE,
        }

    def test_the_notice_is_the_adr_text_verbatim(self) -> None:
        assert RECEIVED_NOTICE == NOTICE

    def test_the_label_names_no_url_and_no_observation_interval(self, received: Path) -> None:
        label = LocalDataset(received).document(_address(received)).observation.label()
        for observed in ("source_url", "observed_at_first", "observed_at_last"):
            assert observed not in label

    def test_the_get_law_block_carries_it(self, received: Path) -> None:
        served = LocalDataset(received).document(_address(received))
        block = json.loads(ObservationLabel(observation=served.observation).model_dump_json())
        assert block["observation"]["basis"] == "received"

    def test_a_search_hit_carries_it(self, received: Path) -> None:
        local = LocalDataset(received)
        [(doc_id, record)] = local.matches("4646/")
        assert local.hit(doc_id, record)["observation"]["received_at"] == RECEIVED_AT


class TestReceivedIntegrity:
    def test_observations_saying_another_received_at_are_refused(self, received: Path) -> None:
        [path] = (received / LOCAL / "4646" / "observations").glob("*.json")
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(RECEIVED_AT, "2026-10-08T00:00:00Z"), encoding="utf-8")
        with pytest.raises(LocalCorpusError, match="received_at"):
            LocalDataset(received).document(_address(received))


class TestFrontMatterChannel:
    """Hand-edited front matter: no supported command writes a mixed channel."""

    def test_a_received_basis_with_a_source_url_is_refused(self, received: Path) -> None:
        path = _markdown_file(received)
        _edit_front_matter(path, "innsyn_request:", 'source_url: "https://x.no"\ninnsyn_request:')
        with pytest.raises(LocalCorpusError, match="basis 'received'"):
            LocalDataset(received).document(_address(received))

    def test_a_received_basis_without_received_at_is_refused(self, received: Path) -> None:
        path = _markdown_file(received)
        _edit_front_matter(path, f'received_at: "{RECEIVED_AT}"\n', "")
        with pytest.raises(LocalCorpusError, match="basis 'received'"):
            LocalDataset(received).document(_address(received))

    def test_a_null_received_at_is_refused(self, received: Path) -> None:
        path = _markdown_file(received)
        _edit_front_matter(path, f'received_at: "{RECEIVED_AT}"', "received_at: null")
        with pytest.raises(LocalCorpusError, match="received_at"):
            LocalDataset(received).document(_address(received))

    def test_an_observed_basis_with_received_keys_is_refused(self, tmp_path: Path) -> None:
        build_central(tmp_path)
        add_local_dataset(tmp_path)
        [path] = (tmp_path / LOCAL / "9999").glob("*.md")
        _edit_front_matter(path, "source_sha256:", f'received_at: "{RECEIVED_AT}"\nsource_sha256:')
        with pytest.raises(LocalCorpusError, match="basis 'observed'"):
            LocalDataset(tmp_path).document(ADDRESS)

    def test_an_observed_basis_without_a_source_url_is_refused(self, tmp_path: Path) -> None:
        build_central(tmp_path)
        add_local_dataset(tmp_path)
        [path] = (tmp_path / LOCAL / "9999").glob("*.md")
        text = path.read_text(encoding="utf-8")
        [line] = [x for x in text.splitlines() if x.startswith("source_url: ")]
        _edit_front_matter(path, f"{line}\n", "")
        with pytest.raises(LocalCorpusError, match="basis 'observed'"):
            LocalDataset(tmp_path).document(ADDRESS)

    def test_a_received_front_matter_over_observed_observations_is_refused(
        self, tmp_path: Path
    ) -> None:
        build_central(tmp_path)
        add_local_dataset(tmp_path)
        [path] = (tmp_path / LOCAL / "9999").glob("*.md")
        text = path.read_text(encoding="utf-8")
        [first] = [x for x in text.splitlines() if x.startswith("observed_at_first: ")]
        [url] = [x for x in text.splitlines() if x.startswith("source_url: ")]
        received_keys = f'received_at: "{RECEIVED_AT}"\ninnsyn_request: "{FJALER}"'
        _edit_front_matter(path, f"{first}\n{url}", f"{received_keys}\ninnsyn_saksnummer: null")
        _edit_front_matter(path, 'basis: "observed"', 'basis: "received"')
        [observations] = (tmp_path / LOCAL / "9999" / "observations").glob("*.json")
        manifest = json.loads((tmp_path / LOCAL / "manifest.json").read_text())
        [doc_id] = manifest["documents"]
        with pytest.raises(LocalCorpusError) as refused:
            LocalDataset(tmp_path).document(ADDRESS)
        assert str(refused.value) == (
            f"{observations}: version 1 of {doc_id} was observed; its front matter says received"
        )


class TestObservedUnchanged:
    def test_an_observed_document_keeps_its_label(self, tmp_path: Path) -> None:
        build_central(tmp_path)
        add_local_dataset(tmp_path)
        served = LocalDataset(tmp_path).document(ADDRESS)
        assert served.observation.label() == OBSERVATION
        assert list(served.observation.label()) == list(OBSERVATION)


def test_missing_validated_front_matter_key_reports_internal_invariant() -> None:
    """The defensive helper diagnoses a lost key even for a direct caller."""
    with pytest.raises(LocalCorpusError) as refused:
        _given(None)
    assert str(refused.value) == "front matter lost a key its basis requires"
