-- Enable the pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Table to store uploaded documents
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) UNIQUE NOT NULL,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table to store document chunks and their vector embeddings
CREATE TABLE IF NOT EXISTS document_chunks (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(768) NOT NULL
);

-- Create an HNSW index for high-speed vector similarity searches
CREATE INDEX IF NOT EXISTS document_chunks_hnsw_idx 
ON document_chunks USING hnsw (embedding vector_cosine_ops);
