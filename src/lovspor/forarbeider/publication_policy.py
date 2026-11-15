"""A publication policy definition, apart from any authorization of it (#738, FC7; §3.1, §3.4).

A ``PublicationPolicy`` says which rights classes, resting on which evidence,
a policy *would* clear for one source and its document families, and which
attachment kinds. It is a definition only. Nothing in it states, or can
state, that the owner approved it or that the legal assessment (D10) accepts
it: its ``status`` has the single value ``proposed``, it has no field for an
approval, a signature or a flag, and the evaluator records every decision
under it as ``pending`` authorization. Owner signatures are FC7's signing
half (D6), not this module.

Some limits hold for every policy, whatever its author writes:

- ``unknown`` and ``third_party_work`` can never be cleared (§3.4);
- FC5's ``default_rule`` is not evidence of reuse rights, and ``insufficient``
  or ``conflicting`` evidence is no evidence, so no rule may accept them;
- a privacy hit or an unresolved privacy state is never allowed, and silence
  is accepted only as residual risk that a later authorization must accept;
- an embedded figure or image is never cleared; another attachment only when
  captured;
- the source must be ``source_complete`` and attachment discovery verified.

Policies are named by the SHA-256 of their canonical bytes.
"""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lovspor.forarbeider.attachment_discovery import AttachmentKind
from lovspor.forarbeider.corpus_models import _loaded
from lovspor.forarbeider.document_models import text_digest
from lovspor.forarbeider.errors import ForarbeiderRefusedError, RefusalReason
from lovspor.forarbeider.identity import DocumentType
from lovspor.forarbeider.span_assessment_models import Evidence, RightsClass
from lovspor.forarbeider.version_models import canonical_text

_Text = Annotated[str, Field(pattern=r"\S")]
NEVER_CLEARED = frozenset({RightsClass.UNKNOWN, RightsClass.THIRD_PARTY_WORK})
NOT_EVIDENCE = frozenset({Evidence.DEFAULT_RULE, Evidence.INSUFFICIENT, Evidence.CONFLICTING})


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RightsRule(_Frozen):
    """One rights class the policy would clear, and the FC5 evidence it accepts for it."""

    rights_class: RightsClass
    accepted_evidence: tuple[Evidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _clearable(self) -> Self:
        if self.rights_class in NEVER_CLEARED:
            raise ValueError(f"{self.rights_class.value} can never be cleared by a policy")
        if set(self.accepted_evidence) & NOT_EVIDENCE:
            raise ValueError("default_rule, insufficient and conflicting are not evidence")
        if len(set(self.accepted_evidence)) != len(self.accepted_evidence):
            raise ValueError("evidence is listed twice")
        return self


class AttachmentRule(_Frozen):
    """One attachment kind the policy would clear once captured."""

    kind: AttachmentKind

    @model_validator(mode="after")
    def _clearable(self) -> Self:
        if self.kind == "embedded":
            raise ValueError("an embedded figure or image can never be cleared by a policy")
        return self


class PolicyProvenance(_Frozen):
    """Who drafted the definition and on what basis, as stated: a label, not an identity."""

    drafted_by: _Text
    drafted_on: date
    basis: tuple[_Text, ...] = Field(min_length=1)


class PublicationPolicy(_Frozen):
    """A proposed policy for one source and its families; never an authorization."""

    schema_version: Literal[1] = 1
    record_type: Literal["forarbeider_publication_policy"] = "forarbeider_publication_policy"
    policy_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{2,62}$")
    policy_version: int = Field(ge=1)
    status: Literal["proposed"] = "proposed"
    source_provider: Literal["regjeringen.no"] = "regjeringen.no"
    document_types: tuple[DocumentType, ...] = Field(min_length=1)
    rights_rules: tuple[RightsRule, ...]
    attachment_rules: tuple[AttachmentRule, ...]
    privacy: Literal["no_detector_hit_required"] = "no_detector_hit_required"
    unknown_rights: Literal["unresolved"] = "unresolved"
    conflicting_evidence: Literal["deny"] = "deny"
    requires_source_complete: Literal[True] = True
    requires_verified_attachment_discovery: Literal[True] = True
    provenance: PolicyProvenance

    @model_validator(mode="after")
    def _each_once(self) -> Self:
        if len(set(self.document_types)) != len(self.document_types):
            raise ValueError("a document type is listed twice")
        if len({rule.rights_class for rule in self.rights_rules}) != len(self.rights_rules):
            raise ValueError("a rights class has two rules")
        if len({rule.kind for rule in self.attachment_rules}) != len(self.attachment_rules):
            raise ValueError("an attachment kind has two rules")
        return self


def policy_text(policy: PublicationPolicy) -> str:
    return canonical_text(policy)


def policy_digest(policy: PublicationPolicy) -> str:
    """The policy's name: the SHA-256 of its canonical bytes."""
    return text_digest(policy_text(policy))


def parse_policy(raw: bytes) -> PublicationPolicy:
    """The policy in ``raw``, which must be its canonical bytes and nothing more.

    Raises:
        ForarbeiderRefusedError: ``schema_invalid``: another shape, an extra
            field (an approval, a signature, a flag), or other bytes.
    """
    try:
        policy = PublicationPolicy.model_validate(_loaded(raw))
    except (ValidationError, ForarbeiderRefusedError) as error:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy") from error
    if policy_text(policy).encode() != raw:
        raise ForarbeiderRefusedError(RefusalReason.SCHEMA_INVALID, "policy")
    return policy
