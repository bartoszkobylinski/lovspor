"""The classifier's output as promotion reads it (ADR-0016 4b, slice S8).

The rows mirror the shape of the classifier's first real run (R1.1,
2026-10-03): one JSON object per line with ``url``, ``authority``, ``sha``,
``form``, ``r1``, ``r2`` and the signals the rules read. Every value here is
invented.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lovspor.errors import ClassifierOutputError
from lovspor.promotion.classifier import (
    ClassifiedArtifact,
    ClassifierClass,
    read_classifier_output,
)
from lovspor.promotion.decisions import ArtifactKey

VERSION = "r1.1-2026-10-03"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def row(sha: str = SHA_A, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "url": f"https://eksempel.kommune.invalid/forskrift/{sha[:4]}",
        "authority": "0301",
        "form": "html",
        "selected": 1,
        "sha": sha,
        "nversions": 1,
        "first": "2026-08-20T09:39:32.890658+00:00",
        "last": "2026-08-20T09:39:32.890658+00:00",
        "r1": True,
        "r2": True,
        "len": 5889,
        "title_forskrift": True,
        "self_operative": True,
        "enacted_by": True,
        "hjemmel": True,
        "ikraft": False,
        "n_sections": 22,
        "proposal_head": False,
        "draft": False,
        "case_doc": False,
        "national": False,
        "lovtidend": False,
        "lf_id": False,
        "adopted_title": True,
        "valid_from": False,
        "n_numbered": 0,
        "forskrift_header": False,
        "self_early": False,
    }
    return base | overrides


def write(tmp_path: Path, *rows: dict[str, object], tail: str = "\n") -> Path:
    path = tmp_path / "predictions.jsonl"
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text(body + tail, encoding="utf-8")
    return path


class TestClassifiedArtifact:
    def test_a_row_reads_with_its_key_class_and_true_signals_as_evidence(self) -> None:
        artifact = ClassifiedArtifact.model_validate(row())

        assert artifact.key == ArtifactKey(
            authority_id="0301", sha256=SHA_A, source_url=row()["url"]
        )
        assert artifact.class_name is ClassifierClass.ENACTED_REGULATION
        assert artifact.signals == ("enacted_by", "hjemmel", "self_operative", "title_forskrift")
        assert artifact.n_sections == 22

    def test_evidence_names_the_version_the_class_and_the_signals(self) -> None:
        evidence = ClassifiedArtifact.model_validate(row()).evidence(VERSION)

        assert evidence.classifier_version == VERSION
        assert evidence.class_name == "enacted_regulation"
        assert evidence.evidence == (
            "enacted_by",
            "hjemmel",
            "self_operative",
            "title_forskrift",
            "n_sections=22",
        )

    @pytest.mark.parametrize(
        ("r1", "r2", "expected"),
        [
            (True, True, ClassifierClass.ENACTED_REGULATION),
            (False, True, ClassifierClass.ADOPTED_RULES),
            (False, False, ClassifierClass.OTHER),
        ],
    )
    def test_the_class_follows_r1_then_r2(
        self, r1: bool, r2: bool, expected: ClassifierClass
    ) -> None:
        assert ClassifiedArtifact.model_validate(row(r1=r1, r2=r2)).class_name is expected

    def test_r1_without_r2_is_refused_because_r1_is_inside_r2(self) -> None:
        with pytest.raises(ValueError, match="r1"):
            ClassifiedArtifact.model_validate(row(r1=True, r2=False))

    @pytest.mark.parametrize(
        "bad",
        [
            {"sha": "A" * 64},
            {"authority": "301"},
            {"url": "ftp://eksempel.invalid/x"},
            {"r1": 1},
            {"r1": "true"},
        ],
    )
    def test_a_malformed_field_is_refused(self, bad: dict[str, object]) -> None:
        with pytest.raises(ValueError):
            ClassifiedArtifact.model_validate(row() | bad)

    def test_a_negative_signal_that_is_true_is_evidence_too(self) -> None:
        artifact = ClassifiedArtifact.model_validate(row(r1=False, r2=False, draft=True))

        assert "draft" in artifact.signals


class TestReadClassifierOutput:
    def test_every_row_is_read_with_the_version_and_the_file_hash(self, tmp_path: Path) -> None:
        path = write(tmp_path, row(SHA_A), row(SHA_B, r1=False))

        output = read_classifier_output(path, VERSION)

        assert output.classifier_version == VERSION
        assert len(output.artifacts) == 2
        assert output.source_sha256 != SHA_A
        assert len(output.source_sha256) == 64

    def test_a_line_that_does_not_parse_is_refused_with_its_number(self, tmp_path: Path) -> None:
        path = tmp_path / "predictions.jsonl"
        path.write_text(json.dumps(row()) + "\n{not json\n", encoding="utf-8")

        with pytest.raises(ClassifierOutputError, match="line 2"):
            read_classifier_output(path, VERSION)

    def test_a_row_that_does_not_validate_is_refused_never_skipped(self, tmp_path: Path) -> None:
        path = write(tmp_path, row(SHA_A), row(SHA_B) | {"sha": "short"})

        with pytest.raises(ClassifierOutputError, match="line 2"):
            read_classifier_output(path, VERSION)

    def test_an_incomplete_last_line_is_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, row(SHA_A), tail="")

        with pytest.raises(ClassifierOutputError, match="incomplete"):
            read_classifier_output(path, VERSION)

    def test_a_blank_line_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "predictions.jsonl"
        path.write_text(json.dumps(row()) + "\n\n", encoding="utf-8")

        with pytest.raises(ClassifierOutputError, match="line 2"):
            read_classifier_output(path, VERSION)

    def test_two_rows_disagreeing_on_one_artifact_are_refused(self, tmp_path: Path) -> None:
        path = write(tmp_path, row(SHA_A), row(SHA_A, r1=False))

        with pytest.raises(ClassifierOutputError, match="disagree"):
            read_classifier_output(path, VERSION)

    def test_a_repeated_identical_row_is_read_once(self, tmp_path: Path) -> None:
        path = write(tmp_path, row(SHA_A), row(SHA_A))

        assert len(read_classifier_output(path, VERSION).artifacts) == 1

    def test_bytes_that_are_not_utf8_are_refused(self, tmp_path: Path) -> None:
        path = tmp_path / "predictions.jsonl"
        path.write_bytes(b"\xff\xfe\n")

        with pytest.raises(ClassifierOutputError, match="UTF-8"):
            read_classifier_output(path, VERSION)

    def test_a_missing_file_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ClassifierOutputError, match="cannot read"):
            read_classifier_output(tmp_path / "absent.jsonl", VERSION)

    @pytest.mark.parametrize("version", ["", "   "])
    def test_the_version_must_be_stated(self, tmp_path: Path, version: str) -> None:
        with pytest.raises(ClassifierOutputError, match="classifier version"):
            read_classifier_output(write(tmp_path, row()), version)


class TestCandidates:
    def test_only_enacted_regulations_of_the_authority_in_stable_order(
        self, tmp_path: Path
    ) -> None:
        path = write(
            tmp_path,
            row(SHA_C),
            row(SHA_A),
            row(SHA_B, r1=False),
            row("d" * 64, authority="4601"),
        )

        candidates = read_classifier_output(path, VERSION).candidates("0301")

        assert [c.sha256 for c in candidates] == [SHA_A, SHA_C]

    def test_a_listed_batch_keeps_only_the_listed_artifacts(self, tmp_path: Path) -> None:
        output = read_classifier_output(write(tmp_path, row(SHA_A), row(SHA_C)), VERSION)

        assert [c.sha256 for c in output.candidates("0301", (SHA_C,))] == [SHA_C]

    def test_a_listed_artifact_that_is_not_a_candidate_is_refused(self, tmp_path: Path) -> None:
        output = read_classifier_output(write(tmp_path, row(SHA_A), row(SHA_B, r1=False)), VERSION)

        with pytest.raises(ClassifierOutputError, match=SHA_B):
            output.candidates("0301", (SHA_A, SHA_B))
