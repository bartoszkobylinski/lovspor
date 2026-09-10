"""The release envelope and the control-plane transaction (ADR-0014 Decision 6).

A release is one envelope with two trees — ``<release>/corpus/``
(ADR-0013's output) and ``<release>/site/`` (ADR-0014's) — plus two
root files written once the envelope's ``release_content_id`` is known:
``release.json``, the record, and ``release.caddy``, the fragment the
host's Caddyfile imports. The envelope is built and finalized under a
``.build-*`` name and renamed to its id in one step, so nothing under
an id name is ever incomplete (``build``); it is made live by staging,
validating and committing its fragment, reloading Caddy and only then
writing the marker (``control``); and what is live is never a pointer
but the reconciled triple of Caddy's running configuration, the
adapted configuration on disk and the marker (``control.live_release``).

Nothing in this package calls a clock: the probe's clock is injected
by the command layer (``commands``), the one place that reads it.
"""
