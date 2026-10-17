"""Preparing and writing a received version (ADR-0016 Amendment 1, A1-A5).

The innsyn workspace is written only through the supported innsyn commands
(fetch-population against the captured KLASS responses, the pilot register,
record-received, classify-received) and read back with ``read_received``; the
corpus is a temporary ``lovverk`` checkout. An observed document is promoted
through ``lovspor promote``. The received file is named the way a kommune's
answer often is, after its case officer: the name must reach no output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pytest_httpx import HTTPXMock

from lovspor.errors import PromotionRefusedError
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.corpus import CorpusCheckout
from lovspor.promotion.decisions import Decision as Verdict
from lovspor.promotion.decisions import HumanDecision, StandingApproval
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.innsyn_authority import received_authority
from lovspor.promotion.innsyn_source import ReceivedArtifact, read_received
from lovspor.promotion.models import Authority, AuthorityType
from lovspor.promotion.plan import Held, Prepared, prepare
from lovspor.promotion.received_writer import received_write_set
from lovspor.promotion.writer import ObservationsFile, ReceivedVersion, WriteSet, apply
from tests.unit.innsyn_support import FIXTURES, fetched, innsyn
from tests.unit.promotion_cli_fixtures import (
    KLASS_VERSION,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    make_corpus,
    promote,
    register,
    store,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page
from tests.unit.test_promotion_render_received import RECEIVED_FRONT_MATTER_KEYS

FJALER = "4646-20261005T074425Z"
OSLO = "0301-20261005T074343Z"
REQUESTED = "2009-02-16-306"
RECEIVED_AT = "2026-10-07T12:45:06Z"
LATER = "2026-10-09T08:00:00Z"
FILE_NAME = "Svar på innsynskrav - Ola Nordmann.html"
CHANGED = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. februar 2020.")
NUMBERED = (REGULATION_LINES[0], "Dato: FOR-2019-12-12-2077", *REGULATION_LINES[1:])
LOCAL = "lokale-forskrifter"


def _run(workspace: Path, *args: str) -> str:
    result = innsyn(workspace, *args)
    assert result.exit_code == 0, result.output
    return result.output


@pytest.fixture
def workspace(tmp_path: Path, httpx_mock: HTTPXMock) -> Path:
    target = fetched(tmp_path / "innsyn", httpx_mock)
    _run(target, "import-pilot", "--register", str(FIXTURES / "pilot" / "sent.csv"))
    return target


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    return make_corpus(tmp_path)


def _received(
    workspace: Path,
    *,
    lines: tuple[str, ...] = REGULATION_LINES,
    at: str = RECEIVED_AT,
    request: str = FJALER,
) -> ReceivedArtifact:
    """Receive ``lines`` as an HTML answer, classify it a regulation text, read it back."""
    folder = workspace.parent / "mail" / at.replace(":", "")
    folder.mkdir(parents=True, exist_ok=True)
    document = folder / FILE_NAME
    document.write_bytes(html_page(lines))
    output = _run(
        workspace, "record-received", "--request", request, "--file", str(document), "--at", at
    )
    sha256 = output.split("document ")[1].split()[0]
    classify = ("--request", request, "--document", sha256, "--kind", "regulation_text")
    # Only Fjaler's request asked for the id the operator reads it as answering.
    answers = ("--answers", REQUESTED) if request == FJALER else ()
    _run(workspace, "classify-received", *classify, *answers)
    return read_received(InnsynWorkspace(workspace, []), request, sha256)


def _approval(received: ReceivedArtifact, prepared: Prepared, reason: str) -> StandingApproval:
    decision = HumanDecision(
        artifact=received.key,
        decision=Verdict.APPROVE,
        decided_by=REVIEWER,
        reviewer_role=REVIEWER_ROLE,
        decided_at=datetime(2026, 10, 16, 9, 30, tzinfo=UTC),
        reason=reason,
        content_hash=prepared.identity.content_hash,
        extractor_version=EXTRACTOR_VERSION,
    )
    return StandingApproval(decision=decision)


def _authority(workspace: Path, received: ReceivedArtifact) -> Authority:
    workspace_ = InnsynWorkspace(workspace, [])
    return received_authority(workspace_, received.key.authority_id, KLASS_VERSION)


def _prepared(workspace: Path, received: ReceivedArtifact, corpus: Path) -> Prepared:
    prepared = prepare(received, _authority(workspace, received), CorpusCheckout(corpus, []))
    assert isinstance(prepared, Prepared), prepared
    return prepared


def _writes(
    workspace: Path, received: ReceivedArtifact, corpus: Path, reason: str = "Vedtatt forskrift."
) -> WriteSet:
    prepared = _prepared(workspace, received, corpus)
    approval = _approval(received, prepared, reason)
    return received_write_set(prepared, received, approval, CorpusCheckout(corpus, []))


def _written(workspace: Path, received: ReceivedArtifact, corpus: Path) -> WriteSet:
    writes = _writes(workspace, received, corpus)
    apply(CorpusCheckout(corpus, []), writes)
    return writes


def _observations(writes: WriteSet) -> dict[str, object]:
    [path] = [p for p in writes.files if "/observations/" in p]
    loaded: dict[str, object] = json.loads(writes.files[path])
    return loaded


def _front_matter(writes: WriteSet) -> tuple[tuple[str, ...], dict[str, object]]:
    [path] = [p for p in writes.files if p.endswith(".md")]
    head = writes.files[path].split("---\n")[1]
    keys = tuple(line.split(":", 1)[0] for line in head.splitlines())
    return keys, yaml.safe_load(head)


def _refusal(workspace: Path, received: ReceivedArtifact, corpus: Path) -> str:
    with pytest.raises(PromotionRefusedError) as caught:
        _writes(workspace, received, corpus)
    return str(caught.value)


class TestTheWriteSet:
    def test_it_writes_exactly_the_four_files(self, workspace: Path, corpus: Path) -> None:
        writes = _writes(workspace, _received(workspace), corpus)
        [markdown] = [p for p in writes.files if p.endswith(".md")]
        slug = markdown.rsplit("/", 1)[1].removesuffix(".md")
        assert set(writes.files) == {
            markdown,
            f"{LOCAL}/4646/observations/{slug}.json",
            f"{LOCAL}/4646/evidence/{slug}.json",
            f"{LOCAL}/manifest.json",
        }

    def test_the_front_matter_is_the_received_one(self, workspace: Path, corpus: Path) -> None:
        keys, values = _front_matter(_writes(workspace, _received(workspace), corpus))
        assert keys == RECEIVED_FRONT_MATTER_KEYS
        assert {"observed_at_first", "source_url"}.isdisjoint(keys)
        assert values["basis"] == "received"
        assert values["received_at"] == RECEIVED_AT
        assert "http" not in json.dumps(values)

    def test_the_observations_file_holds_a_received_version_at_schema_2(
        self, workspace: Path, corpus: Path
    ) -> None:
        received = _received(workspace)
        writes = _writes(workspace, received, corpus)
        [path] = [p for p in writes.files if "/observations/" in p]
        document = ObservationsFile.model_validate_json(writes.files[path])
        assert document.schema_version == 2
        assert document.source_status is None
        [version] = document.versions
        assert isinstance(version, ReceivedVersion)
        assert version.received[0].received_at == RECEIVED_AT
        assert version.received[0].source_sha256 == received.key.sha256
        assert "source_status" not in _observations(writes)

    def test_the_audit_names_the_request_and_no_observation(
        self, workspace: Path, corpus: Path
    ) -> None:
        writes = _writes(workspace, _received(workspace), corpus)
        assert writes.audit.observations == ()
        assert writes.audit.observations_through == writes.audit.decided_at
        assert writes.audit.innsyn is not None
        assert writes.audit.innsyn.model_dump() == {
            "innsyn_request": FJALER,
            "received_at": RECEIVED_AT,
            "kind": "regulation_text",
            "answers": REQUESTED,
        }

    def test_the_manifest_last_seen_is_received_at(self, workspace: Path, corpus: Path) -> None:
        writes = _writes(workspace, _received(workspace), corpus)
        manifest = json.loads(writes.files[f"{LOCAL}/manifest.json"])
        [record] = manifest["documents"].values()
        assert record["last_seen"] == RECEIVED_AT
        assert (record["authority_id"], record["authority_type"]) == ("4646", "kommune")
        assert manifest["generated_at"] == "2026-10-16T09:30:00Z"

    def test_two_runs_write_identical_bytes(self, workspace: Path, corpus: Path) -> None:
        received = _received(workspace)
        first = _writes(workspace, received, corpus)
        assert _writes(workspace, received, corpus).files == first.files

    def test_no_file_name_mailbox_or_reviewer_name_is_written(
        self, workspace: Path, corpus: Path
    ) -> None:
        text = "".join(_writes(workspace, _received(workspace), corpus).files.values())
        for private in ("Ola Nordmann", "Svar på innsynskrav", "@", "postmottak", REVIEWER):
            assert private not in text


class TestIdentityIsFromTheTextAlone:
    def test_answers_never_mints_an_lf_id(self, workspace: Path, corpus: Path) -> None:
        writes = _writes(workspace, _received(workspace), corpus)
        _, values = _front_matter(writes)
        assert str(values["id"]).startswith("lk-4646-")
        assert values["ref_id"] is None
        assert writes.audit.identity.scheme == "lk"
        assert writes.audit.identity.ref_id is None
        assert writes.audit.identity.candidates == ()

    def test_answers_is_written_only_in_the_innsyn_audit(
        self, workspace: Path, corpus: Path
    ) -> None:
        writes = _writes(workspace, _received(workspace), corpus)
        mentions = [p for p, text in writes.files.items() if REQUESTED in text]
        assert mentions == [p for p in writes.files if "/observations/" in p]
        observations = _observations(writes)
        [version] = observations["versions"]  # type: ignore[misc]
        assert version["promotion"]["innsyn"]["answers"] == REQUESTED
        text = json.dumps({**version, "promotion": {**version["promotion"], "innsyn": None}})
        assert REQUESTED not in text
        assert "20090216" not in "".join(writes.files.values())

    def test_the_texts_own_header_mints_the_id_whatever_answers_names(
        self, workspace: Path, corpus: Path
    ) -> None:
        writes = _writes(workspace, _received(workspace, lines=NUMBERED), corpus)
        _, values = _front_matter(writes)
        assert values["id"] == "lf-20191212-2077"
        assert values["ref_id"] == "forskrift/2019-12-12-2077"
        assert writes.audit.innsyn is not None
        assert writes.audit.innsyn.answers == REQUESTED


class TestVersions:
    def test_changed_text_with_the_same_receipt_time_is_refused(
        self, workspace: Path, corpus: Path
    ) -> None:
        _written(workspace, _received(workspace), corpus)
        changed = _received(workspace, lines=CHANGED, at=RECEIVED_AT)
        before = {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob("*") if p.is_file()}
        assert "received before the current version's" in _refusal(workspace, changed, corpus)
        assert {
            p.relative_to(corpus): p.read_bytes() for p in corpus.rglob("*") if p.is_file()
        } == before

    @pytest.mark.parametrize("damage", ["missing", "malformed"])
    def test_unreadable_version_history_refuses_a_new_received_version(
        self, workspace: Path, corpus: Path, damage: str
    ) -> None:
        first = _written(workspace, _received(workspace), corpus)
        [relative] = [p for p in first.files if "/observations/" in p]
        path = corpus / relative
        if damage == "missing":
            path.unlink()
        else:
            path.write_text('{"versions":', encoding="utf-8")
        changed = _received(workspace, lines=CHANGED, at=LATER)
        before = {p.relative_to(corpus): p.read_bytes() for p in corpus.rglob("*") if p.is_file()}
        assert "does not read; no received version is written beside it" in _refusal(
            workspace, changed, corpus
        )
        assert {
            p.relative_to(corpus): p.read_bytes() for p in corpus.rglob("*") if p.is_file()
        } == before

    def test_a_later_received_text_is_version_2(self, workspace: Path, corpus: Path) -> None:
        first = _received(workspace)
        _written(workspace, first, corpus)
        writes = _writes(workspace, _received(workspace, lines=CHANGED, at=LATER), corpus)
        document = ObservationsFile.model_validate_json(
            next(t for p, t in writes.files.items() if "/observations/" in p)
        )
        assert [v.version for v in document.versions] == [1, 2]
        assert all(isinstance(v, ReceivedVersion) for v in document.versions)
        _, values = _front_matter(writes)
        assert (values["version"], values["received_at"]) == (2, LATER)

    def test_a_text_received_before_the_current_version_is_refused(
        self, workspace: Path, corpus: Path
    ) -> None:
        _written(workspace, _received(workspace, at=LATER), corpus)
        earlier = _received(workspace, lines=CHANGED, at=RECEIVED_AT)
        assert "received before the current version's" in _refusal(workspace, earlier, corpus)

    def test_the_same_received_text_again_writes_nothing(
        self, workspace: Path, corpus: Path
    ) -> None:
        received = _received(workspace)
        _written(workspace, received, corpus)
        assert "already in the corpus; nothing to write" in _refusal(workspace, received, corpus)


class TestRefusals:
    def test_a_file_mentioning_nlod_is_refused(self, workspace: Path, corpus: Path) -> None:
        with pytest.raises(PromotionRefusedError, match="would mention NLOD"):
            _writes(workspace, _received(workspace), corpus, reason="Ikke NLOD-data.")

    def test_a_held_text_is_held_as_an_observed_one(self, workspace: Path, corpus: Path) -> None:
        received = _received(workspace, lines=REGULATION_LINES[:1])
        held = prepare(received, _authority(workspace, received), CorpusCheckout(corpus, []))
        assert isinstance(held, Held)
        assert held.stage == "extraction"


class TestAnObservedDocument:
    @pytest.fixture
    def observed(self, tmp_path: Path, corpus: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """``corpus`` with the regulation promoted for 0301 from an observed page."""
        root = tmp_path / "observatory"
        root.mkdir()
        monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(root))
        monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
        register(root)
        sha256 = store(root, html_page())
        assert approve(sha256, Decision().write(tmp_path)).exit_code == 0
        result = promote("local", sha256, corpus)
        assert result.exit_code == 0, result.output
        return corpus

    def test_a_received_new_version_of_it_is_refused(self, workspace: Path, observed: Path) -> None:
        received = _received(workspace, lines=CHANGED, request=OSLO)
        message = _refusal(workspace, received, observed)
        assert "switching the primary channel is a recorded decision (ADR-0016 A4)" in message

    def test_a_received_copy_of_its_text_is_refused_not_corroborated(
        self, workspace: Path, observed: Path
    ) -> None:
        received = _received(workspace, request=OSLO)
        assert "ADR-0016 A4" in _refusal(workspace, received, observed)


class TestAuthority:
    def test_the_block_is_klass_s_norwegian_name(self, workspace: Path) -> None:
        listed = InnsynWorkspace(workspace, [])
        assert received_authority(listed, "4646", KLASS_VERSION) == Authority(
            id="4646", type=AuthorityType.KOMMUNE, name="Fjaler", klass_version=KLASS_VERSION
        )
        assert received_authority(listed, "0301", KLASS_VERSION).name == "Oslo"

    def test_a_fylkeskommune_is_named_without_its_type(self, workspace: Path) -> None:
        authority = received_authority(InnsynWorkspace(workspace, []), "18", KLASS_VERSION)
        assert (authority.type, authority.name) == (AuthorityType.FYLKESKOMMUNE, "Nordland")

    def test_an_id_absent_from_the_population_is_refused(self, workspace: Path) -> None:
        with pytest.raises(PromotionRefusedError, match="0000 is not in the latest innsyn"):
            received_authority(InnsynWorkspace(workspace, []), "0000", KLASS_VERSION)

    def test_a_workspace_without_a_population_is_refused(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(PromotionRefusedError, match="population cannot be read"):
            received_authority(InnsynWorkspace(empty, []), "4646", KLASS_VERSION)
