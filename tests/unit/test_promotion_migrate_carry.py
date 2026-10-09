"""``promote migrate`` carrying a standing approval across an extractor bump (owner, 2026-10-08).

The owner's decision: when an extractor bump leaves a published local
regulation's rendering byte-identical, the standing approval carries to the
running extractor with no new review; any byte difference still needs a fresh
``promote approve``. A carry is recorded as what it is — a ``carried`` record
naming the approval it rests on, never a decision a person made now — and the
published ``promotion`` block says the approval was given at one extractor and
carried to another.

Every state is reached the way an operator reaches it (the backfilled corpus,
``promote approve``, ``promote migrate``, ``git``), except the earlier engine,
which is the running one with ``EXTRACTOR_VERSION`` set one lower. The tests
read ``EXTRACTOR_VERSION``, never a number.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import Result

from lovspor.observatory.storage import ObservatoryRoot
from lovspor.promotion import extract
from lovspor.promotion.decisions import (
    DECISIONS_FILENAME,
    ApprovalReference,
    ArtifactKey,
    CarriedRecord,
    DecisionLog,
    HumanDecision,
)
from lovspor.promotion.extract import EXTRACTOR_VERSION
from lovspor.promotion.fields import read_regulation
from tests.unit.backfilled_corpus_fixtures import CHANGED, backfilled_corpus, dated_git
from tests.unit.promotion_cli_fixtures import (
    AUTHORITY,
    FIRST_SEEN,
    PAGE_URL,
    REVIEWER,
    REVIEWER_ROLE,
    Decision,
    approve,
    git,
    invoke,
    make_corpus,
    promote,
    store,
    tree,
)
from tests.unit.promotion_fixtures import REGULATION_LINES, html_page

LOCAL = "lokale-forskrifter"
OLD = EXTRACTOR_VERSION - 1
BASIS = "byte-identical rendering"
_VERSIONED = ("extract", "plan", "commands", "backfill", "intervals")
LATER = (*REGULATION_LINES[:-1], "Forskriften trer i kraft 1. mars 2020.")


@contextmanager
def _earlier_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with monkeypatch.context() as patched:
        for module in _VERSIONED:
            patched.setattr(f"lovspor.promotion.{module}.EXTRACTOR_VERSION", OLD)
        yield


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The backfilled corpus (v1-v3, A→B→A), approved and promoted by the earlier engine."""
    with _earlier_engine(monkeypatch):
        return backfilled_corpus(tmp_path, monkeypatch)


@pytest.fixture
def root(tmp_path: Path, corpus: Path) -> Path:
    return tmp_path / "observatory"


def _observations(corpus: Path) -> dict[str, object]:
    [path] = (corpus / LOCAL / AUTHORITY / "observations").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _versions(corpus: Path) -> list[dict[str, object]]:
    versions = _observations(corpus)["versions"]
    assert isinstance(versions, list)
    return versions


def _blobs(corpus: Path) -> tuple[str, str]:
    """The blobs of A and B, as the published observations name them."""
    v1, v2, _ = _versions(corpus)
    return v1["source_sha256s"][0], v2["source_sha256s"][0]  # type: ignore[index]


def _record(corpus: Path) -> dict[str, object]:
    manifest = json.loads((corpus / LOCAL / "manifest.json").read_text(encoding="utf-8"))
    [record] = manifest["documents"].values()
    return record


def _migrate(corpus: Path, *extra: str) -> Result:
    return invoke("promote", "migrate", "--corpus", str(corpus), *extra)


def _log(root: Path) -> bytes:
    return (root / DECISIONS_FILENAME).read_bytes()


def _appended(root: Path, before: bytes) -> list[dict[str, object]]:
    after = _log(root)
    assert after.startswith(before)
    return [json.loads(line) for line in after[len(before) :].decode().splitlines()]


