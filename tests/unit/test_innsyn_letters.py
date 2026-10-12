"""The letter generator and ``record-sent`` (#509 point 4), through the CLI only.

The operator's question: given the population, the directory and the LF
ledger, does a wave reach every authority it should, leave out the ones it
must, and can each letter be recorded as sent with what it asked for?
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.innsyn.ledger import requests
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.lf_ledger import LedgerEntry
from tests.unit.innsyn_support import fetched, innsyn

PAGE = "https://www.example.kommune.no/forskrifter"


def _set(workspace: Path, command: str, *args: str) -> None:
    result = innsyn(workspace, command, *args)
    assert result.exit_code == 0, result.output


def _recipient(workspace: Path, authority_id: str, address: str) -> None:
    _set(
        workspace,
        "set-recipient",
        "--id",
        authority_id,
        "--address",
        address,
        "--evidence-url",
        PAGE,
        "--read-at",
        "2026-10-03T10:00:00Z",
    )


def _entry(authority_id: str, lf_id: str, kind: str = "LF") -> str:
    return LedgerEntry(
        authority_id=authority_id,
        lf_id=lf_id,
        kind=kind,  # type: ignore[arg-type]
        first_seen=datetime(2026, 10, 1, tzinfo=UTC),
        url=PAGE,
        sha256="a" * 64,
    ).model_dump_json()


@pytest.fixture
def lf_ledger(tmp_path: Path) -> Path:
    path = tmp_path / "lf-ledger.jsonl"
    lines = [
        _entry("4646", "2024-09-19-2196"),
        _entry("4646", "2009-02-16-306", "LTII"),
        _entry("4646", "2022-12-15-2677"),
        _entry("4646", "2020-01-01-1", "forskrift"),
        _entry("0301", "2019-03-20-400"),
    ]
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _set(target, "set-sender", "--name", "Test Requester", "--email", "kontakt@example.no")
    _recipient(target, "4646", "postmottak@fjaler.kommune.no")
    _recipient(target, "0301", "postmottak@oslobystyre.no")
    _recipient(target, "46", "post@vlfk.no")
    _set(
        target,
        "set-maalform",
        "--id",
        "4646",
        "--form",
        "nn",
        "--evidence-url",
        PAGE,
        "--read-at",
        "2026-10-03T10:00:00Z",
    )
    return target


def _wave(workspace: Path, out: Path, lf_ledger: Path, *extra: str) -> dict[str, object]:
    result = innsyn(workspace, "letters", "--out", str(out), "--lf-ledger", str(lf_ledger), *extra)
    assert result.exit_code == 0, result.output
    manifest: dict[str, object] = json.loads((out / "manifest.json").read_text())
    return manifest


def _letters(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    rows = manifest["letters"]
    assert isinstance(rows, list)
    return {row["authority_id"]: row for row in rows}


class TestWave:
    def test_only_resolved_authorities_get_a_letter_in_id_order(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger)
        assert list(_letters(manifest)) == ["0301", "46", "4646"]
        unresolved = manifest["unresolved"]
        assert isinstance(unresolved, list)
        assert "1134" in unresolved and "4646" not in unresolved
        assert manifest["remaining"] == 0
        assert sorted(p.name for p in (tmp_path / "wave").iterdir()) == [
            "0301.txt",
            "46.txt",
            "4646.txt",
            "manifest.json",
        ]

    def test_part_a_is_lf_and_ltii_ids_less_held_ones(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        held = tmp_path / "held.txt"
        held.write_text("# held texts\n2022-12-15-2677  # from the archive\n\n")
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger, "--held", str(held))
        fjaler = _letters(manifest)["4646"]
        assert fjaler["lf_ids"] == ["2009-02-16-306", "2024-09-19-2196"]
        assert fjaler["general_clause"] is True
        assert (fjaler["maalform"], fjaler["maalform_recorded"]) == ("nn", True)
        assert (_letters(manifest)["0301"]["maalform_recorded"]) is False

    def test_the_wave_size_bounds_the_letters_and_says_what_is_left(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        manifest = _wave(workspace, tmp_path / "wave", lf_ledger, "--wave-size", "2")
        assert list(_letters(manifest)) == ["0301", "46"]
        assert manifest["remaining"] == 1

    def test_a_wave_is_reproducible(self, workspace: Path, tmp_path: Path, lf_ledger: Path) -> None:
        _wave(workspace, tmp_path / "one", lf_ledger)
        _wave(workspace, tmp_path / "two", lf_ledger)
        for name in ("0301.txt", "46.txt", "4646.txt"):
            assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()

    def test_an_authority_with_an_open_request_is_skipped_and_the_next_wave_moves_on(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        first = _wave(workspace, tmp_path / "one", lf_ledger, "--wave-size", "2")
        for authority_id in ("0301", "46"):
            _set(
                workspace,
                "record-sent",
                "--manifest",
                str(tmp_path / "one" / "manifest.json"),
                "--id",
                authority_id,
                "--sent-at",
                "2026-10-13T08:00:00Z",
            )
        second = _wave(workspace, tmp_path / "two", lf_ledger, "--wave-size", "2")
        assert list(_letters(first)) == ["0301", "46"]
        assert list(_letters(second)) == ["4646"]
        assert second["skipped_open"] == ["0301", "46"]

    def test_ids_already_asked_are_not_asked_again(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "one", lf_ledger)
        manifest = str(tmp_path / "one" / "manifest.json")
        sent = ("record-sent", "--manifest", manifest, "--id", "0301")
        _set(workspace, *sent, "--sent-at", "2026-10-13T08:00:00Z")
        request = "0301-20261013T080000Z"
        closing = ("--outcome", "full", "--at", "2026-10-15T08:00:00Z")
        _set(workspace, "record-outcome", "--request", request, *closing)
        again = _wave(workspace, tmp_path / "two", lf_ledger)
        assert _letters(again)["0301"]["lf_ids"] == []


class TestLetterText:
    def test_a_bokmaal_letter_with_known_ids(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        text = (tmp_path / "wave" / "0301.txt").read_text(encoding="utf-8")
        assert text.startswith(
            "Til: postmottak@oslobystyre.no\n"
            "Emne: Innsynskrav etter offentleglova \N{EN DASH} "
            "lokale forskrifter i Oslo kommune\n\n"
        )
        assert "Hvert punkt er et selvstendig krav" in text
        assert "Jeg ber ikke om Lovdatas konsoliderte tekster" in text
        assert "vedtaket i bystyret" in text
        assert "\n1. Forskrifter med kjent nummer\n" in text
        assert "- FOR-2019-03-20-400 (dato 20.03.2019, nr. 400)" in text
        assert "\n2. Andre lokale forskrifter\n" in text
        assert "saker av en bestemt art (offentleglova § 28 andre ledd)" in text
        assert "offentleglova § 30 første ledd" in text
        assert text.endswith("Med vennlig hilsen\nTest Requester\nkontakt@example.no\n")

    def test_a_nynorsk_letter(self, workspace: Path, tmp_path: Path, lf_ledger: Path) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        text = (tmp_path / "wave" / "4646.txt").read_text(encoding="utf-8")
        assert "Eg ber ikkje om Lovdata sine konsoliderte tekstar" in text
        assert "kommunen sine eigne arkivdokument" in text
        assert "vedtaket i kommunestyret" in text
        assert "saker av ein bestemt art" in text
        assert "FOR-2020-01-01-1" not in text
        assert text.endswith("Med venleg helsing\nTest Requester\nkontakt@example.no\n")

    def test_an_authority_with_no_known_ids_gets_the_general_point_alone(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        text = (tmp_path / "wave" / "46.txt").read_text(encoding="utf-8")
        assert "lokale forskrifter i Vestland fylkeskommune" in text
        assert "\n1. Lokale forskrifter\n" in text
        assert "kjent nummer" not in text
        assert "to nummererte punkter" not in text
        assert "vedtaket i fylkestinget" in text
        assert "fylkeskommunens egne arkivdokumenter" in text
        assert "liste eller oversikt" in text


class TestRefusals:
    def test_a_non_empty_output_directory_is_refused(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        out = tmp_path / "wave"
        out.mkdir()
        (out / "stray.txt").write_text("x")
        result = innsyn(workspace, "letters", "--out", str(out), "--lf-ledger", str(lf_ledger))
        assert result.exit_code == 1
        assert "not empty" in result.stderr

    def test_a_missing_lf_ledger_or_held_file_is_refused(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        out = str(tmp_path / "wave")
        gone = str(tmp_path / "none.jsonl")
        assert (
            "no LF ledger" in innsyn(workspace, "letters", "--out", out, "--lf-ledger", gone).stderr
        )
        held = ("--held", str(tmp_path / "none.txt"))
        result = innsyn(workspace, "letters", "--out", out, "--lf-ledger", str(lf_ledger), *held)
        assert "no such --held file" in result.stderr
        assert not (tmp_path / "wave").exists()

    def test_the_default_ledger_is_the_observatory_one(
        self, workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "observatory"
        root.mkdir()
        monkeypatch.setenv("LOVSPOR_OBSERVATORY_ROOT", str(root))
        monkeypatch.delenv("LOVSPOR_OUTPUT_REPO_PATH", raising=False)
        (root / "lf-ledger.jsonl").write_text(_entry("4646", "2024-09-19-2196") + "\n")
        result = innsyn(workspace, "letters", "--out", str(tmp_path / "wave"))
        assert result.exit_code == 0, result.output
        assert "1 with known LF ids" in result.stdout

    def test_a_damaged_lf_ledger_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        bad = tmp_path / "lf.jsonl"
        bad.write_text("{nope\n")
        result = innsyn(workspace, "letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(bad))
        assert result.exit_code == 1

    def test_no_sender_is_refused(
        self, tmp_path: Path, httpx_mock: HTTPXMock, lf_ledger: Path
    ) -> None:
        target = fetched(tmp_path / "bare", httpx_mock)
        result = innsyn(
            target, "letters", "--out", str(tmp_path / "w"), "--lf-ledger", str(lf_ledger)
        )
        assert result.exit_code == 1
        assert "set-sender" in result.stderr


class TestRecordSent:
    def test_it_records_what_the_letter_asked_for(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        manifest = str(tmp_path / "wave" / "manifest.json")
        _set(
            workspace,
            "record-sent",
            "--manifest",
            manifest,
            "--id",
            "4646",
            "--sent-at",
            "2026-10-13T08:00:00+02:00",
        )
        state = requests(InnsynWorkspace(workspace, []))["4646-20261013T060000Z"]
        assert state.sent.recipient == "postmottak@fjaler.kommune.no"
        assert state.sent.lf_ids == ("2009-02-16-306", "2022-12-15-2677", "2024-09-19-2196")
        assert state.sent.general_clause is True
        letter = json.loads((tmp_path / "wave" / "manifest.json").read_text())["letters"][2]
        assert state.sent.letter_sha256 == letter["letter_sha256"]

    def test_a_letter_whose_recipient_was_changed_is_refused(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path
    ) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        letter = tmp_path / "wave" / "4646.txt"
        letter.write_text(letter.read_text().replace("postmottak@fjaler", "post@fjaler"))
        manifest = str(tmp_path / "wave" / "manifest.json")
        args = ("--manifest", manifest, "--id", "4646", "--sent-at", "2026-10-13T08:00:00Z")
        result = innsyn(workspace, "record-sent", *args)
        assert result.exit_code == 1
        assert "is not the manifest's recipient" in result.stderr

    @pytest.mark.parametrize("authority_id", ["1134", "4646"])
    def test_an_unknown_letter_or_a_second_send_is_refused(
        self, workspace: Path, tmp_path: Path, lf_ledger: Path, authority_id: str
    ) -> None:
        _wave(workspace, tmp_path / "wave", lf_ledger)
        manifest = str(tmp_path / "wave" / "manifest.json")
        args = ("--manifest", manifest, "--id", authority_id, "--sent-at", "2026-10-13T08:00:00Z")
        if authority_id == "4646":
            _set(workspace, "record-sent", *args)
        result = innsyn(workspace, "record-sent", *args)
        assert result.exit_code == 1

    def test_a_missing_manifest_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        args = (
            "--manifest",
            str(tmp_path / "none.json"),
            "--id",
            "4646",
            "--sent-at",
            "2026-10-13T08:00Z",
        )
        result = innsyn(workspace, "record-sent", *args)
        assert result.exit_code == 1
        assert "not a wave manifest" in result.stderr
