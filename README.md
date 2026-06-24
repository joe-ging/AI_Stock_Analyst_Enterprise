# 📊 AI Stock Analyst Enterprise (Layer 1)

> **Enterprise-Grade RAG Architecture with Microservices, Vector Search, and Containerization.**

This repository is the **Enterprise Upgrade (v2)** of the original `AI_Stock_Analyst` prototype. It refactors the single-file analyzer into a highly scalable, decoupled **3-tier microservice architecture** containerized with Docker.

---

## 🔹 Enterprise Architecture (Layer 1)

```
                       ┌─────────────────────────┐
                       │   Frontend (React SPA)  │
                       └────────────┬────────────┘
                                    │
                            HTTP (Port 8000)
                                    │
                       ┌────────────▼────────────┐
                       │   Service A: Gateway    │ (FastAPI / Gateway)
                       │   - Serves Static Web   │
                       │   - Reverse proxies     │
                       └────────────┬────────────┘
                                    │
                            HTTP (Port 8001)
                                    │
                       ┌────────────▼────────────┐
                       │   Service B: RAG Engine │ (FastAPI / Gemini API)
                       │   - Ingests & Chunks PDF│
                       │   - Vectorizes text     │
                       └────────────┬────────────┘
                                    │
                         SQL / pgvector (Port 5432)
                                    │
                       ┌────────────▼────────────┐
                       │      Service C: DB      │ (PostgreSQL + pgvector)
                       │   - Stores vectors &    │
                       │     text chunks         │
                       └─────────────────────────┘
```

### Key Enhancements over Prototype
1.  **Microservices Decoupling**: Frontend gateway is completely separated from the computationally heavy RAG engine, allowing independent scaling.
2.  **True Vector Search (RAG)**: Replaced page-truncation with semantic chunking and **vector similarity search** (Cosine similarity `order by embedding <=> query_vector`) powered by PostgreSQL's `pgvector` extension.
3.  **No Size Limits**: Since parsing and chunking are done in vector databases, the application can now query massive 100MB+ documents by retrieving only the most semantically relevant chunks as LLM context.
4.  **Containerized Deployment**: Docker-Compose handles orchestration, networking, and environment configurations out-of-the-box.

---

## 🚀 Quick Start (Local & Cloud Deployment)

### 1. Prerequisite
Ensure Docker and Docker-Compose are installed on your machine.

### 2. Configure Environment
Set your Gemini API Key in your terminal:
```bash
export GEMINI_API_KEY="your-api-key-here"
```

### 3. Launch Services
From the root of this project, spin up the entire stack:
```bash
docker-compose up --build -d
```

This will automatically:
*   Spin up a **PostgreSQL database with pgvector** and execute the initialization schema (`db/init.sql`).
*   Build and run the **RAG Engine** on port 8001.
*   Build and run the **API Gateway** on port 8000.

Open your browser and navigate to `http://localhost:8000` (or `http://your-server-ip:8000` if running on the cloud) to access the system.