def _commit_printed(corpus: Path, output: str) -> str:
    line = next(
        line
        for line in output.splitlines()
        if line.startswith("  git -C") and " commit -m " in line
    )
    subject = line.split(" commit -m ", 1)[1].strip("'")
    git(corpus, "add", "--", LOCAL)
    git(corpus, "commit", "-q", "-m", subject)
    return subject


def _approve(sha256: str, tmp_path: Path, decision: str = "approve") -> None:
    result = approve(sha256, Decision(decision=decision).write(tmp_path))
    assert result.exit_code == 0, result.output


def _markdown(snapshot: dict[str, bytes]) -> dict[str, bytes]:
    return {path: data for path, data in snapshot.items() if path.endswith(".md")}


class TestIdenticalBytesCarryTheStandingApproval:
    def test_migrates_with_nothing_asked_of_the_owner(self, root: Path, corpus: Path) -> None:
        before, log_before = tree(corpus), _log(root)
        observations_before = _versions(corpus)

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        assert "carried" in result.output
        after = tree(corpus)
        assert _markdown(after) == _markdown(before)
        assert _record(corpus)["extractor_version"] == EXTRACTOR_VERSION
        appended = _appended(root, log_before)
        assert [r["kind"] for r in appended] == ["carried", "carried", "carried", "migrated"]
        assert not any(r.get("kind") == "decision" for r in appended)
        for old, new in zip(observations_before, _versions(corpus), strict=True):
            promotion = new["promotion"]
            assert isinstance(promotion, dict)
            carried = promotion.pop("approval_carried")
            assert carried["approved_at_extractor"] == OLD
            assert carried["carried_to_extractor"] == EXTRACTOR_VERSION
            assert carried["basis"] == BASIS
            assert promotion == {**old["promotion"], "extractor_version": EXTRACTOR_VERSION}  # type: ignore[dict-item]

    def test_the_carried_record_names_the_approval_and_no_person(
        self, root: Path, corpus: Path
    ) -> None:
        a, b = _blobs(corpus)
        log = DecisionLog(ObservatoryRoot(root, []))
        approvals = {r.artifact.sha256: r for r in log.records() if isinstance(r, HumanDecision)}
        log_before = _log(root)

        assert _migrate(corpus).exit_code == 0

        carried = [r for r in _appended(root, log_before) if r["kind"] == "carried"]
        assert [(r["artifact"]["sha256"], r["version"]) for r in carried] == [  # type: ignore[index]
            (a, 1),
            (b, 2),
            (a, 3),
        ]
        for record in carried:
            assert "decided_by" not in json.dumps(record)
            assert REVIEWER not in json.dumps(record, ensure_ascii=False)
            original = approvals[record["artifact"]["sha256"]]  # type: ignore[index]
            assert record["approval"] == {
                "decision": "approve",
                "decided_at": original.decided_at.isoformat().replace("+00:00", "Z"),
                "extractor_version": OLD,
                "reviewer_role": REVIEWER_ROLE,
            }
            assert (record["from_extractor_version"], record["to_extractor_version"]) == (
                OLD,
                EXTRACTOR_VERSION,
            )
            assert record["content_hash"] == original.content_hash
            assert record["basis"] == BASIS

    def test_a_rerun_is_a_no_op(self, root: Path, corpus: Path) -> None:
        _commit_printed(corpus, _migrate(corpus).output)
        before, log_before = tree(corpus), _log(root)

        rerun = _migrate(corpus)

        assert rerun.exit_code == 0, rerun.output
        assert f"already at extractor v{EXTRACTOR_VERSION}" in rerun.output
        assert tree(corpus) == before
        assert git(corpus, "status", "--porcelain") == ""
        assert _log(root) == log_before

    def test_the_dry_run_shows_the_carry_and_records_nothing(
        self, root: Path, corpus: Path
    ) -> None:
        before, log_before = tree(corpus), _log(root)

        result = _migrate(corpus, "--dry-run")

        assert result.exit_code == 0, result.output
        assert '+          "basis": "byte-identical rendering",' in result.output
        assert f'+          "approved_at_extractor": {OLD},' in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before


