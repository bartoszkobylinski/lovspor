"""FC7: a proposed policy evaluated fail-closed against verified evidence (#738).

Synthetic pages and a synthetic proposed policy only. Every test also checks
that nothing becomes eligible, authorized or published.
"""

import json
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from lovspor.forarbeider.document_extract import SourcePage, extract_document
from lovspor.forarbeider.document_models import Span
from lovspor.forarbeider.errors import ForarbeiderRefusedError
from lovspor.forarbeider.policy_evaluation import (
    MISSING_FOR_AUTHORIZATION,
    EntryDecision,
    PolicyDecision,
    decision_text,
    evaluate_policy,
    parse_decision,
    verify_decision,
)
from lovspor.forarbeider.publication_policy import (
    AttachmentRule,
    PolicyProvenance,
    PublicationPolicy,
    RightsRule,
    parse_policy,
    policy_digest,
    policy_text,
)
from lovspor.forarbeider.span_assessment import assess_document, assessment_text
from lovspor.forarbeider.span_assessment_models import Evidence, RightsClass
from lovspor.forarbeider.version_manifest import build_version_manifest
from tests.unit.forarbeider_version_support import (
    ASSETS,
    PDF,
    capture,
    evidence,
    first_or_any,
    landing,
    pages,
    sha,
    with_body,
)

PDF_URL = ASSETS + "/vedlegg/vedlegg-1.pdf"
PDF_PATH = PDF_URL.removeprefix("https://www.regjeringen.no")
RENAMED = {
    "1 Innledning": "Forslag til lov om a",
    "2 Bakgrunn": "Forslag til lov om b",
    "Tilr&#xE5;ding": "Forslag til lov om c",
    "Tilråding": "Forslag til lov om c",
    "1 Oppdiktet vedlegg": "Forslag til lov om d",
}


def policy(**changes: Any) -> PublicationPolicy:
    fields: dict[str, Any] = {
        "policy_id": "synthetic-policy",
        "policy_version": 1,
        "document_types": ("prop-l",),
        "rights_rules": (
            RightsRule(
                rights_class=RightsClass.OFFICIAL_TEXT,
                accepted_evidence=(Evidence.STRUCTURE, Evidence.STRUCTURE_AND_ATTRIBUTION),
            ),
            RightsRule(
                rights_class=RightsClass.STATUTE_TEXT,
                accepted_evidence=(
                    Evidence.CHAPTER_CONTEXT,
                    Evidence.STRUCTURE_AND_ATTRIBUTION,
                    Evidence.STRUCTURE_AND_CONTEXT,
                ),
            ),
        ),
        "attachment_rules": (),
        "provenance": PolicyProvenance(
            drafted_by="synthetic test", drafted_on=date(2100, 1, 1), basis=("architecture §3.4",)
        ),
    }
    return PublicationPolicy.model_validate({**fields, **changes})


def with_rules(
    *rules: RightsRule, attachments: tuple[AttachmentRule, ...] = ()
) -> PublicationPolicy:
    return policy(rights_rules=(*policy().rights_rules, *rules), attachment_rules=attachments)


def decide(
    given: list[SourcePage] | None = None, chosen: PublicationPolicy | None = None, **kwargs: Any
) -> PolicyDecision:
    return evaluate_policy(evidence(given, **kwargs), chosen or policy())


def chapter_two(body: str, chosen: PublicationPolicy | None = None) -> PolicyDecision:
    return decide([*pages(1, 3, 4), with_body(2, body)], chosen)


def entry(decision: PolicyDecision, given: list[SourcePage], text: str) -> EntryDecision:
    """The decision for the span of chapter 2 whose (invented) text is ``text``."""
    ids = [
        e.id
        for p in extract_document(given).parts
        for e in p.entries
        if isinstance(e, Span) and e.text == text
    ]
    (found,) = [item for item in decision.entries if item.entry_id in ids]
    return found


def two(body: str, text: str, chosen: PublicationPolicy | None = None) -> EntryDecision:
    given = [*pages(1, 3, 4), with_body(2, body)]
    return entry(decide(given, chosen), given, text)


def never_eligible(decision: PolicyDecision) -> None:
    assert decision.document.eligible is False
    assert decision.document.authorization == "pending_no_verifiable_mechanism"
    assert decision.document.missing_for_authorization == MISSING_FOR_AUTHORIZATION
    assert decision.published is False
    assert decision.policy_status == "proposed"


def renamed(page: SourcePage) -> SourcePage:
    text = page.payload.decode()
    for old, new in RENAMED.items():
        text = text.replace(old, new)
    return page._replace(payload=text.encode())


# --- entries -----------------------------------------------------------------------


