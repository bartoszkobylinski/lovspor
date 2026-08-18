"""Local-law temporal observatory: capture layer for lokale forskrifter.

ADR-0010. A separate collection trust domain from the central authoritative
ingest: it observes municipal and fylkeskommune publication, preserves what it
saw as immutable evidence, and asserts nothing about law. Nothing here writes
to the canonical corpus — promotion is an explicit later step (ADR-0010 §6),
and no code path in this package knows how to reach ``lovverk``.

What an observation means is deliberately narrow (ADR-0010 §3): an
``ObservedAt`` records that specific bytes were retrievable from a recorded
source endpoint at that time. Not that they were legally published, not that
they are valid law, not that the URL was publicly discoverable.
"""
