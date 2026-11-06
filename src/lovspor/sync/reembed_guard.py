"""ADR-0006 §7 mass-re-embed guard: refuse a large paid workload before the first call.

The backfill migration and the change path (#790) both measure their whole
workload, documents and tokens, before any provider call, and hand it here.
A tripped guard raises :class:`MassReembedError`, so no sidecar is written and
nothing is spent. The token count is the production tokenizer's count of the
inputs to be sent: an estimate of the request, not a bill.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass

from lovspor.errors import MassReembedError, ParseError, RenderError
from lovspor.settings import Settings
from lovspor.storage.manifest import Manifest, ManifestRecord
from lovspor.temporal_attestation import AttestationError

REEMBED_GUARD_MIN_DOCS = 100
"""Corpora below this size skip the guard's FRACTION dimension only.

A tiny corpus backfilling 100% of itself is the ordinary keyless-then-
keyed bootstrap, not transformation drift; the token-workload dimension
still applies at every size, so real spend is never waved through.
"""

OVERRIDE_HINT = (
    "re-run with 'lovspor sync --allow-mass-reembed' or raise "
    "LOVSPOR_REEMBED_GUARD_MAX_FRACTION / LOVSPOR_REEMBED_GUARD_MAX_TOKENS."
)


@dataclass(frozen=True)
class ReembedScope:
    """What a guarded run would embed, and how its refusal reads."""

    documents: int
    tokens: int
    noun: str
    title: str
    remedy: str


@dataclass(frozen=True)
class BackfillItem:
    """One document the input-identity/staleness selection chose for repair."""

    doc_id: str
    record: ManifestRecord
    rendered: str
    input_tokens: int


def guard_mass_reembed(
    selection: list[BackfillItem],
    prior: Manifest,
    settings: Settings,
    allow: bool,
) -> None:
    """ADR-0006 §7: fail closed on an unexpectedly large repair scope."""
    scope = ReembedScope(
        documents=len(selection),
        tokens=sum(item.input_tokens for item in selection),
        noun="document(s) selected for re-embedding",
        title="embedding-input repair scope",
        remedy="If this repair is intended (annotation-less corpus, deliberate "
        f"pipeline change), {OVERRIDE_HINT}",
    )
    enforce_reembed_guard(scope, prior, settings, allow)


def enforce_reembed_guard(
    scope: ReembedScope,
    prior: Manifest,
    settings: Settings,
    allow: bool,
) -> None:
    """Refuse ``scope`` when it is larger than the guard allows.

    Both dimensions are computed deterministically from the scope alone —
    no provider is consulted. ``allow`` is the explicit operator override
    (``lovspor sync --allow-mass-reembed``); it never comes from the
    environment and the scheduled workflow never passes it, so a tripped
    guard fails the job before a cent of spend. An empty scope has nothing
    to authorize or refuse, and says nothing.
    """
    if not scope.documents:
        return
    current_total = sum(1 for r in prior.documents.values() if r.status == "current")
    fraction = scope.documents / max(current_total, 1)
    summary = (
        f"{scope.documents} {scope.noun} "
        f"({fraction:.1%} of {current_total} current documents), "
        f"~{scope.tokens:,} input tokens"
    )
    if allow:
        print(f"mass-reembed override active: {summary}", file=sys.stderr, flush=True)
        return
    # The fraction dimension only means "blast radius" on a corpus big enough
    # for a fraction to be meaningful — on a bootstrap or test corpus of a
    # handful of documents, 100% selection is the NORMAL keyless->keyed
    # lifecycle, not drift. Same floor pattern as the mass-removal guard.
    # The token dimension applies unconditionally: spend is spend.
    fired = _thresholds_exceeded(scope, fraction, current_total >= REEMBED_GUARD_MIN_DOCS, settings)
    if fired:
        raise MassReembedError(
            f"{scope.title} exceeds the mass-re-embed guard: {summary}. "
            f"Threshold exceeded: {fired}. No provider call was made and no sidecar was "
            f"written. {scope.remedy}",
        )


def _thresholds_exceeded(
    scope: ReembedScope, fraction: float, fraction_applies: bool, settings: Settings
) -> str:
    """The thresholds ``scope`` exceeds, joined for the refusal; empty when none."""
    hits = []
    if fraction_applies and fraction > settings.reembed_guard_max_fraction:
        hits.append(f"document fraction {fraction:.1%} > {settings.reembed_guard_max_fraction:.1%}")
    if scope.tokens > settings.reembed_guard_max_tokens:
        hits.append(f"token workload {scope.tokens:,} > {settings.reembed_guard_max_tokens:,}")
    return " and ".join(hits)


def change_scope(documents: int, tokens: int) -> ReembedScope:
    """The change path's workload (#790), with the refusal naming its likely cause."""
    return ReembedScope(
        documents=documents,
        tokens=tokens,
        noun="changed document(s) to re-embed",
        title="the change-driven re-embed",
        remedy="So many changed documents usually means the upstream format or the "
        "renderer changed, not the law (#679: an ELI row in every header). Check the "
        f"hashes before paying; if the re-embed is intended, {OVERRIDE_HINT}",
    )


def rendered_or_refused(
    render: Callable[[], str], doc_id: str, xml_hash: str, prior: ManifestRecord | None
) -> str | None:
    """``render()``, or None when it cannot render and the write loop would skip it.

    A document that changed upstream (its ``prior`` has another ``xml_hash``)
    and cannot render keeps that prior record, which the ADR-0012 attestation
    refuses after the commit and before the push, so every embedding of that
    run is paid for and lost. Refusing here, before the first provider call,
    costs nothing (#804).

    Raises:
        AttestationError: a changed document cannot render.
    """
    try:
        return render()
    except (RenderError, ParseError) as exc:
        if prior is not None and prior.xml_hash != xml_hash:
            raise AttestationError(
                f"{doc_id} changed upstream but cannot render ({exc}); its old rendering "
                "would be carried forward, which the counted-conformance attestation "
                "refuses, so the sync stops before any embedding is paid for"
            ) from exc
        return None
