"""The benchmark publication manifest (ADR-0014 Decision 4, ADR:706).

The builder reads LLHB values from ``benchmarks/llhb/PUBLICATION.json`` and
from the files it names, and nothing else under ``benchmarks/llhb/results/``.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.site.benchmark import (
    PUBLICATION_PATH,
    PublicationManifest,
    fact_id,
    load_publication,
)
from lovspor.site.errors import SiteBuildError

_REPO = Path(__file__).resolve().parents[2]
_REPORT = "benchmarks/llhb/results/reports/pair.json"
_LABEL = {"nb": "post hoc-diagnostisk", "en": "post-hoc diagnostic"}


def _report() -> dict[str, Any]:
    return {
        "cases_scored": 250,
        "scorer_version": "llhb-score-v2",
        "metrics": {
            "rate": {"control": {"numerator": 42.0, "denominator": 215.0, "rate": 42 / 215}},
            "delta": {"maximum": -0.1, "minimum": -0.2},
        },
    }


def _entry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": "rate",
        "source": _REPORT,
        "values": {
            "numerator": {"field": "metrics.rate.control.numerator", "format": "count"},
            "percent": {"field": "metrics.rate.control.rate", "format": "percent"},
            "scorer": {"field": "scorer_version", "format": "text"},
        },
        "label": _LABEL,
        "ruling": "DECISIONS.md #30(a)",
        "wording": {"nb": "Svar", "en": "Answers"},
    }
    entry.update(overrides)
    return entry


def _manifest(*entries: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "benchmark": "LLHB v1",
        "approved_by_role": "project owner",
        "approved_on": "2026-10-07",
        "entries": list(entries) or [_entry()],
    }


def _checkout(tmp_path: Path, manifest: dict[str, Any], report: dict[str, Any]) -> Path:
    (tmp_path / _REPORT).parent.mkdir(parents=True)
    (tmp_path / _REPORT).write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / PUBLICATION_PATH).write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


class TestLoad:
    @pytest.mark.parametrize(
        ("raw", "expected"), [(0, 0.0), (1, 100.0), (0.1225, 12.3), (0.0005, 0.1)]
    )
    def test_percent_endpoints_and_half_up_ties(
        self, tmp_path: Path, raw: int | float, expected: float
    ) -> None:
        """The module contract specifies [0, 1] and decimal half-up rounding."""
        entry = _entry(values={"v": {"field": "rate", "format": "percent"}})
        publication = load_publication(_checkout(tmp_path, _manifest(entry), {"rate": raw}))

        assert publication is not None
        assert publication.facts[0].value == expected
        assert type(publication.facts[0].value) is float

    @pytest.mark.parametrize(
        "raw",
        [
            -(10**1000 + 1),
            -(2**100),
            -1,
            0,
            1,
            2**100,
            2**53 + 1,
            10**1000 + 1,
            42,
            42.0,
            -0.0,
        ],
    )
    def test_a_whole_count_is_kept_exact(self, tmp_path: Path, raw: int | float) -> None:
        """An int count never passes through float (Codex test on PR #586)."""
        entry = _entry(values={"v": {"field": "count", "format": "count"}})
        publication = load_publication(_checkout(tmp_path, _manifest(entry), {"count": raw}))

        assert publication is not None
        assert publication.facts[0].value == int(raw)
        assert type(publication.facts[0].value) is int

    def test_no_manifest_publishes_nothing(self, tmp_path: Path) -> None:
        assert load_publication(tmp_path) is None

    def test_values_are_read_from_the_named_fields_as_code_facts(self, tmp_path: Path) -> None:
        publication = load_publication(_checkout(tmp_path, _manifest(), _report()))

        assert publication is not None
        facts = {source.id: source for source in publication.facts}
        assert facts["llhb.rate.numerator"].value == 42
        assert isinstance(facts["llhb.rate.numerator"].value, int)
        assert facts["llhb.rate.percent"].value == 19.5
        assert facts["llhb.rate.scorer"].value == "llhb-score-v2"
        assert {source.kind for source in facts.values()} == {"code"}
        assert {source.artifact for source in facts.values()} == {_REPORT}
        assert facts["llhb.rate.percent"].field == "metrics.rate.control.rate"

    def test_percent_rounds_half_up_to_one_decimal(self, tmp_path: Path) -> None:
        report = _report()
        report["metrics"]["rate"]["control"]["rate"] = 0.12345
        publication = load_publication(_checkout(tmp_path, _manifest(), report))

        assert publication is not None
        assert {s.id: s.value for s in publication.facts}["llhb.rate.percent"] == 12.3

    def test_every_named_file_and_the_manifest_are_hashed(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path, _manifest(), _report())
        publication = load_publication(checkout)

        assert publication is not None
        assert publication.artifact_hashes() == (
            (
                PUBLICATION_PATH,
                hashlib.sha256((checkout / PUBLICATION_PATH).read_bytes()).hexdigest(),
            ),
            (_REPORT, hashlib.sha256((checkout / _REPORT).read_bytes()).hexdigest()),
        )

    def test_an_unlisted_report_is_never_opened(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path, _manifest(), _report())
        (checkout / "benchmarks/llhb/results/reports/other.json").write_text("not json")

        publication = load_publication(checkout)

        assert publication is not None
        assert [path for path, _ in publication.artifact_hashes()] == [PUBLICATION_PATH, _REPORT]

    def test_the_context_names_wording_label_and_fact_ids_per_language(
        self, tmp_path: Path
    ) -> None:
        publication = load_publication(_checkout(tmp_path, _manifest(), _report()))

        assert publication is not None
        context = publication.context("en")
        assert context["rate"]["wording"] == "Answers"
        assert context["rate"]["label"] == "post-hoc diagnostic"
        assert context["rate"]["ids"]["percent"] == fact_id("rate", "percent")
        assert publication.context("nb")["rate"]["wording"] == "Svar"
        assert publication.context("nb")["rate"]["label"] == _LABEL["nb"]
        assert context["rate"]["ruling"] == "DECISIONS.md #30(a)"


class TestRefusals:
    @pytest.mark.parametrize(
        "raw", [-0.001, 1.001, True, False, "0.5", None, float("nan"), float("inf")]
    )
    def test_percent_requires_a_numeric_rate_in_the_closed_unit_interval(
        self, tmp_path: Path, raw: object
    ) -> None:
        entry = _entry(values={"v": {"field": "rate", "format": "percent"}})
        checkout = _checkout(tmp_path, _manifest(entry), {"rate": raw})

        with pytest.raises(SiteBuildError, match="not a rate"):
            load_publication(checkout)

    @pytest.mark.parametrize("bound", ["above", "below"])
    @pytest.mark.parametrize("raw", [0, True, "0", None])
    def test_requirement_bounds_are_strict_and_require_numbers(
        self, tmp_path: Path, bound: str, raw: object
    ) -> None:
        entry = _entry(requires=[{"field": "guard", bound: 0}])
        checkout = _checkout(tmp_path, _manifest(entry), {**_report(), "guard": raw})

        with pytest.raises(SiteBuildError, match="guard .*no longer supports"):
            load_publication(checkout)

    @pytest.mark.parametrize("raw", [b'{"rate":', b"\xff"])
    def test_malformed_source_json_names_the_source(self, tmp_path: Path, raw: bytes) -> None:
        checkout = _checkout(tmp_path, _manifest(), _report())
        (checkout / _REPORT).write_bytes(raw)

        with pytest.raises(SiteBuildError, match="pair.json is not JSON"):
            load_publication(checkout)

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ({"field": "metrics.rate.missing", "format": "count"}, "metrics.rate.missing"),
            ({"field": "metrics.rate.control.rate", "format": "count"}, "not a whole count"),
            ({"field": "scorer_version", "format": "percent"}, "not a rate"),
            ({"field": "cases_scored", "format": "text"}, "not text"),
            ({"field": "metrics.rate.control.numerator.deeper", "format": "count"}, "deeper"),
        ],
    )
    def test_a_value_that_does_not_match_its_field_fails_the_build(
        self, tmp_path: Path, value: dict[str, str], message: str
    ) -> None:
        checkout = _checkout(tmp_path, _manifest(_entry(values={"v": value})), _report())

        with pytest.raises(SiteBuildError, match=message):
            load_publication(checkout)

    def test_a_requirement_that_no_longer_holds_fails_the_build(self, tmp_path: Path) -> None:
        entry = _entry(requires=[{"field": "metrics.delta.maximum", "above": 0}])
        checkout = _checkout(tmp_path, _manifest(entry), _report())

        with pytest.raises(SiteBuildError, match=r"metrics\.delta\.maximum"):
            load_publication(checkout)

    def test_requirements_that_hold_pass(self, tmp_path: Path) -> None:
        entry = _entry(
            requires=[
                {"field": "metrics.delta.maximum", "below": 0},
                {"field": "metrics.delta.minimum", "above": -0.3},
            ]
        )

        assert load_publication(_checkout(tmp_path, _manifest(entry), _report())) is not None

    def test_a_missing_source_fails_the_build(self, tmp_path: Path) -> None:
        entry = _entry(source="benchmarks/llhb/results/reports/absent.json")
        checkout = _checkout(tmp_path, _manifest(entry), _report())

        with pytest.raises(SiteBuildError, match="absent.json"):
            load_publication(checkout)

    def test_a_malformed_manifest_fails_the_build(self, tmp_path: Path) -> None:
        checkout = _checkout(tmp_path, {"schema_version": "1"}, _report())

        with pytest.raises(SiteBuildError, match="PUBLICATION.json"):
            load_publication(checkout)

    @pytest.mark.parametrize(
        "source",
        [
            "/etc/passwd",
            "benchmarks/llhb/../../secrets.json",
            "src/lovspor/site/build.py",
            "benchmarks/llhb/PUBLICATION.json",
        ],
    )
    def test_a_source_outside_the_benchmark_tree_is_refused(self, source: str) -> None:
        with pytest.raises(ValidationError):
            PublicationManifest.model_validate(_manifest(_entry(source=source)))

    def test_entry_ids_are_unique(self) -> None:
        with pytest.raises(ValidationError, match="twice"):
            PublicationManifest.model_validate(_manifest(_entry(), _entry()))

    def test_a_requirement_names_exactly_one_bound(self) -> None:
        for bounds in ({}, {"below": 0, "above": 0}):
            with pytest.raises(ValidationError):
                PublicationManifest.model_validate(
                    _manifest(_entry(requires=[{"field": "x", **bounds}]))
                )

    def test_the_approval_is_recorded(self) -> None:
        manifest = _manifest()
        del manifest["approved_on"]

        with pytest.raises(ValidationError):
            PublicationManifest.model_validate(manifest)


