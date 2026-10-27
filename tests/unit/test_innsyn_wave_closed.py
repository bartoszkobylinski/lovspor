"""A wave and the authorities that already answered the general request (#696).

The operator's question: after ``full`` or ``refused``, with no new ids, does
``innsyn letters`` write that authority a letter? And with new ids, does the
letter ask for those alone, without putting the general point again?
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import requests
from lovspor.innsyn.letters import LetterRequest
from lovspor.innsyn.population import Authority
from lovspor.innsyn.sender import Sender
from lovspor.innsyn.waves import read_manifest
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import innsyn, letters_workspace, lf_entry, ok, sample_lf_ledger

OSLO, FJALER = "0301", "4646"


@pytest.fixture
def lf_ledger(tmp_path: Path) -> Path:
    return sample_lf_ledger(tmp_path / "lf-ledger.jsonl")


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return letters_workspace(tmp_path / "innsyn", httpx_mock)


NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)
NEW_ID = "2025-06-12-900"


def _manifest(out: Path) -> dict[str, object]:
    manifest: dict[str, object] = json.loads((out / "manifest.json").read_text())
    return manifest


def _wave(workspace: Path, out: Path, ledger: Path) -> tuple[str, dict[str, object]]:
    result = innsyn(workspace, "letters", "--out", str(out), "--lf-ledger", str(ledger))
    assert result.exit_code == 0, result.output
    return result.stdout, _manifest(out)


def _entries(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = manifest["letters"]
    assert isinstance(rows, list)
    return {row["authority_id"]: row for row in rows}


def _close(workspace: Path, tmp_path: Path, ledger: Path, authority: str, outcome: str) -> None:
    """Send the first wave's letter to ``authority`` and record ``outcome`` on it."""
    _wave(workspace, tmp_path / "first", ledger)
    manifest = str(tmp_path / "first" / "manifest.json")
    sent = ("record-sent", "--manifest", manifest, "--id", authority)
    ok(workspace, *sent, "--sent-at", "2026-10-13T08:00:00Z")
    closing = ("--outcome", outcome, "--at", "2026-10-15T08:00:00Z")
    ok(workspace, "record-outcome", "--request", f"{authority}-20261013T080000Z", *closing)


def _link(ledger: Path, authority: str, lf_id: str = NEW_ID) -> None:
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(lf_entry(authority, lf_id) + "\n")


