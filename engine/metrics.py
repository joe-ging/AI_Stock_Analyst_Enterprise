"""
Expose Prometheus metrics from the JL Intelligence RAG Engine.

Metrics tracked:
  - jl_inference_requests_total       (counter, by analysis_type + language)
  - jl_inference_duration_seconds     (histogram, end-to-end latency)
  - jl_cache_hits_total               (counter)
  - jl_cache_misses_total             (counter)
  - jl_ragas_faithfulness             (gauge, last run)
  - jl_ragas_answer_recall            (gauge, last run)
  - jl_ragas_relevance                (gauge, last run)
  - jl_edgar_fetches_total            (counter, by ticker)
  - jl_milvus_chunks_stored_total     (counter)
"""

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry, REGISTRY

# ── Inference Metrics ──────────────────────────────────────────────────────────
INFERENCE_REQUESTS = Counter(
    "jl_inference_requests_total",
    "Total inference requests",
    ["analysis_type", "language", "upload_mode"]
)

INFERENCE_DURATION = Histogram(
    "jl_inference_duration_seconds",
    "End-to-end inference latency in seconds",
    ["analysis_type"],
    buckets=[5, 10, 20, 30, 60, 90, 120, 180, 300]
)

# ── Cache Metrics ──────────────────────────────────────────────────────────────
CACHE_HITS = Counter(
    "jl_cache_hits_total",
    "Total Redis cache hits",
    ["analysis_type"]
)

CACHE_MISSES = Counter(
    "jl_cache_misses_total",
    "Total Redis cache misses",
    ["analysis_type"]
)

# ── Ragas Self-Audit Metrics ───────────────────────────────────────────────────
RAGAS_FAITHFULNESS = Gauge(
    "jl_ragas_faithfulness",
    "Latest Ragas faithfulness score (0.0–1.0)"
)

RAGAS_ANSWER_RECALL = Gauge(
    "jl_ragas_answer_recall",
    "Latest Ragas answer recall score (0.0–1.0)"
)

RAGAS_RELEVANCE = Gauge(
    "jl_ragas_relevance",
    "Latest Ragas answer relevance score (0.0–1.0)"
)

# ── EDGAR Metrics ──────────────────────────────────────────────────────────────
EDGAR_FETCHES = Counter(
    "jl_edgar_fetches_total",
    "Total SEC EDGAR filing fetches",
    ["ticker", "year"]
)

EDGAR_FETCH_DURATION = Histogram(
    "jl_edgar_fetch_duration_seconds",
    "Time to fetch and parse an SEC EDGAR filing",
    buckets=[1, 2, 5, 10, 20, 30, 60]
)

# ── Milvus / Storage Metrics ───────────────────────────────────────────────────
MILVUS_CHUNKS_STORED = Counter(
    "jl_milvus_chunks_stored_total",
    "Total document chunks stored in Milvus",
    ["source"]  # "pdf" or "edgar"
)


def record_ragas_scores(scores: dict):
    """Update Ragas gauge metrics from a score dict."""
    if not scores:
        return
    if "faithfulness" in scores:
        RAGAS_FAITHFULNESS.set(scores["faithfulness"])
    if "answer_recall" in scores:
        RAGAS_ANSWER_RECALL.set(scores["answer_recall"])
    if "relevance" in scores:
        RAGAS_RELEVANCE.set(scores["relevance"])
