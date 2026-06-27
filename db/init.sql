-- Table to store uploaded documents metadata
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) UNIQUE NOT NULL,
    company_name VARCHAR(255),
    doc_type VARCHAR(50),
    doc_year VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table to store query and output history
CREATE TABLE IF NOT EXISTS query_logs (
    id SERIAL PRIMARY KEY,
    document_ids VARCHAR(255),
    target_query TEXT NOT NULL,
    language VARCHAR(50),
    output_text TEXT,
    ragas_scores JSONB,
    inference_time_ms INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
