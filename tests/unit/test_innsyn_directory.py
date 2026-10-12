"""Recipients, målform and sender, set and read through the CLI only (#509 point 2).

The operator's question: can every directory field be reached using only
supported commands, and does an authority without evidence stay unresolved?
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from lovspor.errors import InnsynRecordError
from lovspor.innsyn.directory import contacts, directory_journal
from lovspor.innsyn.sender import read_sender
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.log import ObservationLog
from lovspor.observatory.model import ArtifactObservation, RetrievalProvenance
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT, ObservatoryRoot
from tests.unit.innsyn_support import fetched, innsyn

FJALER = "https://www.fjaler.kommune.no/"


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    return fetched(tmp_path / "innsyn", httpx_mock)


def _contacts(workspace: Path) -> dict[str, object]:
    return dict(contacts(directory_journal(InnsynWorkspace(workspace, [])).records()))


def _set_recipient(workspace: Path, authority_id: str, address: str) -> None:
    result = innsyn(
        workspace,
        "set-recipient",
        "--id",
        authority_id,
        "--address",
        address,
        "--evidence-url",
        FJALER,
        "--read-at",
        "2026-10-03T10:00:00Z",
    )
    assert result.exit_code == 0, result.output


class TestRecipient:
    def test_it_is_recorded_with_its_evidence(self, workspace: Path) -> None:
        _set_recipient(workspace, "4646", " Postmottak@Fjaler.Kommune.no ")
        recipient = contacts(directory_journal(InnsynWorkspace(workspace, [])).records())[
            "4646"
        ].recipient
        assert recipient is not None
        assert recipient.address == "postmottak@fjaler.kommune.no"
        assert recipient.evidence.url == FJALER
        assert recipient.evidence.read_at == datetime(2026, 10, 3, 10, tzinfo=UTC)

    def test_the_latest_record_stands_and_the_first_stays_as_history(self, workspace: Path) -> None:
        _set_recipient(workspace, "4646", "postmottak@fjaler.kommune.no")
        _set_recipient(workspace, "4646", "post@fjaler.kommune.no")
        journal = directory_journal(InnsynWorkspace(workspace, []))
        assert len(journal.records()) == 2
        recipient = contacts(journal.records())["4646"].recipient
        assert recipient is not None
        assert recipient.address == "post@fjaler.kommune.no"

    def test_an_authority_outside_the_population_is_refused(self, workspace: Path) -> None:
        result = innsyn(
            workspace,
            "set-recipient",
            "--id",
            "0300",
            "--address",
            "postmottak@oslo.kommune.no",
            "--evidence-url",
            FJALER,
            "--read-at",
            "2026-10-03T10:00:00Z",
        )
        assert result.exit_code == 1
        assert "not in the population" in result.stderr
        assert _contacts(workspace) == {}

    @pytest.mark.parametrize(
        ("option", "value"),
        [("--address", "not-an-address"), ("--evidence-url", "fjaler.kommune.no")],
    )
    def test_a_malformed_address_or_evidence_is_refused(
        self, workspace: Path, option: str, value: str
    ) -> None:
        args = {
            "--address": "postmottak@fjaler.kommune.no",
            "--evidence-url": FJALER,
            "--read-at": "2026-10-03T10:00:00Z",
        }
        args[option] = value
        flat = [part for pair in args.items() for part in pair]
        result = innsyn(workspace, "set-recipient", "--id", "4646", *flat)
        assert result.exit_code == 1
        assert _contacts(workspace) == {}

    def test_a_naive_read_time_is_a_usage_error(self, workspace: Path) -> None:
        result = innsyn(
            workspace,
            "set-recipient",
            "--id",
            "4646",
            "--address",
            "postmottak@fjaler.kommune.no",
            "--evidence-url",
            FJALER,
            "--read-at",
            "2026-10-03T10:00:00",
        )
        assert result.exit_code == 2
        assert _contacts(workspace) == {}


class TestAuthoritiesListing:
    def test_an_authority_without_a_recorded_address_is_unresolved(self, workspace: Path) -> None:
        _set_recipient(workspace, "4646", "postmottak@fjaler.kommune.no")
        result = innsyn(workspace, "authorities")
        assert result.exit_code == 0, result.output
        rows = {line.split("\t")[0]: line.split("\t") for line in result.stdout.splitlines()}
        assert rows["4646"][3:] == ["postmottak@fjaler.kommune.no", FJALER, "nb (unset)"]
        assert rows["1134"][3] == "UNRESOLVED"
        assert rows["46"][1:3] == ["fylkeskommune", "Vestland fylkeskommune"]
        assert "1 with a recorded recipient" in result.stderr
        assert f"{len(rows) - 1} unresolved" in result.stderr

    def test_maalform_is_recorded_and_listed(self, workspace: Path) -> None:
        result = innsyn(
            workspace,
            "set-maalform",
            "--id",
            "1134",
            "--form",
            "nn",
            "--evidence-url",
            "https://www.sprakradet.no/",
            "--read-at",
            "2026-10-12T08:00:00Z",
        )
        assert result.exit_code == 0, result.output
        listing = innsyn(workspace, "authorities")
        row = next(r for r in listing.stdout.splitlines() if r.startswith("1134\t"))
        assert row.split("\t")[5] == "nn"
        assert "1 with a recorded målform" in listing.stderr

    def test_an_unknown_maalform_is_refused(self, workspace: Path) -> None:
        result = innsyn(
            workspace,
            "set-maalform",
            "--id",
            "1134",
            "--form",
            "nynorsk",
            "--evidence-url",
            "https://www.sprakradet.no/",
            "--read-at",
            "2026-10-12T08:00:00Z",
        )
        assert result.exit_code == 1
        assert _contacts(workspace) == {}

    def test_a_damaged_directory_is_refused_not_read_past(self, workspace: Path) -> None:
        _set_recipient(workspace, "4646", "postmottak@fjaler.kommune.no")
        with (workspace / "directory.jsonl").open("a") as handle:
            handle.write('{"kind":')
        result = innsyn(workspace, "authorities")
        assert result.exit_code == 1
        assert "incomplete" in result.stderr
        with pytest.raises(InnsynRecordError):
            directory_journal(InnsynWorkspace(workspace, [])).records()


class TestSender:
    def test_it_is_recorded_and_replaced(self, workspace: Path) -> None:
        first = innsyn(workspace, "set-sender", "--name", "A Person", "--email", "a@example.no")
        assert first.exit_code == 0, first.output
        second = innsyn(
            workspace, "set-sender", "--name", " Owner Name ", "--email", "Kontakt@Example.no"
        )
        assert second.exit_code == 0, second.output
        sender = read_sender(InnsynWorkspace(workspace, []))
        assert (sender.name, sender.email) == ("Owner Name", "kontakt@example.no")

    def test_a_blank_name_is_refused(self, workspace: Path) -> None:
        result = innsyn(workspace, "set-sender", "--name", "  ", "--email", "a@example.no")
        assert result.exit_code == 1
        assert not (workspace / "sender.json").exists()


@pytest.fixture
def archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ObservationLog:
    root = tmp_path / "observatory"
    root.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(root))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    return ObservationLog(ObservatoryRoot(root, []))


def _capture(log: ObservationLog, url: str, body: str, authority_id: str = "4646") -> None:
    payload = body.encode()
    record = ArtifactObservation(
        authority_id=authority_id,
        url=url,
        observed_at=datetime(2026, 9, 30, 9, tzinfo=UTC),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="test-agent",
            rate_limit_seconds=1.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html; charset=utf-8",
        http_status=200,
    )
    log.append_artifact(record, payload)


class TestCandidates:
    def test_mailboxes_are_proposed_with_the_shortest_citation(
        self, tmp_path: Path, archive: ObservationLog
    ) -> None:
        _capture(archive, FJALER + "tenester/kontakt/", "<p>postmottak@fjaler.kommune.no</p>")
        _capture(archive, FJALER, "<p>Postmottak@Fjaler.kommune.no post@fjaler.kommune.no</p>")
        _capture(archive, "https://x.no/", "<p>postmottak@suldal.kommune.no</p>", "1134")
        result = innsyn(tmp_path / "innsyn", "recipient-candidates", "--id", "4646")
        assert result.exit_code == 0, result.output
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        assert [row[:4] for row in rows] == [
            ["4646", "postmottak@fjaler.kommune.no", "2", FJALER],
            ["4646", "post@fjaler.kommune.no", "1", FJALER],
        ]
        assert "2 candidates across 1 authorities" in result.stderr
        assert not (tmp_path / "innsyn").exists()

    def test_without_an_id_every_authority_is_scanned(
        self, tmp_path: Path, archive: ObservationLog
    ) -> None:
        _capture(archive, FJALER, "<p>postmottak@fjaler.kommune.no</p>")
        _capture(archive, "https://x.no/", "<p>postmottak@suldal.kommune.no</p>", "1134")
        result = innsyn(tmp_path / "innsyn", "recipient-candidates")
        assert [line.split("\t")[0] for line in result.stdout.splitlines()] == ["1134", "4646"]

    def test_a_damaged_log_is_refused(self, tmp_path: Path, archive: ObservationLog) -> None:
        _capture(archive, FJALER, "<p>postmottak@fjaler.kommune.no</p>")
        with archive.log_path.open("ab") as handle:
            handle.write(b'{"torn":')
        result = innsyn(tmp_path / "innsyn", "recipient-candidates")
        assert result.exit_code == 1
        assert "unreadable record" in result.stderr


def _variant(log: ObservationLog, body: str, **changes: object) -> None:
    payload = body.encode()
    record = ArtifactObservation(
        authority_id="4646",
        url=FJALER,
        observed_at=datetime(2026, 9, 30, 9, tzinfo=UTC),
        provenance=RetrievalProvenance(
            adapter="http",
            channel="http",
            discovery_method="sitemap",
            user_agent="test-agent",
            rate_limit_seconds=1.0,
        ),
        sha256=hashlib.sha256(payload).hexdigest(),
        content_type="text/html",
        http_status=200,
    )
    log.append_artifact(record.model_copy(update=changes), payload)


class TestCandidateScanEdges:
    def test_non_html_and_error_pages_are_not_read(
        self, tmp_path: Path, archive: ObservationLog
    ) -> None:
        _variant(archive, "postmottak@pdf.no", content_type="application/pdf")
        _variant(archive, "postmottak@error.no", http_status=404)
        result = innsyn(tmp_path / "innsyn", "recipient-candidates")
        assert result.stdout == ""

    def test_the_same_bytes_count_once_and_a_longer_page_keeps_the_short_citation(
        self, tmp_path: Path, archive: ObservationLog
    ) -> None:
        _capture(archive, FJALER, "<p>postmottak@fjaler.kommune.no</p>")
        _capture(archive, FJALER + "a/", "<p>postmottak@fjaler.kommune.no</p>")
        _capture(archive, FJALER + "longer/page/", "<p>postmottak@fjaler.kommune.no </p>")
        result = innsyn(tmp_path / "innsyn", "recipient-candidates")
        assert result.stdout.splitlines()[0].split("\t")[1:4] == [
            "postmottak@fjaler.kommune.no",
            "2",
            FJALER,
        ]

    def test_a_missing_blob_is_skipped(self, tmp_path: Path, archive: ObservationLog) -> None:
        body = "<p>postmottak@fjaler.kommune.no</p>"
        _capture(archive, FJALER, body)
        archive.blob_path(hashlib.sha256(body.encode()).hexdigest()).unlink()
        result = innsyn(tmp_path / "innsyn", "recipient-candidates")
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
