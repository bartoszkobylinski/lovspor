"""A wave and the authorities with an open request (#695).

The operator's question: while an authority delivers in parts, holds, or points
to Lovdata, do the ids its pages newly link reach it? They do, as a separate
id-only request; with nothing new the authority is still skipped as open.
"""

import json
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import requests
from lovspor.innsyn.workspace import InnsynWorkspace
from tests.unit.innsyn_support import innsyn, letters_workspace, lf_entry, ok, sample_lf_ledger

OSLO, FJALER = "0301", "4646"
NEW_ID = "2025-06-12-900"
OPEN_OUTCOMES = ["partial", "referred_to_lovdata", "interim_reply_no_date", "no_answer"]


@pytest.fixture
def lf_ledger(tmp_path: Path) -> Path:
    return sample_lf_ledger(tmp_path / "lf-ledger.jsonl")


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return letters_workspace(tmp_path / "innsyn", httpx_mock)


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


def _send(workspace: Path, tmp_path: Path, ledger: Path, authority: str) -> str:
    """Send the first wave's letter to ``authority``; the request id."""
    _wave(workspace, tmp_path / "first", ledger)
    manifest = str(tmp_path / "first" / "manifest.json")
    sent = ("record-sent", "--manifest", manifest, "--id", authority)
    ok(workspace, *sent, "--sent-at", "2026-10-13T08:00:00Z")
    return f"{authority}-20261013T080000Z"


def _link(ledger: Path, authority: str, lf_id: str = NEW_ID) -> None:
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(lf_entry(authority, lf_id) + "\n")


class TestOpenAuthorities:
    def test_with_nothing_new_it_is_still_skipped_as_open(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _send(workspace, tmp_path, lf_ledger, OSLO)
        said, manifest = _wave(workspace, tmp_path / "next", lf_ledger)
        assert OSLO not in _entries(manifest)
        assert manifest["skipped_open"] == [OSLO]
        assert "1 with an open request" in said

    @pytest.mark.parametrize("outcome", [None, *OPEN_OUTCOMES])
    def test_with_new_ids_it_gets_a_separate_id_only_letter(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, outcome: str | None
    ) -> None:
        request = _send(workspace, tmp_path, lf_ledger, OSLO)
        if outcome is not None:
            at = ("--at", "2026-10-15T08:00:00Z")
            ok(workspace, "record-outcome", "--request", request, "--outcome", outcome, *at)
        _link(lf_ledger, OSLO)
        _, manifest = _wave(workspace, tmp_path / "next", lf_ledger)
        entry = _entries(manifest)[OSLO]
        assert entry["lf_ids"] == [NEW_ID]
        assert entry["general_clause"] is False
        assert manifest["skipped_open"] == []
        assert manifest["skipped_closed"] == []
        text = (tmp_path / "next" / f"{OSLO}.txt").read_text(encoding="utf-8")
        assert "i tillegg til mitt tidligere innsynskrav til kommunen" in text
        assert f"- FOR-{NEW_ID} (dato 12.06.2025, nr. 900)" in text
        assert "2019-03-20-400" not in text
        assert "Andre lokale forskrifter" not in text

    def test_the_nynorsk_id_only_letter(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _send(workspace, tmp_path, lf_ledger, FJALER)
        _link(lf_ledger, FJALER)
        _wave(workspace, tmp_path / "next", lf_ledger)
        text = (tmp_path / "next" / f"{FJALER}.txt").read_text(encoding="utf-8")
        assert "Kravet kjem i tillegg til mitt tidlegare innsynskrav til kommunen" in text
        assert "Andre lokale forskrifter" not in text

    def test_the_id_only_request_is_its_own_and_the_open_one_is_untouched(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        first = _send(workspace, tmp_path, lf_ledger, OSLO)
        _link(lf_ledger, OSLO)
        _wave(workspace, tmp_path / "next", lf_ledger)
        manifest = str(tmp_path / "next" / "manifest.json")
        sent = ("record-sent", "--manifest", manifest, "--id", OSLO)
        ok(workspace, *sent, "--sent-at", "2026-10-20T08:00:00Z")
        held = requests(InnsynWorkspace(workspace, []))
        second = held[f"{OSLO}-20261020T080000Z"]
        assert (second.sent.lf_ids, second.sent.general_clause) == ((NEW_ID,), False)
        assert held[first].sent.lf_ids == ("2019-03-20-400",)
        assert held[first].sent.general_clause is True
        # Both open, nothing new: skipped once, and the new id is not asked again.
        _, third = _wave(workspace, tmp_path / "third", lf_ledger)
        assert third["skipped_open"] == [OSLO]
        assert OSLO not in _entries(third)

    def test_ids_the_open_request_asked_for_are_not_new(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _send(workspace, tmp_path, lf_ledger, OSLO)
        _link(lf_ledger, OSLO, "2019-03-20-400")
        _, manifest = _wave(workspace, tmp_path / "next", lf_ledger)
        assert manifest["skipped_open"] == [OSLO]

    def test_a_held_new_id_does_not_write_to_an_open_authority(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _send(workspace, tmp_path, lf_ledger, OSLO)
        _link(lf_ledger, OSLO)
        held = tmp_path / "held.txt"
        held.write_text(f"{NEW_ID}\n", encoding="utf-8")
        out = tmp_path / "next"
        args = ("--out", str(out), "--lf-ledger", str(lf_ledger), "--held", str(held))
        result = innsyn(workspace, "letters", *args)
        assert result.exit_code == 0, result.output
        assert _manifest(out)["skipped_open"] == [OSLO]

    def test_an_open_authority_with_new_ids_is_not_also_counted_closed(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        """One request closed ``full``, a later one open: still one id-only letter."""
        first = _send(workspace, tmp_path, lf_ledger, OSLO)
        at = ("--at", "2026-10-15T08:00:00Z")
        ok(workspace, "record-outcome", "--request", first, "--outcome", "full", *at)
        _link(lf_ledger, OSLO, "2025-01-01-1")
        _wave(workspace, tmp_path / "second", lf_ledger)
        manifest = str(tmp_path / "second" / "manifest.json")
        sent = ("record-sent", "--manifest", manifest, "--id", OSLO)
        ok(workspace, *sent, "--sent-at", "2026-10-20T08:00:00Z")
        _link(lf_ledger, OSLO)
        _, third = _wave(workspace, tmp_path / "third", lf_ledger)
        entry = _entries(third)[OSLO]
        assert entry["lf_ids"] == [NEW_ID]
        assert entry["general_clause"] is False
        assert third["skipped_open"] == []
        assert third["skipped_closed"] == []