class TestAByteDifferenceStillNeedsAFreshApproval:
    def test_an_earlier_version_whose_rendering_changes_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def b_read_otherwise(lines: tuple[str, ...]) -> object:
            regulation, fields = read_regulation(lines)  # type: ignore[misc]
            if CHANGED[-1] in lines:
                fields = fields.model_copy(update={"ikraft_text": "straks"})
            return regulation, fields

        with _earlier_engine(monkeypatch), monkeypatch.context() as patched:
            patched.setattr(extract, "read_regulation", b_read_otherwise)
            corpus = backfilled_corpus(tmp_path, monkeypatch)
        root = tmp_path / "observatory"
        _, b = _blobs(corpus)
        before, log_before = tree(corpus), _log(root)

        result = _migrate(corpus)

        assert result.exit_code == 1, result.output
        assert f"v2 (approve one of {b})" in result.output
        assert "not byte-identical" in result.output
        assert "its rendering changes though its text does not" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_a_one_byte_difference_in_the_rendering_is_refused(
        self, root: Path, corpus: Path
    ) -> None:
        [markdown] = (corpus / LOCAL / AUTHORITY).glob("*.md")
        markdown.write_bytes(markdown.read_bytes() + b"\n")
        dated_git(corpus, "2026-10-01T12:00:00Z", "commit", "-q", "-am", "test: one byte more")
        before, log_before = tree(corpus), _log(root)

        result = _migrate(corpus)

        assert result.exit_code == 1, result.output
        assert "its rendering changes though its text does not" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_an_earlier_version_not_shown_identical_needs_its_own_approval(
        self, root: Path, corpus: Path
    ) -> None:
        # The earlier versions' published bytes are their commits; a checkout whose history
        # does not hold them cannot show them identical, so nothing is carried for them.
        a, _ = _blobs(corpus)
        git(corpus, "checkout", "-q", "--orphan", "squashed")
        git(corpus, "commit", "-q", "-m", "test: history squashed away")
        before, log_before = tree(corpus), _log(root)

        result = _migrate(corpus)

        assert result.exit_code == 1, result.output
        assert f"v1 (approve one of {a})" in result.output
        assert "byte-identical" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before


