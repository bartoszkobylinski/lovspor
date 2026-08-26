"""One-off empirical comparison of embedding models for the lovverk corpus.

Runs four models (NbAiLab nb-sbert-v2-base + v2-large, OpenAI
text-embedding-3-small + 3-large) against the same queries
extracted from ``evals/scenarios/`` (52 as of 2026-08-26 — one per
scenario with a ``slug_match`` expected tool call, so the count follows
the scenario files; the committed 2026-04-30 results had 47) over the
real ``lovverk`` corpus. Outputs Recall@5, MRR, and per-query breakdowns to a
markdown report so the model choice for Sprint 9 PR-B is data-driven
rather than vibes-driven.

This is decision-support, not production code — it lives in
``benchmarks/`` to make the separation from the eval suite (which
is permanent CI infrastructure) explicit. The results file is
intended to be committed for portfolio "I picked the model
empirically" narrative.
"""