def test_official_text_on_the_default_rule_is_not_evidence_and_stays_unresolved() -> None:
    item = two(
        "<p>Departementet viser til gjeldende rett.</p>", "Departementet viser til gjeldende rett."
    )
    assert (item.decision, item.rule) == ("unresolved", "rights:official_text")
    assert "default_rule_not_evidence" in item.reasons
    never_eligible(chapter_two("<p>x.</p>"))


def test_a_heading_with_structural_evidence_passes_the_rule_with_residual_privacy_risk() -> None:
    item = two("<p>Tekst.</p>", "2 Bakgrunn")
    assert item.decision == "allow"
    assert item.reasons == ("rule_matched:structure", "privacy_residual_risk_unaccepted")


def test_statute_text_on_chapter_context_passes_when_the_policy_accepts_that_evidence() -> None:
    given = [renamed(item) for item in pages()]
    decision = decide(given, landing_page=renamed(landing()))
    statute = [item for item in decision.entries if item.rule == "rights:statute_text"]
    assert statute and {item.decision for item in statute} == {"allow"}
    never_eligible(decision)


@pytest.mark.parametrize(
    ("intro", "rule"),
    [
        ("Advokatforeningen skriver:", "rights:third_party_quote"),
        ("Oslo kommune uttaler:", "rights:public_statement"),
    ],
)
def test_quotations_need_explicit_policy_coverage(intro: str, rule: str) -> None:
    body = f"<p>{intro}</p><blockquote><p>Sitert tekst her.</p></blockquote>"
    uncovered = two(body, "Sitert tekst her.")
    assert (uncovered.decision, uncovered.rule, uncovered.reasons[0]) == (
        "unresolved",
        rule,
        "class_not_covered",
    )
    covering = with_rules(
        RightsRule(
            rights_class=RightsClass(rule.split(":")[1]),
            accepted_evidence=(Evidence.STRUCTURE_AND_ATTRIBUTION,),
        )
    )
    covered = two(body, "Sitert tekst her.", covering)
    assert covered.decision == "allow"
    assert covered.evidence_entries


def test_attributed_statements_need_coverage_and_the_accepted_evidence() -> None:
    body = "<h2>Høringsinstansenes syn</h2><p>Advokatforeningen mener forslaget går for langt.</p>"
    text = "Advokatforeningen mener forslaget går for langt."
    assert two(body, text).reasons[0] == "class_not_covered"
    wrong = with_rules(
        RightsRule(
            rights_class=RightsClass.ATTRIBUTED_STATEMENT, accepted_evidence=(Evidence.STRUCTURE,)
        )
    )
    assert two(body, text, wrong).reasons[0] == "evidence_not_accepted:stated_attribution"


def test_unknown_rights_are_unresolved_and_conflicting_evidence_is_denied() -> None:
    unknown = two("<p>Det vises til.</p><blockquote><p>Løst sitat.</p></blockquote>", "Løst sitat.")
    assert (unknown.decision, unknown.reasons[0]) == ("unresolved", "rights_unknown")
    body = (
        "<p>Advokatforeningen foreslår at § 3 skal lyde:</p>"
        "<blockquote><p>Foreslått ordlyd.</p></blockquote>"
    )
    conflicting = two(body, "Foreslått ordlyd.")
    assert (conflicting.decision, conflicting.rule) == ("deny", "conflicting_evidence")


def test_a_third_party_work_is_denied_whatever_the_policy() -> None:
    item = two("<p>Figur © Oppdiktet Forlag.</p>", "Figur © Oppdiktet Forlag.")
    assert (item.decision, item.reasons[0]) == ("deny", "third_party_work_never_cleared")


def test_a_privacy_hit_is_never_allowed_even_where_the_rights_rule_passes() -> None:
    given = [renamed(item) for item in pages(1, 3, 4)]
    given.append(renamed(with_body(2, "<p>Kontakt ola@example.invalid om saken.</p>")))
    decision = decide(given, landing_page=renamed(landing()))
    item = entry(decision, given, "Kontakt ola@example.invalid om saken.")
    assert item.decision == "unresolved"
    assert item.reasons == ("rule_matched:chapter_context", "privacy_review_required")
    never_eligible(decision)


def test_gaps_stay_visible_and_unresolved() -> None:
    decision = chapter_two('<p>Synlig <span hidden="">skjult</span> tekst.</p>')
    gaps = [item for item in decision.entries if item.rule == "extraction_gap"]
    assert [item.reasons[0] for item in gaps] == ["extraction_gap:hidden_content"]
    assert {item.decision for item in gaps} == {"unresolved"}
    documents = decide(pages(1, 2, 3))
    assert any(item.part_id is None and item.decision == "unresolved" for item in documents.entries)


# --- attachments -------------------------------------------------------------------


def _linked() -> list[SourcePage]:
    return [*pages(1, 3, 4), with_body(2, f'<p>Se <a href="{PDF_PATH}">vedlegget</a>.</p>')]


