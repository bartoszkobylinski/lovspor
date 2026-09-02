"""Serving composition for the ``get_temporal_events`` tool (ADR-0012).

Composes, from one raw corpus state body, the served temporal answer:
:func:`~lovspor.temporal.derive_temporal_layer` (strict — an unrecognised
marker is a typed derivation failure, never a partial layer) together with
:func:`~lovspor.temporal.extract_never_in_force`, an optional mechanical
``section_id`` narrowing, and an optional ``valid_at`` evaluation bounded
by the serving state's knowledge horizon.

The module is pure: no clock, no git, no corpus access. The caller
resolves the state, supplies its body and horizon, and owns the
state-level response fields (identifiers, evidence, ``reconciliation``).

Narrowing is label matching, nothing else (ADR-0012 point 7): an event is
kept when the provision label the parser attributed folds to the requested
section id under :func:`~lovspor.headings.canonical_section_id` — the same
folding that makes a caller's citation match the corpus's own spellings.
Events at enclosing scopes (chapters, parts, headings) never expand into
descendant provisions. Problems are never narrowed: the seven non-fatal
kinds are part of every successful response (ADR-0012 point 3).
"""

from collections.abc import Callable
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from lovspor.headings import canonical_section_id
from lovspor.temporal import (
    TEMPORAL_PARSER_VERSION,
    AmendmentEvent,
    Evaluation,
    NeverInForceMarker,
    derive_temporal_layer,
    evaluate_event,
    evaluate_never_in_force,
    extract_never_in_force,
)

__all__ = ["TemporalEventsRequest", "compose_temporal_events"]


class TemporalEventsRequest(BaseModel):
    """One composition request against one resolved corpus state body.

    ``section_id`` is the canonical lookup form (``"8-7a"``), already
    validated by the caller to exist in the document. ``horizon`` is the
    knowledge horizon of the serving state — the author date of its
    resolved commit (ADR-0012 point 5); it is consulted only when
    ``valid_at`` is present.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon: date
    document_ref: str | None = None
    section_id: str | None = None
    valid_at: date | None = None


def compose_temporal_events(body: str, request: TemporalEventsRequest) -> dict[str, Any]:
    """The served temporal answer for one state body.

    Raises :class:`~lovspor.errors.TemporalDerivationError` when strict
    derivation fails — no partial layer is ever served (ADR-0012 point 3).
    """
    layer = derive_temporal_layer(body, document_ref=request.document_ref, strict=True)
    events = _narrowed(layer.events, request.section_id)
    markers = _narrowed(extract_never_in_force(body), request.section_id)
    result: dict[str, Any] = {
        "temporal_parser_version": TEMPORAL_PARSER_VERSION,
        "events": [_payload(event, evaluate_event, request) for event in events],
        "never_in_force": [_payload(m, evaluate_never_in_force, request) for m in markers],
        "problems": [problem.model_dump(mode="json") for problem in layer.problems],
    }
    if request.valid_at is not None:
        result["valid_at"] = request.valid_at.isoformat()
        result["knowledge_horizon"] = request.horizon.isoformat()
    return result


def _narrowed[T: (AmendmentEvent, NeverInForceMarker)](
    items: list[T],
    section_id: str | None,
) -> list[T]:
    if section_id is None:
        return items
    return [item for item in items if canonical_section_id(item.provision) == section_id]


def _payload[T: (AmendmentEvent, NeverInForceMarker)](
    model: T,
    evaluator: Callable[[T, date, date], Evaluation],
    request: TemporalEventsRequest,
) -> dict[str, Any]:
    """One served object; the verdict pair rides along iff evaluating.

    An evaluated response carries the pair on EVERY served object — there
    is no unevaluated raw marker in an evaluated response (ADR-0012
    point 5); an unevaluated response carries no verdict fields at all.
    """
    payload = model.model_dump(mode="json")
    if request.valid_at is None:
        return payload
    evaluation = evaluator(model, request.valid_at, request.horizon)
    payload["commencement_status"] = evaluation.status.value
    payload["status_reason"] = None if evaluation.reason is None else evaluation.reason.value
    return payload
