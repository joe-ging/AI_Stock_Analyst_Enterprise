# 🚀 Database Tuning & Future Scalability Plan

This document outlines the proactive and reactive database tuning strategies for the **JL Intelligence AI Stock Analyst Enterprise** platform. As data volume grows and the system operates over extended periods, these optimizations will ensure latency remains strictly within SLAs (P99 < 5s for queries) and resource consumption is minimized.

## 1. PostgreSQL (Relational Database) Optimization
**Current Role:** Stores Job metadata, analysis status, and user request logs.
**Pain Point (Future):** As the `jobs` table accumulates millions of rows, `SELECT` queries for job status updates will degrade from O(1) to O(N), causing frontend timeouts.

### 🛠️ Tuning Action Plan
- **Phase 1: Indexing Strategy**
  - Execute `EXPLAIN ANALYZE` on slow queries to identify missing indexes.
  - Apply **B-Tree indexes** on frequently queried columns: `CREATE INDEX idx_jobs_status ON jobs(status);` and `CREATE INDEX idx_jobs_user_id ON jobs(user_id);`.
- **Phase 2: Connection Management**
  - Implement **PgBouncer** as a connection pooler to prevent FastAPI/Celery workers from exhausting the maximum PostgreSQL connections (avoiding `FATAL: too many clients already` errors).
- **Phase 3: Maintenance & Vacuuming**
  - Tune the `autovacuum` daemon to run more frequently during off-peak hours to reclaim storage and prevent transaction ID wraparound.

---

## 2. Milvus (Vector Database) Optimization
**Current Role:** Stores high-dimensional (768D) embeddings of SEC filings and PDF chunks.
**Pain Point (Future):** The default `FLAT` index performs a brute-force search. With hundreds of thousands of document chunks, memory usage will spike and cosine similarity searches will bottleneck the RAG pipeline.

### 🛠️ Tuning Action Plan
- **Phase 1: Index Algorithm Upgrade**
  - Migrate from `FLAT` to **`IVF_FLAT`** (Inverted File Flat) or **`HNSW`** (Hierarchical Navigable Small World).
  - *Trade-off:* `HNSW` drastically reduces search latency (50x faster) at the cost of marginally higher memory overhead and ~1% drop in extreme recall accuracy.
- **Phase 2: Search Parameter Tuning**
  - Tune `nlist` (number of cluster units) and `nprobe` (number of units to search). 
  - *Formula:* Higher `nprobe` = better accuracy but slower speed. Adjust dynamically based on Grafana API duration metrics.
- **Phase 3: Hardware Scaling**
  - Separate Milvus query nodes from data nodes, allowing horizontal scaling of query nodes if read traffic surges.

---

## 3. Redis (High-Speed Cache) Optimization
**Current Role:** Caches highly relevant AI research reports (Cosine > 0.97) for instantaneous 0ms retrieval.
**Pain Point (Future):** AI reports contain thousands of tokens. Storing too many will quickly exhaust the Redis RAM limit, leading to OOM (Out Of Memory) crashes.

### 🛠️ Tuning Action Plan
- **Phase 1: Memory Eviction Policy**
  - Explicitly configure the `maxmemory-policy` to **`allkeys-lru`** (Least Recently Used). This ensures that reports for obscure, rarely searched stocks are evicted first, keeping reports for blue-chip stocks (e.g., AAPL, NVDA) strictly in memory.
- **Phase 2: Payload Compression**
  - Since AI reports are heavy text (Markdown/JSON), compress the payload using `GZIP` or `Brotli` before writing to Redis. This can reduce memory footprint by 70%.
- **Phase 3: Cache Penetration Defense**
  - Implement a Bloom Filter to prevent attackers from querying non-existent tickers, which would otherwise bypass Redis and hammer the Milvus/DeepSeek backend unnecessarily.

---

## 📈 Monitoring & Tracing
All tuning efforts will be guided by the **PLG (Promtail-Loki-Grafana) Stack**:
1. Monitor the `http_request_duration_seconds` (P95/P99 latency) on the Grafana dashboard.
2. Watch for `OOMKilled` events in Loki for Redis and Milvus containers.
3. Establish alerting thresholds (e.g., Alert if Database CPU > 80% for 5 minutes).
