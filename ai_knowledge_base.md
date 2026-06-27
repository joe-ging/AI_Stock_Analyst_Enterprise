# 🤖 AI Agent Knowledge Base & Redline Rules

This repository contains critical system architectures and live deployment configurations. As an AI Agent coding assistant, you **MUST** strictly follow the guidelines documented in this file to prevent system crashes and maintain analytical integrity.

---

## ⚠️ 1. Server Deployment Redlines (Tencent HK Instance)

The remote cloud server (`43.129.249.161`) has a delicate virtualized network stack. **NEVER** run commands that tear down the entire container cluster.

*   **PROHIBITED ACTIONS**:
    *   **NEVER** execute `docker-compose down` or `docker compose down`.
    *   **NEVER** run mass termination scripts like `docker stop $(docker ps -q)`.
    *   *Why?* Terminating heavy databases (Milvus cluster, PostgreSQL, RabbitMQ) simultaneously crashes the Linux kernel bridge network interface, locking all public SSH and HTTP ports.
*   **CORRECT WORKFLOW FOR DEPLOYING CODE CHANGES**:
    *   To apply edits to a single service (e.g. `engine`), **ONLY** rebuild and restart that specific service container using:
        ```bash
        docker-compose up -d --build engine
        ```
    *   This is highly safe, takes less than 5 seconds, and will not impact other databases or network topologies.

---

## 📝 2. Citation Formatting & Footnote Compiler Pipeline

To guarantee formal academic-grade formatting and prevent LLM loop-crashes:

*   **LLM Rule**: The LLM during draft generation must **ONLY** write raw page sources inline as `[Page X]` next to figures (e.g., *"...revenues rose 12% [Page 15]."*).
*   **Prohibition**: The LLM **MUST NOT** write any HTML `<sup>` tags, nor should it try to write a "CITATIONS", "REFERENCES", or bibliography list at the end of its response.
*   **Backend Compilation**:
    *   When the stream finishes, the backend Python `compile_footnotes` parser automatically intercepts the raw text, swaps `[Page X]` to neat sequential `<sup>1</sup>`, `<sup>2</sup>` tags, and appends a static, cleanly-wrapped Bluebook-style references list to the end of the report.
    *   Do not modify this pipeline or try to make the LLM hand-write the footer citations list. Hand-writing citations causes the model to hit token limits and crash.

---

## 📊 3. Ragas Authentic Quality Scoring

We strictly prioritize data integrity over placeholder aesthetics.

*   **Evaluation Principle**: The Ragas quality scores (Faithfulness, Recall, Relevance) rendered in the footer cards must reflect **genuine, raw evaluated metrics** computed by the LLM.
*   **Prohibition**: **NEVER** write heuristic override logic or backend mocks (e.g., hardcoded values like `0.94` if model fails) to fake a perfect rating.
*   **Failure Handling**: If the evaluation model fails to output valid scores, return them as `0.0` or `null`. The user wants to see authentic performance metrics, not faked statistics.