class TestTheRepositoryManifest:
    """The owner-approved values of 2026-10-07, read from the real reports."""

    def test_reads_the_approved_values(self) -> None:
        publication = load_publication(_REPO)

        assert publication is not None
        assert publication.manifest.approved_by_role == "project owner"
        assert publication.manifest.approved_on == "2026-10-07"
        values = {source.id: source.value for source in publication.facts}
        expected = {
            "pair.cases": 250,
            "model.model": "claude-opus-5",
            "citation_hallucination_rate.control_numerator": 42,
            "citation_hallucination_rate.control_denominator": 215,
            "citation_hallucination_rate.control_percent": 19.5,
            "citation_hallucination_rate.treatment_numerator": 30,
            "citation_hallucination_rate.treatment_denominator": 249,
            "citation_hallucination_rate.treatment_percent": 12.0,
            "citation_accuracy.control_numerator": 128,
            "citation_accuracy.control_denominator": 189,
            "citation_accuracy.control_percent": 67.7,
            "citation_accuracy.treatment_numerator": 431,
            "citation_accuracy.treatment_denominator": 494,
            "citation_accuracy.treatment_percent": 87.2,
            "correct_provision_identification.control_numerator": 8,
            "correct_provision_identification.treatment_numerator": 30,
            "correct_provision_identification.treatment_denominator": 90,
            "false_premise_rejection_rate.control_numerator": 0,
            "false_premise_rejection_rate.treatment_numerator": 8,
            "false_premise_rejection_rate.treatment_denominator": 35,
            "no_invention_rate.control_numerator": 7,
            "no_invention_rate.control_denominator": 7,
            "no_invention_rate.treatment_numerator": 7,
            "no_invention_rate.treatment_denominator": 11,
            "c2_hallucination.control_numerator": 1,
            "c2_hallucination.control_denominator": 28,
            "c2_hallucination.treatment_numerator": 8,
            "c2_hallucination.treatment_denominator": 40,
            "stability.repeats": 5,
        }
        for key, value in expected.items():
            assert values[f"llhb.{key}"] == value, key

    def test_every_source_lives_under_the_benchmark_tree(self) -> None:
        publication = load_publication(_REPO)

        assert publication is not None
        for path, _ in publication.artifact_hashes():
            assert path.startswith("benchmarks/llhb/"), path


