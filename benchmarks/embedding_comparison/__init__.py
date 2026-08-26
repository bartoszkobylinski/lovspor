"""One-off empirical comparison of embedding models for the lovverk corpus.

Runs four models (NbAiLab nb-sbert-v2-base + v2-large, OpenAI
text-embedding-3-small + 3-large) against the same 47 queries
extracted from ``evals/scenarios/`` over the real ``lovverk``
corpus. Outputs Recall@5, MRR, and per-query breakdowns to a
markdown report so the model choice for Sprint 9 PR-B is data-driven
rather than vibes-driven.

This is decision-support, not production code — it lives in
``benchmarks/`` to make the separation from the eval suite (which
is permanent CI infrastructure) explicit. The results file is
intended to be committed for portfolio "I picked the model
empirically" narrative.
"""