def test_attachments_are_decided_apart_and_fail_closed() -> None:
    (missing,) = decide(_linked()).attachments
    assert (missing.decision, missing.reasons) == (
        "deny",
        ("attachment_not_captured", "attachment_kind_not_covered"),
    )
    caught = (capture(PDF_URL, PDF),)
    (uncovered,) = decide(_linked(), captures=caught).attachments
    assert (uncovered.decision, uncovered.reasons) == (
        "unresolved",
        ("attachment_kind_not_covered",),
    )
    covering = with_rules(attachments=(AttachmentRule(kind="attachment"),))
    (covered,) = decide(_linked(), covering, captures=caught).attachments
    assert (covered.decision, covered.raw_sha256) == ("allow", sha(PDF))


def test_an_embedded_figure_is_denied() -> None:
    decision = chapter_two('<p>Se <img src="/bilde/figur.png"> her.</p>')
    (figure,) = decision.attachments
    assert figure.decision == "deny"
    assert "third_party_work_never_cleared" in figure.reasons


# --- document ----------------------------------------------------------------------


def test_every_entry_passing_still_leaves_the_document_ineligible() -> None:
    given = [renamed(item) for item in pages()]
    decision = decide(given, landing_page=renamed(landing()))
    assert {item.decision for item in decision.entries} == {"allow"}
    assert decision.document.rules_satisfied is True
    assert decision.document.blockers == ()
    never_eligible(decision)


def test_an_incomplete_or_unknown_source_blocks_the_document() -> None:
    incomplete = decide(pages(1, 2, 3))
    assert "source_state:source_incomplete" in incomplete.document.blockers
    unknown = decide(landing_page=None)
    assert {"source_state:unknown", "attachment_discovery_unverified"} <= set(
        unknown.document.blockers
    )
    for decision in (incomplete, unknown):
        assert decision.document.rules_satisfied is False
        never_eligible(decision)


def test_the_document_counts_every_blocking_entry() -> None:
    decision = chapter_two("<p>Departementet viser til.</p><p>Figur © Forlag.</p>")
    blockers = dict(item.split(":") for item in decision.document.blockers)
    assert int(blockers["entries_deny"]) == sum(i.decision == "deny" for i in decision.entries)
    assert int(blockers["entries_unresolved"]) == sum(
        i.decision == "unresolved" for i in decision.entries
    )


def test_a_policy_for_other_document_types_resolves_nothing() -> None:
    decision = decide(chosen=policy(document_types=("prop-ls",)))
    assert {item.decision for item in decision.entries} == {"unresolved"}
    assert "policy_not_applicable" in decision.document.blockers
    never_eligible(decision)


