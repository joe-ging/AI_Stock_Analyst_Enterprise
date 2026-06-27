<div align="right">
  <a href="README.md"><img src="https://img.shields.io/badge/Language-English-blue?style=for-the-badge" alt="English"></a>
  <a href="README_zh.md"><img src="https://img.shields.io/badge/语言-简体中文-red?style=for-the-badge" alt="简体中文"></a>
</div>

# 📊 JL Intelligence - Enterprise AI Analyst (Microservices Architecture)

> AI-powered SEC financial analysis tool for institutional investors. Built with a production-ready microservices architecture, supporting distributed asynchronous ingestion, multi-modal semantic caching, and strict Ragas objective auditing.

**Live Demo:** [JL Intelligence](https://jl-intelligence.netlify.app/)
**Core Stack:** React · FastAPI · Milvus · Redis · Celery/RabbitMQ · DeepSeek / Gemini / OpenAINext

---

## 🔹 Enterprise Microservices Architecture & Tech Stack

Our system is completely decoupled into specialized microservices, avoiding the bottlenecks of monolithic designs. We utilize an event-driven architecture to handle high-concurrency document processing and low-latency semantic retrieval.

### Core Tech Stack:
- **Frontend**: React (SPA), Tailwind CSS
- **API Gateway & Core Engine**: FastAPI (Python 3.10)
- **Document Parsing**: `pdfplumber` (for precise layout and table extraction)
- **Message Broker**: RabbitMQ
- **Background Workers**: Celery
- **Vector Database**: Milvus (Standalone) backed by MinIO & etcd
- **Relational Database**: PostgreSQL (for document metadata tracking)
- **Caching Layer**: Redis (for Task states and Semantic Caching)
- **AI Models & Orchestration**: 
  - **Generation**: DeepSeek-Chat (Primary) with streaming inference via Server-Sent Events (SSE).
  - **Fallback**: Gemini 2.5 Flash / Pro (Seamless fallback if primary model fails).
  - **Embeddings**: OpenAINext (`text-embedding-3-small`) with Gemini embedding fallback.
  - **Orchestration**: LangChain-style RAG pipelines with custom LangGraph-inspired Auditor routing loops.

---

## 🔹 Microservices Event-Driven Flows (Architecture Diagrams)

The core strength of our physical architecture is how microservices communicate and listen to each other asynchronously. Here are the three primary system flows:

### 1. Asynchronous Ingestion & Vectorization Flow (The Worker Loop)

When a massive 200-page SEC 10-K report is uploaded, the Engine does not block the user's HTTP request. Instead, it registers the job and delegates the heavy lifting to the Celery Worker cluster via RabbitMQ.

```mermaid
sequenceDiagram
    participant UI as React Client
    participant Engine as FastAPI Engine
    participant PG as PostgreSQL
    participant MQ as RabbitMQ
    participant Worker as Celery Worker
    participant AI as OpenAINext / Gemini
    participant Milvus as Milvus DB
    
    UI->>Engine: POST /upload (PDF File)
    Engine->>PG: Insert Document Metadata (Status: Pending)
    Engine->>MQ: Publish Ingestion Task (Job ID)
    Engine-->>UI: Return 202 Accepted (Job ID)
    
    MQ->>Worker: Consume Task
    activate Worker
    Worker->>Worker: Parse with pdfplumber
    Worker->>Worker: Semantic Chunking (1000 chars)
    Worker->>AI: Request Embeddings
    AI-->>Worker: Return Vector Dimensions
    Worker->>Milvus: Upsert Vectors & Metadata
    Worker->>PG: Update Status -> 'Completed'
    deactivate Worker
```

### 2. Semantic Caching & Hybrid Retrieval Flow (The Query Loop)

To minimize expensive LLM API calls and drastically reduce latency, the Engine intercepts queries and checks a Redis-backed Semantic Cache before hitting the Vector DB.

```mermaid
sequenceDiagram
    participant UI as React Client
    participant Engine as FastAPI Engine
    participant Redis as Redis Cache
    participant AI as OpenAINext (Embeddings)
    participant Milvus as Milvus DB
    
    UI->>Engine: GET /query
    Engine->>AI: Embed User Query
    AI-->>Engine: Query Vector
    
    Engine->>Redis: Vector Cosine Similarity Search
    alt Cache Hit (Cosine > 0.97)
        Redis-->>Engine: Cached Analytical Report
        Engine-->>UI: Return Cached Report (0ms LLM Latency)
    else Cache Miss
        Redis-->>Engine: Not Found
        Engine->>Milvus: Hybrid Search (Query Vector)
        Milvus-->>Engine: Top-K Relevant Chunks
        Engine->>Engine: Rerank and Context Assembly
    end
```

### 3. Streaming Inference & Objective Auditing Flow (The Generation Loop)

We implement real-time streaming inference using Server-Sent Events (SSE). Once generation finishes, an isolated Ragas auditing process is launched to ensure institutional compliance.

```mermaid
sequenceDiagram
    participant UI as React Client
    participant Engine as FastAPI Engine
    participant DeepSeek as DeepSeek API
    participant Fallback as Gemini API (Fallback)
    participant Ragas as Ragas Auditor (Gemini Pro)
    
    Engine->>DeepSeek: Stream Completion Request (Prompt + Chunks)
    
    alt DeepSeek Throttled/Fails
        DeepSeek--xEngine: 429 / 500 Error
        Engine->>Fallback: Trigger Seamless Fallback
        Fallback-->>Engine: Stream Tokens
    else DeepSeek Success
        DeepSeek-->>Engine: Stream Tokens
    end
    
    Engine-->>UI: Yield Tokens via SSE (Server-Sent Events)
    
    Note over Engine, Ragas: Post-Generation Audit Phase
    Engine->>Ragas: Evaluate (Draft Report vs Original Chunks)
    Ragas-->>Engine: Faithfulness & Relevance Scores
    Engine-->>UI: Yield {type: "done", citations, scores}
```

---

## 🔹 DevOps & CI/CD Pipeline

The system runs on a containerized environment deployed via automated CI/CD pipelines to ensure reliability and Zero-Downtime deployments.

```mermaid
graph LR
    A[Git Push to Main] -->|GitHub Actions| B[CI/CD Pipeline]
    B --> C[Run PyTests & Linting]
    C --> D[Build Docker Images]
    D --> E[Deploy to Remote Server]
    E --> F[Graceful Restart (docker-compose)]
```

### Deployment (DevOps & MLOps Maintenance)
- **Containerization**: Everything runs inside isolated Docker containers managed by `docker-compose`, making horizontal scaling of Celery workers trivial.
- **Automated Testing**: Every push triggers integration tests (e.g., `test_e2e_stream.py`) to validate the RAG retrieval logic and API connectivity.
- **Hot-Reload Deployments**: The deployment script (`deploy.sh`) selectively rebuilds and restarts only the modified application containers (`gateway`, `engine`, `worker`), leaving stateful services (`milvus`, `postgres`, `redis`) untouched to ensure zero data corruption.

---

## 🚀 Quick Start (Local Docker Deployment)

```bash
# Clone repository
git clone https://github.com/joe-ging/AI_Stock_Analyst_Enterprise.git
cd AI_Stock_Analyst_Enterprise

# Set environment variables
echo "GEMINI_API_KEY=your_key" >> .env
echo "DEEPSEEK_API_KEY=your_key" >> .env
echo "OPENAINEXT_API_KEY=your_key" >> .env

# Launch entire microservice cluster
docker-compose up -d --build

# View logs
docker-compose logs -f engine worker
```

**Access the Application:** Navigate to `http://localhost:8000/index.html`

---

## 🔹 Solution Architecture Highlights (Tencent SA Interview Alignment)

This project natively demonstrates several key **Solutions Architect (SA)** principles derived from the codebase's actual implementation, directly aligning with enterprise cloud requirements:

1. **Cost Optimization (TCO) & Semantic Caching:** 
   LLM inference is expensive. The codebase implements a Redis interceptor (`query_cache`) that computes the cosine similarity of user queries against previous requests. If similarity is > 0.97, the API completely bypasses the Vector DB and LLM layers, delivering a 0ms response and significantly reducing API Token costs.
2. **High Availability (HA) & Fault Tolerance:** 
   The system implements a robust **LLM Cascade**. If the primary `DeepSeek-Chat` endpoint hits a rate limit (HTTP 429) or crashes (HTTP 500), the `call_llm_with_fallback` mechanism dynamically routes the stream to `Gemini 2.5 Flash / Pro`. This guarantees system resilience and continuous delivery without the user experiencing downtime.
3. **Decoupling Compute via Microservices:** 
   Extracting structured tables from SEC PDFs via `pdfplumber` is highly CPU-bound. Instead of blocking the FastAPI thread pool, the architecture publishes an asynchronous event to RabbitMQ. The Celery Worker cluster consumes this queue, allowing the HTTP API Gateway to scale independently of the ingestion pipeline.
4. **Zero-Downtime DevOps Delivery:** 
   The deployment workflow utilizes GitOps principles (GitHub Actions). The custom `deploy.sh` script applies rolling updates specifically to stateless containers (`gateway`, `engine`), intentionally preserving stateful volumes (`postgres`, `milvus`, `redis`) to prevent enterprise data corruption.
5. **Multi-Cloud Networking & Proxy Elimination (TCO Strategy):** 
   Due to Gemini API's strict regional blocking in Hong Kong, the initial architecture relied on a brittle SOCKS5 proxy tunnel routing all LLM requests through an AWS Sydney EC2 instance. This drastically increased latency. As a subsequent SA optimization, the stateless `engine` and `gateway` containers were permanently migrated to an AWS Sydney environment, natively bypassing regional API blocks and eliminating the proxy layer overhead, reducing API latency by over 50%.

---

## 📄 License
MIT
