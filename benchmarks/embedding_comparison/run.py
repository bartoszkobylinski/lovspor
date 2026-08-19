"""Embedding-model comparison benchmark.

Compares four embedding models on the real lovverk corpus using 80
queries extracted from ``evals/scenarios/``. Outputs Recall@5, MRR,
indexing time, query latency, and per-query breakdowns to a
markdown report.

Models compared:

- NbAiLab/nb-sbert-v2-base    (free, local, 768-dim, 256 max tokens)
- NbAiLab/nb-sbert-v2-large   (free, local, 1024-dim, 512 max tokens)
- text-embedding-3-small      (OpenAI API, 1536-dim, 8191 max tokens)
- text-embedding-3-large      (OpenAI API, 3072-dim, 8191 max tokens)

Usage::

    # NbAiLab models load automatically; OpenAI requires a key.
    export OPENAI_API_KEY=sk-...
    # OR put it in lovspor/.env (already loaded via python-dotenv)

    uv run --extra embeddings python benchmarks/embedding_comparison/run.py \\
        --lovverk-path ~/Programming/Python/lovverk \\
        --output benchmarks/embedding_comparison/results-2026-04-30.md

    # Skip OpenAI (e.g. no key, just compare NbAiLab):
    uv run --extra embeddings python benchmarks/embedding_comparison/run.py \\
        --lovverk-path ~/Programming/Python/lovverk \\
        --skip-openai

Embeddings are cached to ``benchmarks/embedding_comparison/.cache/``
so re-runs of the report (or model subset) reuse prior compute. The cache
key carries a hash of the embedded corpus, so a changed corpus re-indexes
instead of scoring a stale matrix. The cache is gitignored.

Total runtime estimate on M1 16GB:

- v2-base   indexing  ~15 min
- v2-large  indexing  ~30 min
- OpenAI    indexing  ~10 min combined (mostly API latency)
- Query phase         <5  min
- Total                                  ~60-90 min one-time
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pickle
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from lovspor.embeddings.sections import iter_sections, strip_frontmatter

DEFAULT_OUTPUT = Path("benchmarks/embedding_comparison/results-2026-04-30.md")
DEFAULT_CACHE_DIR = Path("benchmarks/embedding_comparison/.cache")
DEFAULT_TOP_K = 5
MIN_SECTION_CHARS = 30  # filter out tiny metadata stubs that pollute search
SECONDS_PER_MINUTE = 60

# Lovdata kortform suffixes get appended to the canonical slug
# (skatteloven-sktl, husleieloven-husll, etc.). Eval scenarios use
# the bare canonical form, so this regex strips any trailing
# "-<lowercase>" suffix when matching candidate slugs against
# expected slugs.
_KORTFORM_SUFFIX = re.compile(r"-[a-zæøå]+$")


@dataclass(frozen=True)
class Query:
    """One realistic user query plus the slug we expect to retrieve."""

    persona: str
    scenario_id: str
    text: str
    expected_slug: str


@dataclass(frozen=True)
class Section:
    """One indexable unit: ``(slug, section_id, text)`` from a doc."""

    slug: str
    section_id: str
    text: str


@dataclass(frozen=True)
class QueryResult:
    """Top-K outcome for one query against one model."""

    query: Query
    hits: list[tuple[str, str, float]]  # (slug, section_id, score)
    rank_of_match: int | None  # 1-indexed; None if no match in top-K
    latency_ms: float


@dataclass
class ModelMetrics:
    name: str
    recall_at_5: float = 0.0
    mrr: float = 0.0
    indexing_seconds: float = 0.0
    median_query_latency_ms: float = 0.0
    sections_indexed: int = 0
    embedding_dim: int = 0
    query_results: list[QueryResult] = field(default_factory=list)


# -------------------------------------------------------------------- queries


def extract_queries(scenarios_dir: Path) -> list[Query]:
    """Read ``evals/scenarios/*.yaml`` and return one Query per scenario.

    Scenarios may have one or more ``slug_match`` patterns under
    ``expected_tool_calls``; we take the first one as the expected
    slug for retrieval scoring. Scenarios without any ``slug_match``
    are skipped — those test tools that don't return a slug
    (corpus_status, list_recent_changes without filter, etc.).
    """
    import yaml  # noqa: PLC0415 — pyyaml comes via sentence-transformers transitively

    queries: list[Query] = []
    for path in sorted(scenarios_dir.glob("*.yaml")):
        persona = path.stem
        with path.open() as f:
            data = yaml.safe_load(f)
        for scenario in data.get("scenarios", []):
            text = (scenario.get("user_query") or "").strip()
            if not text:
                continue
            expected = _first_slug_match(scenario.get("expected_tool_calls", []))
            if expected is None:
                continue
            queries.append(
                Query(
                    persona=persona,
                    scenario_id=scenario["id"],
                    text=text,
                    expected_slug=expected,
                ),
            )
    return queries


def _first_slug_match(tool_calls: list[dict[str, Any]]) -> str | None:
    for call in tool_calls:
        slug = call.get("slug_match")
        if slug:
            return str(slug)
    return None


# -------------------------------------------------------------------- corpus


def load_corpus(lovverk_path: Path) -> list[Section]:
    """Iterate every .md file under ``lovverk/lover/`` and ``lovverk/forskrifter/``,
    parse sections, and return a flat list. Skips INDEX.md and history/.

    Sections shorter than 30 chars are skipped — typically these are
    metadata stubs from edge-case acts and they pollute search
    results without adding signal.
    """
    sections: list[Section] = []
    for dataset in ("lover", "forskrifter"):
        dataset_dir = lovverk_path / dataset
        if not dataset_dir.is_dir():
            continue
        for md_path in sorted(dataset_dir.glob("*.md")):
            if md_path.name == "INDEX.md":
                continue
            slug = md_path.stem
            try:
                raw = md_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            body = strip_frontmatter(raw)
            for sec in iter_sections(body):
                if len(sec.text) < MIN_SECTION_CHARS:
                    continue
                sections.append(
                    Section(slug=slug, section_id=sec.section_id, text=sec.text),
                )
    return sections


# -------------------------------------------------------------------- backends


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray:
        """Return one normalized row per text."""


class NbAiLabBackend:
    """Local sentence-transformers model. Loads on first ``encode``."""

    def __init__(self, model_name: str, display_name: str) -> None:
        self.name = display_name
        self._model_name = model_name
        self._model: Any = None
        self.dim = 0

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed. The benchmark needs "
                "the [embeddings] extra. Run:\n\n"
                "    uv sync --extra embeddings\n\n"
                "then re-run this script.",
            ) from exc

        print(f"  loading {self._model_name} ...", flush=True)
        self._model = SentenceTransformer(self._model_name)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: list[str]) -> np.ndarray:
        self._ensure_loaded()
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        result = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        return np.asarray(result, dtype=np.float32)


class OpenAIBackend:
    """OpenAI Embeddings API client, batched with split-on-failure fallback."""

    _ENDPOINT = "https://api.openai.com/v1/embeddings"
    # 128 not 256: text-embedding-3-large produces 3072-dim float vectors
    # which inflate the JSON response payload to ~7 MB per batch of 256.
    # That repeatedly tripped the 60s read timeout on slow OpenAI nodes.
    # 128 halves the response payload (~3.5 MB) and trades batch count
    # for reliability.
    _BATCH_SIZE = 128
    # Conservative ~5000 token upper bound at ~3.6 chars/token average for
    # Norwegian (compound words pack more than typical English). The
    # text-embedding-3 hard limit is 8191 tokens; if a section still hits
    # 400 here, the split-on-failure path catches it.
    _MAX_INPUT_CHARS = 18_000
    _HTTP_TIMEOUT_SECONDS = 180.0  # 60s was too tight for 3-large
    _MAX_TIMEOUT_RETRIES = 3

    def __init__(self, model_name: str, dim: int, display_name: str, api_key: str) -> None:
        self.name = display_name
        self.dim = dim
        self._model_name = model_name
        self._api_key = api_key

    def encode(self, texts: list[str]) -> np.ndarray:
        import httpx  # noqa: PLC0415

        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        truncated = [t[: self._MAX_INPUT_CHARS] for t in texts]
        out: list[list[float]] = []
        with httpx.Client(timeout=self._HTTP_TIMEOUT_SECONDS) as client:
            for i in range(0, len(truncated), self._BATCH_SIZE):
                batch = truncated[i : i + self._BATCH_SIZE]
                out.extend(self._encode_batch_with_fallback(client, batch))
        arr = np.asarray(out, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    def _encode_batch_with_fallback(
        self,
        client: Any,
        batch: list[str],
    ) -> list[list[float]]:
        """Send a batch; retry on timeout, split on 400. Single-input batch
        that still fails gets a zero-vector placeholder + warning so one
        pathological section does not crash the whole run."""
        import httpx  # noqa: PLC0415

        last_timeout: Exception | None = None
        for attempt in range(1, self._MAX_TIMEOUT_RETRIES + 1):
            try:
                resp = client.post(
                    self._ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"model": self._model_name, "input": batch},
                )
                resp.raise_for_status()
            except httpx.TimeoutException as exc:
                last_timeout = exc
                wait = 2**attempt
                print(
                    f"  WARN: timeout on batch of {len(batch)} "
                    f"(attempt {attempt}/{self._MAX_TIMEOUT_RETRIES}); "
                    f"backoff {wait}s",
                    flush=True,
                )
                time.sleep(wait)
                continue
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:300]
                print(
                    f"  WARN: batch of {len(batch)} failed "
                    f"(status {exc.response.status_code}): {body}",
                    flush=True,
                )
                if len(batch) == 1:
                    preview = batch[0][:80].replace("\n", " ")
                    print(
                        f"  WARN: skipping problematic section "
                        f"({len(batch[0])} chars): {preview!r}",
                        flush=True,
                    )
                    return [[0.0] * self.dim]
                mid = len(batch) // 2
                return self._encode_batch_with_fallback(
                    client, batch[:mid]
                ) + self._encode_batch_with_fallback(client, batch[mid:])
            else:
                data = resp.json()
                return [entry["embedding"] for entry in data["data"]]
        # All retries exhausted on timeout — try splitting as last resort.
        if len(batch) > 1:
            print(
                f"  WARN: timeout exhausted on batch of {len(batch)}, splitting",
                flush=True,
            )
            mid = len(batch) // 2
            return self._encode_batch_with_fallback(
                client, batch[:mid]
            ) + self._encode_batch_with_fallback(client, batch[mid:])
        # Single-section batch that times out repeatedly: skip with warning.
        print(
            f"  WARN: skipping section after {self._MAX_TIMEOUT_RETRIES} "
            f"timeouts: {batch[0][:80]!r}",
            flush=True,
        )
        if last_timeout is not None:
            print(f"  WARN: last timeout: {last_timeout}", flush=True)
        return [[0.0] * self.dim]


# -------------------------------------------------------------------- caching


def corpus_fingerprint(sections: list[Section]) -> str:
    """Hash exactly what gets embedded, in order.

    Keyed by backend name alone, the cache silently served the previous
    matrix after a corpus change, and the reported Recall@5 then described
    a corpus state that no longer existed (issue #108). Model *revision* is
    still uncovered: the backend protocol exposes a display name, not the
    upstream weights it resolves to.
    """
    digest = hashlib.sha256()
    for section in sections:
        for part in (section.slug, section.section_id, section.text):
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


def _load_cached_matrix(cache_path: Path, fingerprint: str) -> np.ndarray | None:
    """Return the cached matrix only if it was built from this exact corpus."""
    if not cache_path.exists():
        return None
    with cache_path.open("rb") as f:
        cached = pickle.load(f)  # noqa: S301 — own-controlled cache, not untrusted input
    if cached.get("fingerprint") != fingerprint:
        print(f"  cache ignored, corpus changed: {cache_path}", flush=True)
        return None
    print(f"  cache hit: {cache_path}", flush=True)
    matrix: np.ndarray = cached["matrix"]
    return matrix


def embed_corpus_cached(
    backend: EmbeddingBackend,
    sections: list[Section],
    cache_dir: Path,
) -> tuple[np.ndarray, float]:
    """Embed the whole corpus, caching under ``cache_dir/<name>-<corpus>.pkl``.

    Returns (matrix, indexing_seconds). Matrix shape: (N, dim).
    """
    fingerprint = corpus_fingerprint(sections)
    cache_path = cache_dir / f"{_safe_filename(backend.name)}-{fingerprint[:16]}.pkl"
    cached_matrix = _load_cached_matrix(cache_path, fingerprint)
    if cached_matrix is not None:
        return cached_matrix, 0.0
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"  indexing {len(sections):,} sections with {backend.name} ...", flush=True)
    start = time.perf_counter()
    texts = [s.text for s in sections]
    matrix = backend.encode(texts)
    elapsed = time.perf_counter() - start
    rate = len(sections) / max(elapsed, 1e-3)
    print(f"  done in {elapsed:.1f}s ({rate:.1f} sec/sec)", flush=True)

    with cache_path.open("wb") as f:
        pickle.dump(
            {"matrix": matrix, "name": backend.name, "fingerprint": fingerprint},
            f,
            protocol=4,
        )
    return matrix, elapsed


def _safe_filename(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "_", name)


# -------------------------------------------------------------------- search


def query_top_k(
    query_vector: np.ndarray,
    matrix: np.ndarray,
    sections: list[Section],
    k: int,
) -> list[tuple[str, str, float]]:
    """Brute-force cosine top-K. Both query and matrix are L2-normalized."""
    if matrix.size == 0:
        return []
    scores = matrix @ query_vector
    top_idx = np.argpartition(-scores, min(k, len(scores) - 1))[:k]
    top_idx = top_idx[np.argsort(-scores[top_idx])]
    return [(sections[i].slug, sections[i].section_id, float(scores[i])) for i in top_idx]


def slug_matches_expected(actual: str, expected: str) -> bool:
    """``arbeidsmiljøloven-aml`` matches ``arbeidsmiljoloven`` (with or
    without diacritics) modulo the kortform suffix."""
    base = _KORTFORM_SUFFIX.sub("", actual)
    return _normalize(base) == _normalize(expected) or _normalize(actual).startswith(
        _normalize(expected)
    )


def _normalize(s: str) -> str:
    return s.lower().translate(str.maketrans("æøå", "aoa"))


# -------------------------------------------------------------------- run


def run_model(
    backend: EmbeddingBackend,
    sections: list[Section],
    queries: list[Query],
    cache_dir: Path,
    k: int,
) -> ModelMetrics:
    print(f"\n=== {backend.name} ===")
    matrix, indexing_seconds = embed_corpus_cached(backend, sections, cache_dir)

    metrics = ModelMetrics(
        name=backend.name,
        indexing_seconds=indexing_seconds,
        sections_indexed=len(sections),
        embedding_dim=int(matrix.shape[1]) if matrix.size else 0,
    )

    latencies: list[float] = []
    rr_sum = 0.0
    hits_at_k = 0
    for q in queries:
        start = time.perf_counter()
        qvec = backend.encode([q.text])[0]
        hits = query_top_k(qvec, matrix, sections, k)
        latency_ms = (time.perf_counter() - start) * 1000.0
        latencies.append(latency_ms)

        rank: int | None = None
        for i, (slug, _section, _score) in enumerate(hits, start=1):
            if slug_matches_expected(slug, q.expected_slug):
                rank = i
                break
        if rank is not None:
            hits_at_k += 1
            rr_sum += 1.0 / rank
        metrics.query_results.append(
            QueryResult(query=q, hits=hits, rank_of_match=rank, latency_ms=latency_ms),
        )

    metrics.recall_at_5 = hits_at_k / max(len(queries), 1)
    metrics.mrr = rr_sum / max(len(queries), 1)
    metrics.median_query_latency_ms = float(np.median(latencies)) if latencies else 0.0
    return metrics


# -------------------------------------------------------------------- report


def write_report(
    output: Path,
    queries: list[Query],
    sections: list[Section],
    metrics: list[ModelMetrics],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    lines.append("# Embedding model comparison — lovspor Sprint 9 decision input")
    lines.append("")
    lines.append("Empirical comparison of four embedding models on the real ``lovverk`` corpus")
    lines.append("using realistic queries extracted from ``evals/scenarios/``. Generated by")
    lines.append("``benchmarks/embedding_comparison/run.py``.")
    lines.append("")
    lines.append(f"- Corpus: {len(sections):,} sections across all current acts")
    lines.append(
        f"- Queries: {len(queries)} (one per eval scenario with a slug_match expected_tool_call)",
    )
    lines.append(
        "- Top-K: 5 (i.e. did the expected slug appear in the top 5 retrieved sections?)",
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Model | Recall@5 | MRR | Indexing time | Median query latency | Dim |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for m in metrics:
        lines.append(
            f"| {m.name} | {m.recall_at_5:.3f} | {m.mrr:.3f} | "
            f"{_format_time(m.indexing_seconds)} | {m.median_query_latency_ms:.1f} ms | "
            f"{m.embedding_dim} |",
        )
    lines.append("")

    lines.append("## Per-persona Recall@5")
    lines.append("")
    personas = sorted({q.persona for q in queries})
    header = ["Persona"] + [m.name for m in metrics] + ["count"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for persona in personas:
        row = [persona]
        persona_qs = [q for q in queries if q.persona == persona]
        for m in metrics:
            persona_hits = sum(
                1
                for r in m.query_results
                if r.query.persona == persona and r.rank_of_match is not None
            )
            row.append(f"{persona_hits / max(len(persona_qs), 1):.2f}")
        row.append(str(len(persona_qs)))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Per-query results")
    lines.append("")
    lines.append("Rank of expected slug in each model's top-5. ``-`` means not found.")
    lines.append("")
    header = ["Scenario", "Expected slug"] + [m.name for m in metrics]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for q in queries:
        row = [q.scenario_id, q.expected_slug]
        for m in metrics:
            r = next(r for r in m.query_results if r.query.scenario_id == q.scenario_id)
            row.append(str(r.rank_of_match) if r.rank_of_match is not None else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    best = max(metrics, key=lambda m: (m.recall_at_5, m.mrr))
    lines.append(
        f"On Recall@5, **{best.name}** leads with {best.recall_at_5:.3f}. "
        "Combine with the operational trade-offs (cost, determinism, network "
        "dependency, EU-region) documented in ``docs/roadmap.md`` Class C1 to "
        "make the final Sprint 9 PR-B model choice.",
    )
    lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport written to {output}", flush=True)


def _format_time(seconds: float) -> str:
    if seconds < 1:
        return "(cached)"
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    return f"{seconds / SECONDS_PER_MINUTE:.1f} min"


# -------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lovverk-path", type=Path, required=True)
    parser.add_argument(
        "--scenarios-dir",
        type=Path,
        default=Path("evals/scenarios"),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--skip-openai", action="store_true")
    parser.add_argument("--skip-nbailab", action="store_true")
    args = parser.parse_args()

    # Load .env if present (mirrors lovspor's existing settings pattern).
    try:
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv()
    except ImportError:
        pass

    print(f"loading queries from {args.scenarios_dir} ...")
    queries = extract_queries(args.scenarios_dir)
    print(f"  {len(queries)} queries with slug_match expectations")

    print(f"\nloading corpus from {args.lovverk_path} ...")
    sections = load_corpus(args.lovverk_path)
    print(f"  {len(sections):,} sections across {len({s.slug for s in sections})} docs")

    backends: list[EmbeddingBackend] = []
    if not args.skip_nbailab:
        backends.append(NbAiLabBackend("NbAiLab/nb-sbert-v2-base", "nb-sbert-v2-base"))
        backends.append(NbAiLabBackend("NbAiLab/nb-sbert-v2-large", "nb-sbert-v2-large"))
    if not args.skip_openai:
        # Accept both OPENAI_API_KEY (OpenAI standard) and OPENAI_APIKEY
        # (some users have it without the underscore from older docs).
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_APIKEY")
        if not api_key:
            print(
                "\nWARNING: OPENAI_API_KEY (or OPENAI_APIKEY) not set; "
                "skipping OpenAI models. Export the key or place it in "
                "lovspor/.env, or pass --skip-openai.",
                file=sys.stderr,
            )
        else:
            backends.append(
                OpenAIBackend(
                    "text-embedding-3-small",
                    1536,
                    "text-embedding-3-small",
                    api_key,
                ),
            )
            backends.append(
                OpenAIBackend(
                    "text-embedding-3-large",
                    3072,
                    "text-embedding-3-large",
                    api_key,
                ),
            )

    if not backends:
        print("no models to run; check --skip flags and OPENAI_API_KEY", file=sys.stderr)
        return 1

    metrics: list[ModelMetrics] = []
    for backend in backends:
        metrics.append(run_model(backend, sections, queries, args.cache_dir, args.top_k))

    write_report(args.output, queries, sections, metrics)

    print("\n=== summary ===")
    for m in metrics:
        print(f"  {m.name:30} Recall@5={m.recall_at_5:.3f}  MRR={m.mrr:.3f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
