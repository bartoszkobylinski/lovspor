"""The public site as a generated artifact of this repository (ADR-0014).

Everything Caddy serves outside ``/mcp`` and outside the ADR-0013 corpus
namespaces is built here: templates, the shared page chrome both
generators render, the fact mechanism that ties every published number
to a named artifact, the capability-document schema and the release
identifiers. The package lives under ``src/lovspor/`` so the corpus
generator's ``pages.layout()`` can import the chrome (ADR-0014 Decision
5); publication itself is source-checkout tooling and runs only inside
a clean git work tree at a pinned revision (Decision 1).

Nothing in this package calls a clock: the only time values in a site
build are the corpus manifest's ``corpus_commit_time`` and the probe's
``observed_at``, both copied facts (Decision 3).
"""