@pytest.mark.parametrize("raw", [float("inf"), float("nan"), 4.5, True, "42"])
def test_a_count_that_is_not_a_finite_whole_number_is_refused(tmp_path: Path, raw: object) -> None:
    entry = _entry(values={"v": {"field": "count", "format": "count"}})
    with pytest.raises(SiteBuildError, match="not a whole count"):
        load_publication(_checkout(tmp_path, _manifest(entry), {"count": raw}))


@pytest.mark.parametrize("value_format", ["count", "percent", "text"])
def test_invalid_value_error_names_the_manifest_value(tmp_path: Path, value_format: str) -> None:
    entry = _entry(values={"bad_value": {"field": "broken", "format": value_format}})
    checkout = _checkout(tmp_path, _manifest(entry), {"broken": None})

    with pytest.raises(SiteBuildError) as raised:
        load_publication(checkout)

    assert str(raised.value).startswith("bad_value: None is not ")


@pytest.mark.parametrize("requirement", [False, True])
def test_missing_field_error_names_the_source(tmp_path: Path, requirement: bool) -> None:
    entry = (
        _entry(requires=[{"field": "missing.guard", "above": 0}])
        if requirement
        else _entry(values={"v": {"field": "missing.value", "format": "count"}})
    )
    checkout = _checkout(tmp_path, _manifest(entry), _report())

    with pytest.raises(SiteBuildError) as raised:
        load_publication(checkout)

    field = "missing.guard" if requirement else "missing.value"
    assert str(raised.value) == f"{_REPORT}: field {field!r} is not in the source"
