"""The corpus names the reviewer by role; the name stays in the private log.

Owner decision on PR #517 (2026-10-03): ``lovverk`` is public and its history
is never rewritten (ADR-0003), so the audit record published there carries
``reviewed_by_role`` and no ``decided_by``. The person's name is written only
to ``promotions.jsonl`` in the archive root.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from lovspor.observatory.storage import ENV_CORPUS_ROOT, ENV_OBSERVATORY_ROOT
from lovspor.promotion.decisions import (
    DECISIONS_FILENAME,
    ROLE_REQUIRED,
    ArtifactKey,
    ClassifierEvidence,
    Decision,
    DecisionDocument,
    HumanDecision,
)
from tests.unit.promotion_cli_fixtures import Decision as DecisionFile
from tests.unit.promotion_cli_fixtures import (
    approve,
    make_corpus,
    promote,
    register,
    store,
    tree,
)
from tests.unit.promotion_fixtures import html_page

PLANTED = "Zbigniewa Przybyszewska-Ødegårdsen"
ROLE = "project owner"
NAME_REFUSAL = "carries the reviewer's name; the corpus is given the role, not the name"
KEY = ArtifactKey(authority_id="0301", sha256="a" * 64, source_url="https://x.invalid/f")
AT = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
EVIDENCE = {"classifier_version": "c1", "class_name": "forskrift", "evidence": ["§ 1"]}


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    observatory = tmp_path / "observatory"
    observatory.mkdir()
    monkeypatch.setenv(ENV_OBSERVATORY_ROOT, str(observatory))
    monkeypatch.delenv(ENV_CORPUS_ROOT, raising=False)
    register(observatory)
    return observatory


def _log_text(root: Path) -> str:
    path = root / DECISIONS_FILENAME
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _refused(root: Path, tmp_path: Path, document: DecisionFile) -> str:
    """The refusal line of an approve that must not record anything."""
    result = approve(store(root, html_page()), document.write(tmp_path))
    assert result.exit_code == 1
    assert result.stdout == ""
    assert _log_text(root) == ""
    return result.stderr


def _document(**fields: object) -> dict[str, object]:
    return {
        "decision": "hold",
        "decided_by": PLANTED,
        "reviewer_role": ROLE,
        "reason": "r",
    } | fields


class TestTheNameNeverReachesTheCorpus:
    @pytest.fixture
    def promoted(self, root: Path, tmp_path: Path) -> Path:
        corpus = make_corpus(tmp_path)
        sha256 = store(root, html_page())
        decision = DecisionFile(decided_by=PLANTED, classifier=EVIDENCE)
        assert approve(sha256, decision.write(tmp_path)).exit_code == 0
        assert promote("local", sha256, corpus).exit_code == 0
        return corpus

    def test_no_corpus_file_contains_the_name_or_any_word_of_it(self, promoted: Path) -> None:
        files = tree(promoted)
        written = [path for path in files if path.startswith("lokale-forskrifter/")]

        assert len(written) == 3
        for path, data in files.items():
            text = data.decode("utf-8").casefold()
            for word in ("zbigniewa", "przybyszewska", "ødegårdsen", "decided_by"):
                assert word not in text, path

    def test_the_published_audit_names_the_role(self, promoted: Path) -> None:
        [observations] = (promoted / "lokale-forskrifter" / "0301" / "observations").iterdir()
        [version] = json.loads(observations.read_text("utf-8"))["versions"]

        assert version["promotion"]["reviewed_by_role"] == ROLE
        assert "decided_by" not in version["promotion"]

    def test_the_private_log_holds_the_name_and_the_role(self, root: Path, promoted: Path) -> None:
        decision, outcome = (json.loads(line) for line in _log_text(root).splitlines())

        assert PLANTED in _log_text(root)
        assert (decision["kind"], decision["decided_by"]) == ("decision", PLANTED)
        assert decision["reviewer_role"] == ROLE
        assert outcome["kind"] == "promoted"
        assert outcome["audit"]["reviewed_by_role"] == ROLE
        assert "decided_by" not in outcome["audit"]


class TestTheRoleIsRequired:
    def test_a_decision_without_a_role_is_refused_with_the_rule(
        self, root: Path, tmp_path: Path
    ) -> None:
        stderr = _refused(root, tmp_path, DecisionFile(reviewer_role=None))

        assert stderr.startswith("Refused: the decision document does not validate: ")
        assert f"Value error, {ROLE_REQUIRED} " in stderr

    def test_the_rule_is_spelled_exactly(self) -> None:
        assert ROLE_REQUIRED == (
            "reviewer_role is required: the published audit names the reviewer by role, "
            "never by name"
        )

    @pytest.mark.parametrize("role", ["", "   "])
    def test_a_blank_role_is_refused(self, role: str) -> None:
        with pytest.raises(ValidationError) as refused:
            DecisionDocument.model_validate(_document(reviewer_role=role))

        assert ROLE_REQUIRED not in str(refused.value)
        assert "reviewer_role" in str(refused.value)

    def test_a_document_that_is_not_an_object_is_not_told_the_role_is_missing(self) -> None:
        with pytest.raises(ValidationError) as refused:
            DecisionDocument.model_validate(["reviewer_role"])

        assert ROLE_REQUIRED not in str(refused.value)

    def test_the_role_is_trimmed_before_it_is_recorded(self, root: Path, tmp_path: Path) -> None:
        sha256 = store(root, html_page())

        approve(sha256, DecisionFile(reviewer_role="  project owner ").write(tmp_path))

        assert json.loads(_log_text(root))["reviewer_role"] == ROLE


class TestTheRoleIsNotAPersonOrAMachine:
    @pytest.mark.parametrize(
        ("role", "shown"),
        [("classifier", "classifier"), (" Lovspor ", "Lovspor"), ("SYSTEM", "SYSTEM")],
    )
    def test_a_machine_role_is_refused(
        self, root: Path, tmp_path: Path, role: str, shown: str
    ) -> None:
        stderr = _refused(root, tmp_path, DecisionFile(reviewer_role=role))

        assert f"reviewer_role must name a human role, not {shown!r} (ADR-0016 4c)" in stderr

    @pytest.mark.parametrize(
        "role", ["project owner, ola@eksempel.kommune.invalid", "project owner tlf 12345678"]
    )
    def test_a_role_with_personal_data_is_refused(
        self, root: Path, tmp_path: Path, role: str
    ) -> None:
        stderr = _refused(root, tmp_path, DecisionFile(reviewer_role=role))

        assert "reviewer_role carries personal data; it is published with the audit record" in (
            stderr
        )

    @pytest.mark.parametrize(
        ("field", "document"),
        [
            ("reviewer_role", DecisionFile(reviewer_role="project owner (Gjennomgang)")),
            ("reason", DecisionFile(reason="Lest av KARI.")),
            (
                "classifier.evidence[1]",
                DecisionFile(classifier=EVIDENCE | {"evidence": ["§ 1", "kari"]}),
            ),
            ("classifier.class_name", DecisionFile(classifier=EVIDENCE | {"class_name": "kari"})),
            (
                "classifier.classifier_version",
                DecisionFile(classifier=EVIDENCE | {"classifier_version": "gjennomgang-2"}),
            ),
        ],
    )
    def test_a_published_field_carrying_the_name_is_refused(
        self, root: Path, tmp_path: Path, field: str, document: DecisionFile
    ) -> None:
        stderr = _refused(root, tmp_path, document)

        assert f"Value error, {field} {NAME_REFUSAL} " in stderr

    def test_a_two_letter_word_of_the_name_counts(self) -> None:
        with pytest.raises(ValidationError, match=f"reviewer_role {NAME_REFUSAL}"):
            DecisionDocument.model_validate(_document(decided_by="Bo Ek", reviewer_role="owner bo"))

    def test_a_one_letter_word_of_the_name_does_not(self) -> None:
        document = DecisionDocument.model_validate(
            _document(decided_by="Kari I Gjennomgang", reason="Lest i sin helhet.")
        )

        assert document.reason == "Lest i sin helhet."

    def test_the_log_refuses_a_decision_whose_role_carries_the_name(self) -> None:
        with pytest.raises(ValidationError, match=f"reviewer_role {NAME_REFUSAL}"):
            HumanDecision(
                artifact=KEY,
                decision=Decision.REJECT,
                decided_by="Kari Gjennomgang",
                reviewer_role="kari",
                decided_at=AT,
                reason="r",
            )

    def test_the_log_refuses_a_decision_whose_reason_carries_the_name(self) -> None:
        with pytest.raises(ValidationError, match=f"reason {NAME_REFUSAL}"):
            HumanDecision(
                artifact=KEY,
                decision=Decision.REJECT,
                decided_by="Kari Gjennomgang",
                reviewer_role=ROLE,
                decided_at=AT,
                reason="Gjennomgang",
            )


class TestEveryPublishedFieldIsScreened:
    def test_classifier_evidence_with_personal_data_is_refused(
        self, root: Path, tmp_path: Path
    ) -> None:
        evidence = EVIDENCE | {"evidence": ["§ 1", "ola@eksempel.kommune.invalid"]}

        stderr = _refused(root, tmp_path, DecisionFile(classifier=evidence))

        assert stderr == (
            "Refused: the classifier.evidence[1] carries personal data; "
            "it is published with the audit record\n"
        )

    def test_the_published_fields_are_named_by_their_path(self) -> None:
        document = DecisionDocument.model_validate(_document(classifier=EVIDENCE))

        assert document.published() == {
            "reviewer_role": ROLE,
            "reason": "r",
            "classifier.classifier_version": "c1",
            "classifier.class_name": "forskrift",
            "classifier.evidence[0]": "§ 1",
        }

    def test_without_a_classifier_only_the_role_and_reason_are_published(self) -> None:
        assert DecisionDocument.model_validate(_document()).published() == {
            "reviewer_role": ROLE,
            "reason": "r",
        }

    def test_evidence_is_published_in_order(self) -> None:
        evidence = ClassifierEvidence(classifier_version="c", class_name="k", evidence=("a", "b"))

        assert list(evidence.published().items())[2:] == [
            ("classifier.evidence[0]", "a"),
            ("classifier.evidence[1]", "b"),
        ]
