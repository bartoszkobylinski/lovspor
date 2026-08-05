"""Deterministic candidate-case validator for LLHB (C1-C8).

Validates a proposed benchmark case in two layers:

1. the committed JSON Schema (``benchmarks/llhb/schema/case.schema.json``)
   via ``lovspor.llhb.schema``;
2. category-specific deterministic checks against a corpus reader —
   the same canonical semantics the MCP tools use.

Everything fails closed and reports typed issues; the validator never
repairs a case. C8 is deliberately limited: Stage 2 cannot prove an
arbitrary "not in corpus" proposition, so C8 gets structural checks
plus a mandatory-manual-review warning until ``spot_checked`` is true
(METHODOLOGY.md accepts exactly this limitation).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from lovspor.llhb.corpus_pin import CorpusPin
from lovspor.llhb.quotes import QuoteRef, QuoteStatus, materialize_quote
from lovspor.llhb.schema import validate_case as validate_against_schema
from lovspor.mcp import CorpusAmbiguousSectionError, CorpusNotFoundError, CorpusReader

_PROVISION_CAP = 2  # FREEZE.md §2.3: max cases per category sharing one provision


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class CaseIssue(BaseModel):
    case_id: str | None
    code: str
    message: str
    severity: IssueSeverity = IssueSeverity.ERROR


class CandidateValidator:
    """Validates candidate cases against schema + one pinned corpus state."""

    def __init__(
        self,
        reader: CorpusReader,
        schema: dict[str, Any],
        pin: CorpusPin | None = None,
    ) -> None:
        self._reader = reader
        self._schema = schema
        self._pin = pin

    def validate_case(self, case: dict[str, Any]) -> list[CaseIssue]:
        case_id = case.get("case_id")
        issues = [
            CaseIssue(case_id=case_id, code="schema", message=message)
            for message in validate_against_schema(case, self._schema)
        ]
        if issues:
            return issues
        issues.extend(self._check_pin(case))
        checker = getattr(self, f"_check_{str(case['category']).lower()}")
        issues.extend(checker(case))
        return issues

    def validate_dataset(self, cases: list[dict[str, Any]]) -> list[CaseIssue]:
        issues: list[CaseIssue] = []
        for case in cases:
            issues.extend(self.validate_case(case))
        issues.extend(_duplicate_ids(cases))
        issues.extend(_provision_cap(cases))
        return issues

    # -- shared deterministic checks -------------------------------------

    def _check_pin(self, case: dict[str, Any]) -> list[CaseIssue]:
        if self._pin is None:
            return []
        pinned = str(case["corpus_pin"]["lovverk_commit"])
        if pinned == self._pin.lovverk_commit:
            return []
        return [
            CaseIssue(
                case_id=case["case_id"],
                code="corpus-pin-mismatch",
                message=(
                    f"case pins lovverk {pinned} but the validator corpus is at "
                    f"{self._pin.lovverk_commit}; validate against the pinned state"
                ),
            )
        ]

    def _provision_exists(
        self,
        case: dict[str, Any],
        slug: str,
        section_id: str,
        occurrence: int | None,
    ) -> list[CaseIssue]:
        try:
            self._reader.get_section(slug, section_id, occurrence)
        except CorpusAmbiguousSectionError as exc:
            return [_issue(case, "provision-ambiguous", str(exc))]
        except CorpusNotFoundError as exc:
            return [_issue(case, "provision-missing", str(exc))]
        return []

    def _provision_absent(
        self, case: dict[str, Any], slug: str, section_id: str
    ) -> list[CaseIssue]:
        """The trap pair must be provably absent — ambiguity is NOT absence."""
        try:
            self._reader.get_section(slug, section_id)
        except CorpusAmbiguousSectionError as exc:
            return [_issue(case, "trap-section-ambiguous-not-nonexistent", str(exc))]
        except CorpusNotFoundError:
            return []
        return [
            _issue(
                case,
                "trap-section-exists",
                f"claimed {slug} § {section_id} exists; the trap is not a trap",
            )
        ]

    def _act_current(self, case: dict[str, Any], slug: str) -> list[CaseIssue]:
        verdict = self._reader.validate_citation(slug)
        if verdict["valid"]:
            return []
        return [_issue(case, "act-unknown", f"act slug {slug!r} not current in corpus")]

    def _expected_exists(self, case: dict[str, Any]) -> list[CaseIssue]:
        return self._provision_exists(
            case,
            str(case["expected_act_slug"]),
            str(case["expected_section_id"]),
            case.get("expected_occurrence"),
        )

    # -- category checks --------------------------------------------------

    def _check_c1(self, case: dict[str, Any]) -> list[CaseIssue]:
        return self._expected_exists(case)

    def _check_c2(self, case: dict[str, Any]) -> list[CaseIssue]:
        issues = self._expected_exists(case)
        question = str(case["question"]).casefold()
        if str(case["expected_act_slug"]).casefold() in question or "§" in question:
            issues.append(
                _issue(
                    case,
                    "c2-question-leaks-target",
                    "semantic-discovery question must not name the act slug or a §",
                )
            )
        return issues

    def _check_c3(self, case: dict[str, Any]) -> list[CaseIssue]:
        slug = str(case["claimed_act_slug"])
        issues = self._act_current(case, slug)
        if not issues:
            issues = self._provision_absent(case, slug, str(case["claimed_section_id"]))
        return issues

    def _check_c4(self, case: dict[str, Any]) -> list[CaseIssue]:
        issues = self._expected_exists(case)
        issues.extend(self._check_claimed_trap(case))
        claimed = (case["claimed_act_slug"], case["claimed_section_id"])
        expected = (case["expected_act_slug"], case["expected_section_id"])
        if claimed == expected:
            issues.append(
                _issue(case, "trap-equals-expected", "claimed pair equals the ground truth")
            )
        return issues

    def _check_claimed_trap(self, case: dict[str, Any]) -> list[CaseIssue]:
        slug = str(case["claimed_act_slug"])
        section_id = str(case["claimed_section_id"])
        if case["citation_exists"]:
            issues = self._act_current(case, slug)
            return issues or self._provision_exists(case, slug, section_id, None)
        issues = self._act_current(case, slug)
        return issues or self._provision_absent(case, slug, section_id)

    def _check_c5(self, case: dict[str, Any]) -> list[CaseIssue]:
        slug = str(case["claimed_act_slug"] or case["expected_act_slug"])
        section_id = str(case["claimed_section_id"] or case["expected_section_id"])
        if _is_tombstone(self._reader, slug):
            return []
        try:
            self._reader.get_section(slug, section_id)
        except CorpusAmbiguousSectionError:
            return []
        except CorpusNotFoundError as exc:
            return [_issue(case, "provision-missing", str(exc))]
        return [
            _issue(
                case,
                "not-genuinely-ambiguous",
                f"{slug} § {section_id} resolves uniquely and {slug!r} is not a tombstone",
            )
        ]

    def _check_c6(self, case: dict[str, Any]) -> list[CaseIssue]:
        issues = self._expected_exists(case)
        if case.get("claimed_act_slug") is not None:
            issues.extend(self._check_claimed_trap(case))
        return issues

    def _check_c7(self, case: dict[str, Any]) -> list[CaseIssue]:
        if case["expected_behaviour"] == "verify_quote":
            return self._check_true_quote(case)
        return self._check_fabricated_quote(case)

    def _check_true_quote(self, case: dict[str, Any]) -> list[CaseIssue]:
        ref = QuoteRef.model_validate(case["quote_ref"])
        result = materialize_quote(self._reader, ref)
        if result.status is not QuoteStatus.OK or result.text is None:
            return [_issue(case, f"quote-ref-{result.status}", result.reason or "")]
        verdict = self._reader.verify_quote(ref.slug, ref.section_id, result.text, ref.occurrence)
        if verdict["verified"]:
            return []
        return [_issue(case, "quote-not-verifiable", str(verdict["reason"]))]

    def _check_fabricated_quote(self, case: dict[str, Any]) -> list[CaseIssue]:
        slug = case.get("claimed_act_slug") or case.get("expected_act_slug")
        section_id = case.get("claimed_section_id") or case.get("expected_section_id")
        if slug is None or section_id is None:
            return [
                _issue(
                    case,
                    "deny-quote-needs-target",
                    "a deny_quote case must name the provision the quote purports to be from",
                )
            ]
        issues = self._provision_exists(case, str(slug), str(section_id), None)
        slug, section_id = str(slug), str(section_id)
        if issues:
            return issues
        verdict = self._reader.verify_quote(slug, section_id, str(case["fabricated_quote_text"]))
        if verdict["verified"]:
            return [
                _issue(
                    case,
                    "fabricated-quote-actually-verifies",
                    f"the 'fabricated' quote is verbatim text of {slug} § {section_id}",
                )
            ]
        return []

    def _check_c8(self, case: dict[str, Any]) -> list[CaseIssue]:
        issues = [
            _issue(case, "c8-unexpected-citation-field", f"{field} must be null for C8")
            for field in ("expected_act_slug", "expected_section_id", "claimed_act_slug")
            if case.get(field) is not None
        ]
        if not case["validation"].get("spot_checked", False):
            issues.append(
                CaseIssue(
                    case_id=case["case_id"],
                    code="c8-requires-manual-review",
                    message=(
                        "out-of-corpus ground truth cannot be proven deterministically; "
                        "C8 needs 100% manual review (spot_checked=true) before freeze"
                    ),
                    severity=IssueSeverity.WARNING,
                )
            )
        return issues


def _issue(case: dict[str, Any], code: str, message: str) -> CaseIssue:
    return CaseIssue(case_id=case.get("case_id"), code=code, message=message)


def _is_tombstone(reader: CorpusReader, slug: str) -> bool:
    return any(
        record.slug == slug and record.status == "removed"
        for record in reader.manifest.documents.values()
    )


def _duplicate_ids(cases: list[dict[str, Any]]) -> list[CaseIssue]:
    ids = [str(case.get("case_id")) for case in cases]
    return [
        CaseIssue(
            case_id=case_id,
            code="duplicate-case-id",
            message=f"case_id {case_id!r} appears {ids.count(case_id)} times",
        )
        for case_id in sorted({i for i in ids if ids.count(i) > 1})
    ]


def _provision_cap(cases: list[dict[str, Any]]) -> list[CaseIssue]:
    counts: dict[tuple[str, str, str], list[str]] = {}
    for case in cases:
        slug, section = case.get("expected_act_slug"), case.get("expected_section_id")
        if slug is None or section is None:
            continue
        key = (str(case.get("category")), str(slug), str(section))
        counts.setdefault(key, []).append(str(case.get("case_id")))
    return [
        CaseIssue(
            case_id=None,
            code="provision-cap-exceeded",
            message=(
                f"category {key[0]} has {len(ids)} cases on {key[1]} § {key[2]} "
                f"(cap {_PROVISION_CAP}): {sorted(ids)}"
            ),
        )
        for key, ids in sorted(counts.items())
        if len(ids) > _PROVISION_CAP
    ]
