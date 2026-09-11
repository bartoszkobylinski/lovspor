"""The drift check: the served document's state against a fresh observation.

ADR-0014 Decision 4 (ADR:1054-1090): after a release, an hourly timer
re-observes both subjects as ``observer: drift-timer``, derives ``state``
with the served document's **own** ``checkout`` as the expectations —
so a moved work tree without a release is not drift of the hosted
state, while a restart that changed what the process runs or serves is
— and compares that ``state`` with the served
``/deployment-capabilities.json``'s ``state``. Never the ``observation``
part: ``observed_at``, ``observer`` and an unobserved ``reason`` are
diagnostic. Any difference is drift, named field by field with dotted
paths; the probe credential expiring is drift of the same kind
(``transport.authenticated.status`` moves), and the fix is rotation,
then a release.

The served document that cannot be read is ``ServedDocumentError``, the
check's own failure — reported as such, never as drift.

The check's **first action** is the admin socket's four facts (Decision 6,
through ``checked_drift``), before any probe runs: the release procedure's
whole permission model rests on them, a Caddy restart is what takes them
away, and an hourly timer is the only thing on the box that would notice.
Observing a host whose admin socket the service user can open and then
reporting *no drift* would be the worst of both — a green unit over an
open door.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from lovspor.release.admin_socket import AdminSocket, check_admin_socket
from lovspor.site.capabilities import CapabilityDocument, State, parse_capabilities
from lovspor.site.errors import CapabilityDocumentError, ServedDocumentError
from lovspor.site.probe import ProbeSettings, probe

_OK = 200


class DriftReport(BaseModel):
    """What the served document says, what the host says now, and where they differ."""

    model_config = ConfigDict(frozen=True)

    served: CapabilityDocument
    observed: CapabilityDocument
    differences: tuple[str, ...]

    @property
    def drifted(self) -> bool:
        return bool(self.differences)


def fetch_served_document(
    url: str, client: httpx.Client, *, timeout_seconds: float
) -> CapabilityDocument:
    """The live ``deployment-capabilities.json``, validated like any other."""
    try:
        response = client.get(url, timeout=timeout_seconds)
    except httpx.TimeoutException as error:
        raise ServedDocumentError("timeout", str(error)) from error
    except httpx.RequestError as error:
        raise ServedDocumentError("network", str(error)) from error
    if response.status_code != _OK:
        raise ServedDocumentError(f"http_{response.status_code}")
    try:
        return parse_capabilities(response.content)
    except CapabilityDocumentError as error:
        raise ServedDocumentError("invalid", str(error).splitlines()[0]) from error


def _differences(left: Any, right: Any, path: str) -> Iterator[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            yield from _differences(left.get(key), right.get(key), f"{path}.{key}" if path else key)
    elif left != right:
        yield path


def state_differences(served: State, observed: State) -> tuple[str, ...]:
    """Dotted paths of every leaf on which the two states disagree, sorted."""
    return tuple(_differences(served.model_dump(mode="json"), observed.model_dump(mode="json"), ""))


def drift_check(
    served_url: str,
    settings: ProbeSettings,
    *,
    client: httpx.Client,
    clock: Callable[[], datetime],
) -> DriftReport:
    """Fetch the served document, re-observe against its checkout, compare states."""
    served = fetch_served_document(served_url, client, timeout_seconds=settings.timeout_seconds)
    observed = probe(settings, client=client, checkout=served.state.checkout, clock=clock)
    return DriftReport(
        served=served,
        observed=observed,
        differences=state_differences(served.state, observed.state),
    )


@dataclass(frozen=True)
class DriftInputs:
    """One drift check: the document to compare with, the probe, and the socket asserted first."""

    served_url: str
    settings: ProbeSettings
    access: AdminSocket


def checked_drift(
    inputs: DriftInputs, *, client: httpx.Client, clock: Callable[[], datetime]
) -> DriftReport:
    """The admin socket's four facts, then the comparison (ADR-0014 Decision 4).

    The order is the contract: *first action*, before the served document
    is fetched and before either subject is observed. A socket the
    unprivileged user can open is not drift and is not something the
    comparison could ever report — it is the precondition of the release
    procedure itself, so the check stops there and names it.
    """
    check_admin_socket(inputs.access)
    return drift_check(inputs.served_url, inputs.settings, client=client, clock=clock)