# --- policy forgery ----------------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"status": "approved"},
        {"authorized": True},
        {"owner_approved": True},
        {"legal_assessment": "accepted"},
        {"signature": "ssh-ed25519 AAAA"},
    ],
)
def test_no_json_field_can_state_an_approval(change: dict[str, Any]) -> None:
    payload = {**json.loads(policy_text(policy())), **change}
    raw = (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        parse_policy(raw)


@pytest.mark.parametrize(
    ("rights_class", "accepted"),
    [
        (RightsClass.UNKNOWN, (Evidence.STRUCTURE,)),
        (RightsClass.THIRD_PARTY_WORK, (Evidence.TEXTUAL_MARKER,)),
        (RightsClass.OFFICIAL_TEXT, (Evidence.DEFAULT_RULE,)),
        (RightsClass.OFFICIAL_TEXT, (Evidence.INSUFFICIENT,)),
        (RightsClass.STATUTE_TEXT, (Evidence.CONFLICTING,)),
    ],
)
def test_a_policy_cannot_clear_what_is_never_clearable(
    rights_class: RightsClass, accepted: tuple[Evidence, ...]
) -> None:
    with pytest.raises(ValidationError):
        RightsRule(rights_class=rights_class, accepted_evidence=accepted)


def test_a_policy_cannot_clear_embedded_figures_or_loosen_fixed_limits() -> None:
    with pytest.raises(ValidationError):
        AttachmentRule(kind="embedded")
    for field, value in (
        ("privacy", "allow_with_review"),
        ("requires_source_complete", False),
        ("unknown_rights", "allow"),
        ("conflicting_evidence", "allow"),
        ("requires_verified_attachment_discovery", False),
    ):
        with pytest.raises(ValidationError):
            policy(**{field: value})


def test_parse_policy_reads_its_own_bytes_and_names_it_by_digest() -> None:
    raw = policy_text(policy()).encode()
    assert parse_policy(raw) == policy()
    assert policy_digest(policy()) == sha(raw)


# --- integrity ---------------------------------------------------------------------


def test_the_decision_binds_version_assessment_pages_and_policy() -> None:
    decision = decide()
    manifest = build_version_manifest(evidence())
    assessment = assess_document(evidence())
    assert decision.version_id == manifest.version_id
    assert decision.assessment_sha256 == sha(assessment_text(assessment).encode())
    assert decision.source_sha256s == assessment.source_sha256s
    assert (decision.policy_id, decision.policy_version, decision.policy_sha256) == (
        "synthetic-policy",
        1,
        policy_digest(policy()),
    )


def test_the_same_evidence_in_any_order_gives_the_same_bytes() -> None:
    given = _linked()
    assert decision_text(decide(given)) == decision_text(decide(list(reversed(given))))


def test_verify_reproduces_and_refuses_another_policy_or_source() -> None:
    raw = decision_text(decide()).encode()
    assert verify_decision(raw, evidence(), policy()).document.eligible is False
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        verify_decision(raw, evidence(), policy(policy_version=2))
    changed = [*pages(1, 3, 4), with_body(2, "<p>Endret.</p>")]
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        verify_decision(raw, evidence(changed), policy())


def _forge(change: Any) -> bytes:
    payload = json.loads(decision_text(decide()))
    change(payload)
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def _allow_everything(payload: dict[str, Any]) -> None:
    for item in payload["entries"]:
        item["decision"] = "allow"
    payload["document"].update(rules_satisfied=True, blockers=[])


def _swap_assessment(payload: dict[str, Any]) -> None:
    payload["assessment_sha256"] = "0" * 64


def test_a_hand_edited_decision_parses_but_never_verifies() -> None:
    for change in (_allow_everything, _swap_assessment):
        raw = _forge(change)
        assert parse_decision(raw).document.eligible is False
        with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
            verify_decision(raw, evidence(), policy())


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("document", "eligible"), True),
        (("document", "authorization"), "verified"),
        (("published",), True),
        (("policy_status",), "accepted"),
    ],
)
def test_a_decision_record_cannot_claim_eligibility_or_authorization(
    path: tuple[str, ...], value: object
) -> None:
    def change(payload: dict[str, Any]) -> None:
        target = payload
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value

    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        parse_decision(_forge(change))


def test_reused_fc6_and_fc5_records_are_untouched() -> None:
    assert decide().source_state.value == build_version_manifest(evidence()).source.state.value
    assert first_or_any(1).payload == pages(1)[0].payload


@pytest.mark.parametrize(
    ("payloads", "reason"),
    [
        ((), "attachment_not_captured"),
        ((b"not a supported attachment",), "attachment_unsupported"),
        ((PDF, PDF + b"changed"), "attachment_capture_conflicting"),
    ],
)
def test_attachment_coverage_cannot_override_unsafe_capture_state(
    payloads: tuple[bytes, ...], reason: str
) -> None:
    covering = with_rules(attachments=(AttachmentRule(kind="attachment"),))
    decision = decide(
        _linked(), covering, captures=tuple(capture(PDF_URL, raw) for raw in payloads)
    )
    (attachment,) = decision.attachments
    assert (attachment.decision, attachment.rule) == ("deny", "attachment:attachment")
    assert reason in attachment.reasons
    assert "attachments_deny:1" in decision.document.blockers
    assert decision.document.rules_satisfied is False
    never_eligible(decision)


@pytest.mark.parametrize("field", ["document_types", "rights_rules", "attachment_rules"])
def test_policy_rejects_duplicate_scope_and_rules(field: str) -> None:
    chosen = with_rules(attachments=(AttachmentRule(kind="attachment"),))
    values = getattr(chosen, field)
    with pytest.raises(ValidationError, match="twice|two rules"):
        policy(**{field: (*values, values[0])})


def test_rights_rule_rejects_duplicate_accepted_evidence() -> None:
    with pytest.raises(ValidationError, match="listed twice"):
        RightsRule(
            rights_class=RightsClass.OFFICIAL_TEXT,
            accepted_evidence=(Evidence.STRUCTURE, Evidence.STRUCTURE),
        )


@pytest.mark.parametrize("record", ["policy", "decision"])
@pytest.mark.parametrize("encoding", ["compact", "crlf", "missing_newline", "invalid_utf8"])
def test_record_parsers_require_exact_canonical_bytes(record: str, encoding: str) -> None:
    raw = (policy_text(policy()) if record == "policy" else decision_text(decide())).encode()
    variants = {
        "compact": json.dumps(json.loads(raw), separators=(",", ":")).encode(),
        "crlf": raw.replace(b"\n", b"\r\n"),
        "missing_newline": raw.rstrip(b"\n"),
        "invalid_utf8": b"\xff" + raw,
    }
    parser = parse_policy if record == "policy" else parse_decision
    with pytest.raises(ForarbeiderRefusedError, match="schema_invalid"):
        parser(variants[encoding])