class TestOnlyAnApprovalIsCarried:
    @pytest.mark.parametrize("decision", ["reject", "hold"])
    def test_a_standing_reject_or_hold_is_refused(
        self, root: Path, corpus: Path, tmp_path: Path, decision: str
    ) -> None:
        a, _ = _blobs(corpus)
        _approve(a, tmp_path, decision)
        before, log_before = tree(corpus), _log(root)

        result = _migrate(corpus)

        assert result.exit_code == 1, result.output
        assert f"standing decision is {decision}" in result.output
        assert tree(corpus) == before
        assert _log(root) == log_before

    def test_an_approval_of_another_text_is_not_carried(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        log = DecisionLog(ObservatoryRoot(root, []))
        [first, *_] = [r for r in log.records() if isinstance(r, HumanDecision)]
        log.append(first.model_copy(update={"content_hash": "f" * 64}))
        before = tree(corpus)

        result = _migrate(corpus)

        assert result.exit_code == 1, result.output
        assert "another text or extractor" in result.output
        assert tree(corpus) == before


class TestAFreshApprovalWins:
    def test_a_fresh_approval_is_used_and_nothing_is_carried(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        for sha256 in _blobs(corpus):
            _approve(sha256, tmp_path)
        log_before = _log(root)

        result = _migrate(corpus)

        assert result.exit_code == 0, result.output
        assert [r["kind"] for r in _appended(root, log_before)] == ["migrated"]
        for version in _versions(corpus):
            promotion = version["promotion"]
            assert isinstance(promotion, dict)
            assert "approval_carried" not in promotion
            assert promotion["extractor_version"] == EXTRACTOR_VERSION


class TestTheCarriedStateIsReachableThroughSupportedInterfacesOnly:
    """Can a document migrated by carry be reached, and worked on, using only commands?"""

    def test_migrate_commit_then_observe_backfill_and_local_accept_it(
        self, root: Path, corpus: Path
    ) -> None:
        a, _ = _blobs(corpus)
        stuck = invoke("promote", "observe", "--corpus", str(corpus))
        assert "that is a migration, not a refresh" in stuck.output
        _commit_printed(corpus, invoke("promote", "history", "--corpus", str(corpus)).output)

        _commit_printed(corpus, _migrate(corpus).output)

        observed = invoke("promote", "observe", "--corpus", str(corpus))
        assert observed.exit_code == 0, observed.output
        assert "skipped" not in observed.output
        backfill = promote("backfill", a, corpus)
        assert backfill.exit_code == 0, backfill.output
        assert "Nothing to write" in backfill.output
        unchanged = promote("local", a, corpus)
        assert unchanged.exit_code == 0, unchanged.output
        assert "Unchanged" in unchanged.output

    def test_a_new_version_after_a_carry_backfills_on_its_own_fresh_approval(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _blobs(corpus)
        _commit_printed(corpus, _migrate(corpus).output)
        later = datetime(2026, 9, 1, 12, tzinfo=UTC)
        assert later > FIRST_SEEN
        c = store(root, html_page(LATER), observed_at=later)
        _approve(c, tmp_path)

        result = promote("backfill", a, corpus)

        assert result.exit_code == 0, result.output
        assert f"wrote {LOCAL}/manifest.json" in result.output
        v1, v2, v3, v4 = _versions(corpus)
        for carried in (v1, v2, v3):
            assert carried["promotion"]["approval_carried"]["basis"] == BASIS  # type: ignore[index]
        assert "approval_carried" not in v4["promotion"]  # type: ignore[operator]
        assert v4["promotion"]["extractor_version"] == EXTRACTOR_VERSION  # type: ignore[index]


class TestEveryWriterStampsACarry:
    """Decisions §19: an approval a writer uses via a carry is published as carried."""

    def _carry_all(self, root: Path) -> None:
        log = DecisionLog(ObservatoryRoot(root, []))
        carried_at = datetime(2026, 10, 8, 12, tzinfo=UTC)
        for decision in [r for r in log.records() if isinstance(r, HumanDecision)]:
            assert decision.content_hash is not None
            log.append(
                CarriedRecord(
                    artifact=decision.artifact,
                    carried_at=carried_at,
                    doc_id="lk-0301-000000000000",
                    version=1,
                    content_hash=decision.content_hash,
                    from_extractor_version=OLD,
                    to_extractor_version=EXTRACTOR_VERSION,
                    approval=ApprovalReference.of(decision),
                )
            )

    def test_local_publishes_a_carried_approval_as_carried(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _blobs(corpus)
        self._carry_all(root)
        fresh = make_corpus(tmp_path / "fresh")

        result = promote("local", a, fresh)

        assert result.exit_code == 0, result.output
        [path] = (fresh / LOCAL / AUTHORITY / "observations").glob("*.json")
        [version] = json.loads(path.read_text(encoding="utf-8"))["versions"]
        assert version["promotion"]["extractor_version"] == EXTRACTOR_VERSION
        assert version["promotion"]["approval_carried"] == {
            "approved_at_extractor": OLD,
            "basis": BASIS,
            "carried_at": "2026-10-08T12:00:00Z",
            "carried_to_extractor": EXTRACTOR_VERSION,
        }

    def test_a_fresh_approval_is_published_without_a_carry(
        self, root: Path, corpus: Path, tmp_path: Path
    ) -> None:
        a, _ = _blobs(corpus)
        self._carry_all(root)
        _approve(a, tmp_path)
        fresh = make_corpus(tmp_path / "fresh")

        result = promote("local", a, fresh)

        assert result.exit_code == 0, result.output
        [path] = (fresh / LOCAL / AUTHORITY / "observations").glob("*.json")
        [version] = json.loads(path.read_text(encoding="utf-8"))["versions"]
        assert "approval_carried" not in version["promotion"]


class TestTheCarriedRecord:
    def _decision(self) -> HumanDecision:
        return HumanDecision(
            artifact=ArtifactKey(authority_id=AUTHORITY, sha256="a" * 64, source_url=PAGE_URL),
            decision="approve",  # type: ignore[arg-type]
            decided_by=REVIEWER,
            reviewer_role=REVIEWER_ROLE,
            decided_at=FIRST_SEEN,
            reason="Vedtatt forskrift.",
            content_hash="b" * 64,
            extractor_version=OLD,
        )

    def _carried(self, decision: HumanDecision, to: int) -> CarriedRecord:
        return CarriedRecord(
            artifact=decision.artifact,
            carried_at=datetime(2026, 10, 8, tzinfo=UTC),
            doc_id="lk-0301-000000000000",
            version=1,
            content_hash="b" * 64,
            from_extractor_version=to - 1,
            to_extractor_version=to,
            approval=ApprovalReference.of(decision),
        )

    def test_a_carry_cannot_carry_a_persons_name(self) -> None:
        decision = self._decision()
        payload = self._carried(decision, EXTRACTOR_VERSION).model_dump(mode="json")
        payload["approval"]["decided_by"] = REVIEWER

        with pytest.raises(ValueError, match="decided_by"):
            CarriedRecord.model_validate(payload)

    def test_the_log_maps_an_approval_to_the_last_extractor_it_was_carried_to(
        self, tmp_path: Path
    ) -> None:
        log = DecisionLog(ObservatoryRoot(tmp_path, []))
        decision = self._decision()
        other = decision.model_copy(update={"decided_at": datetime(2026, 9, 1, tzinfo=UTC)})
        carries = (self._carried(decision, OLD + 2), self._carried(decision, OLD + 1))
        for record in (decision, other, *carries):
            log.append(record)

        assert log.carried() == {decision: OLD + 2}
        assert log.carries() == {decision: carries[0]}

    def test_of_two_carries_to_one_extractor_the_last_recorded_stands(self, tmp_path: Path) -> None:
        log = DecisionLog(ObservatoryRoot(tmp_path, []))
        decision = self._decision()
        first = self._carried(decision, EXTRACTOR_VERSION)
        second = first.model_copy(update={"carried_at": datetime(2026, 10, 9, tzinfo=UTC)})
        for record in (decision, first, second):
            log.append(record)

        assert log.carries() == {decision: second}

    def test_a_carry_of_another_text_or_extractor_carries_nothing(self, tmp_path: Path) -> None:
        log = DecisionLog(ObservatoryRoot(tmp_path, []))
        decision = self._decision()
        log.append(decision)
        carried = self._carried(decision, EXTRACTOR_VERSION)
        log.append(carried.model_copy(update={"content_hash": "c" * 64}))
        stale = carried.approval.model_copy(update={"extractor_version": OLD - 1})
        log.append(carried.model_copy(update={"approval": stale}))

        assert log.carried() == {}

    def test_recording_the_same_carry_twice_appends_once(self, tmp_path: Path) -> None:
        log = DecisionLog(ObservatoryRoot(tmp_path, []))
        carried = self._carried(self._decision(), EXTRACTOR_VERSION)
        later = carried.model_copy(update={"carried_at": datetime(2026, 10, 9, tzinfo=UTC)})

        assert log.record_carry(carried) is True
        assert log.record_carry(later) is False
        assert log.records() == (carried,)