class TestAnsweredAuthorities:
    @pytest.mark.parametrize("outcome", ["full", "refused"])
    def test_with_nothing_new_it_gets_no_letter(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, outcome: str
    ) -> None:
        _close(workspace, tmp_path, lf_ledger, OSLO, outcome)
        said, manifest = _wave(workspace, tmp_path / "next", lf_ledger)
        assert OSLO not in _entries(manifest)
        assert manifest["skipped_closed"] == [OSLO]
        assert manifest["skipped_open"] == []
        assert not (tmp_path / "next" / f"{OSLO}.txt").exists()
        assert "1 answered with nothing new" in said

    @pytest.mark.parametrize("outcome", ["full", "refused"])
    def test_with_new_ids_it_gets_a_letter_for_those_ids_alone(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, outcome: str
    ) -> None:
        _close(workspace, tmp_path, lf_ledger, OSLO, outcome)
        _link(lf_ledger, OSLO)
        _, manifest = _wave(workspace, tmp_path / "next", lf_ledger)
        entry = _entries(manifest)[OSLO]
        assert entry["lf_ids"] == [NEW_ID]
        assert entry["general_clause"] is False
        assert manifest["skipped_closed"] == []
        text = (tmp_path / "next" / f"{OSLO}.txt").read_text(encoding="utf-8")
        assert "i tillegg til mitt tidligere innsynskrav til kommunen" in text
        assert "\n1. Forskrifter med kjent nummer\n" in text
        assert f"- FOR-{NEW_ID} (dato 12.06.2025, nr. 900)" in text
        assert "2019-03-20-400" not in text
        assert "Andre lokale forskrifter" not in text
        assert "saker av en bestemt art" not in text
        assert "liste eller oversikt" not in text
        assert "to nummererte punkter" not in text

    def test_the_nynorsk_id_only_letter(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _close(workspace, tmp_path, lf_ledger, FJALER, "full")
        _link(lf_ledger, FJALER)
        _wave(workspace, tmp_path / "next", lf_ledger)
        text = (tmp_path / "next" / f"{FJALER}.txt").read_text(encoding="utf-8")
        assert "Kravet kjem i tillegg til mitt tidlegare innsynskrav til kommunen" in text
        assert "to nummererte punkt" not in text
        assert "Andre lokale forskrifter" not in text
        assert "Forskriftene nedanfor er oppgitt" in text

    def test_an_id_only_letter_is_recorded_without_the_general_clause(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _close(workspace, tmp_path, lf_ledger, OSLO, "full")
        _link(lf_ledger, OSLO)
        _wave(workspace, tmp_path / "next", lf_ledger)
        manifest = str(tmp_path / "next" / "manifest.json")
        sent = ("record-sent", "--manifest", manifest, "--id", OSLO)
        ok(workspace, *sent, "--sent-at", "2026-10-20T08:00:00Z")
        state = requests(InnsynWorkspace(workspace, []))[f"{OSLO}-20261020T080000Z"]
        assert (state.sent.lf_ids, state.sent.general_clause) == ((NEW_ID,), False)
        listing = innsyn(workspace, "requests").stdout.splitlines()
        rows = {r.split("\t")[0]: r.split("\t") for r in listing}
        assert rows[f"{OSLO}-20261020T080000Z"][4:6] == ["1", ""]
        # The new request is open, so the next wave skips the authority as open;
        # its id is now asked for and would not be asked again.
        _, third = _wave(workspace, tmp_path / "third", lf_ledger)
        assert third["skipped_open"] == [OSLO]
        assert third["skipped_closed"] == []

    def test_an_undelivered_letter_does_not_count_as_answered(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "first", lf_ledger)
        manifest = str(tmp_path / "first" / "manifest.json")
        sent = ("record-sent", "--manifest", manifest, "--id", OSLO)
        ok(workspace, *sent, "--sent-at", "2026-10-13T08:00:00Z")
        evidence = tmp_path / "bounce.eml"
        evidence.write_text("Subject: Undeliverable\n\nStatus: 5.1.1\n", encoding="utf-8")
        bounce = ("--request", f"{OSLO}-20261013T080000Z", "--at", "2026-10-13T09:00:00Z")
        proof = ("--status", "5.1.1", "--evidence", str(evidence))
        result = innsyn(workspace, "record-bounce", *bounce, *proof)
        assert result.exit_code == 0, result.output
        _, manifest_next = _wave(workspace, tmp_path / "next", lf_ledger)
        assert manifest_next["skipped_closed"] == []
        assert OSLO in manifest_next["unresolved"]


class TestContracts:
    def test_a_manifest_written_before_696_still_reads(self, tmp_path: Path) -> None:
        data = {
            "schema_version": 1,
            "generated_at": "2026-10-19T10:00:00Z",
            "population_valid_at": "2026-10-12",
            "wave_size": 50,
            "letters": [],
            "unresolved": [],
            "skipped_open": [],
            "remaining": 0,
        }
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert read_manifest(path).skipped_closed == ()

    def test_a_letter_must_ask_for_something(self) -> None:
        authority = Authority(authority_id="0301", kind="kommune", name="Oslo", klass_code="0301")
        sender = Sender(name="Test Requester", email="kontakt@example.no", recorded_at=NOW)
        with pytest.raises(ValidationError, match="asks for known ids, the general point"):
            LetterRequest(
                authority=authority,
                recipient="postmottak@oslobystyre.no",
                maalform="nb",
                lf_ids=(),
                general_clause=False,
                sender=sender,
            )
