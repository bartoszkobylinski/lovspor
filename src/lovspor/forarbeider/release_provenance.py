"""AR1b: the boundary between a source label and verified provenance (#738; §9).

A piece of release evidence names its ``source``, but a label is only a
claim: anyone can build ``LimitsEvidence(status="verified", source=...)``.
This module adds a seal that only the engine's own verifiers attach.

:func:`issue_provenance` returns a copy of a record carrying a
:class:`Provenance`: the kind of evidence, the verifier that produced it and
an HMAC over the kind and the record's canonical digest. The key is drawn
from the operating system when this module is imported, never stored and
never serialized (``seal`` is excluded from every dump), so:

- a record built by hand carries no seal, or an empty one;
- a seal copied onto another record, or kept after the record is changed,
  no longer matches its digest;
- a record that crossed a process boundary (dumped and parsed again) has lost
  its seal and is untrusted, as it should be: AR1b evidence is collected and
  judged in one process. A persisted or transmitted record needs its own
  signature (AR6), not this seal.

Only kinds in :data:`VERIFIERS` can be sealed. Adding a kind there needs the
verifier it names; the inventory, lineage-head anchors and the D10 legal
assessment have none (AR2, AR6/AR8, D10), so no evidence of theirs can ever
be sealed and the gates never trust it, whatever its label says.

The seal proves only that this process's verifier produced the record. It is
no cryptographic proof to anyone else, and it does not make the evidence
sufficient: the gates still apply every other check. The AST test in
``test_forarbeider_release_controls.py`` limits which modules may import
:func:`issue_provenance`.
"""

import hmac
import secrets
from hashlib import sha256
from typing import Final, Literal, Protocol, Self

from pydantic import Field

from lovspor.forarbeider.version_models import Frozen, NonBlank, canonical_text

VerifiedKind = Literal[
    "signer_registry",
    "publisher_identity",
    "policy_approval",
    "manifest_reproduction",
    "publication_limits",
    "release_control",
    "budget_ledger",
]
#: Each kind that can be sealed and the verifier that seals it.
VERIFIERS: Final[dict[VerifiedKind, str]] = {
    "signer_registry": "lovspor.forarbeider.release_sources.registry_of",
    "publisher_identity": "lovspor.forarbeider.release_sources.publisher_of",
    "policy_approval": "lovspor.forarbeider.release_sources.approvals_of",
    "manifest_reproduction": "lovspor.forarbeider.release_sources.reproduction_of",
    "publication_limits": "lovspor.forarbeider.release_limits.verify_limits",
    "release_control": "lovspor.forarbeider.release_control.verify_control",
    "budget_ledger": "lovspor.forarbeider.release_ledger.verify_ledger",
}
_KEY: Final = secrets.token_bytes(32)


class Provenance(Frozen):
    """Which verifier produced a record; the seal never leaves the process."""

    kind: VerifiedKind
    verifier: NonBlank
    seal: str = Field(default="", exclude=True, repr=False)


class _Sealable(Protocol):
    provenance: Provenance | None

    def model_copy(
        self, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> Self: ...


def issue_provenance[R: _Sealable](record: R, kind: str) -> R:
    """``record`` sealed as evidence of ``kind``; only the engine's verifiers call this.

    Raises:
        ValueError: ``kind`` has no verifier in the engine.
    """
    if kind not in VERIFIERS:
        raise ValueError(f"no verifier exists for {kind}")
    verified_kind: VerifiedKind = kind
    provenance = Provenance(
        kind=verified_kind,
        verifier=VERIFIERS[verified_kind],
        seal=_seal(verified_kind, _subject(record)),
    )
    return record.model_copy(update={"provenance": provenance})


def authentic(record: _Sealable, kind: VerifiedKind) -> bool:
    """Whether ``record`` carries this process's seal for ``kind`` over its exact content."""
    provenance = record.provenance
    if provenance is None or provenance.kind != kind or provenance.verifier != VERIFIERS[kind]:
        return False
    return hmac.compare_digest(provenance.seal, _seal(kind, _subject(record)))


def _subject(record: _Sealable) -> str:
    bare = record.model_copy(update={"provenance": None})
    return sha256(canonical_text(bare).encode()).hexdigest()  # type: ignore[arg-type]


def _seal(kind: VerifiedKind, subject: str) -> str:
    return hmac.new(_KEY, f"{kind}\n{subject}".encode(), sha256).hexdigest()
