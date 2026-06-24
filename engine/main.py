import os
import time
import logging
import gc
import psycopg2
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from google import genai
from io import BytesIO
from PyPDF2 import PdfReader

# --- 0. Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RAG-Engine")

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/postgres")

app = FastAPI()
client = genai.Client(api_key=API_KEY)

# --- 1. Database Connection Helpers ---
def get_db_connection():
    retries = 5
    while retries > 0:
        try:
            conn = psycopg2.connect(DATABASE_URL)
            return conn
        except psycopg2.OperationalError as e:
            logger.warning(f"Database connection failed, retrying... ({retries} left). Error: {e}")
            retries -= 1
            time.sleep(3)
    raise Exception("Could not connect to the database after several retries.")

# --- 2. Text Ingestion & Chunking Logic ---
def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list:
    """Helper to split text into overlapping chunks"""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

@app.get("/health")
async def health():
    # Test DB connection
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy",
        "gemini_api": "active" if API_KEY else "missing",
        "database": db_status
    }

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key missing on Engine")

    logger.info(f"Starting ingestion for: {file.filename}")
    
    # 1. Read and parse PDF
    try:
        content = await file.read()
        reader = PdfReader(BytesIO(content))
        raw_text = ""
        # Process up to 50 pages for demo efficiency
        for page in reader.pages[:50]:
            text = page.extract_text()
            if text:
                raw_text += text + "\n"
        
        del reader
        del content
        gc.collect()
    except Exception as e:
        logger.error(f"PDF parsing error: {e}")
        raise HTTPException(status_code=400, detail=f"PDF parsing failed: {str(e)}")

    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No readable text extracted from PDF")

    # 2. Chunk text
    chunks = chunk_text(raw_text)
    logger.info(f"Split document into {len(chunks)} chunks")

    # 3. Write document registry to DB
    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # Check if already exists and delete to avoid duplicates
        cur.execute("SELECT id FROM documents WHERE filename = %s;", (file.filename,))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM documents WHERE id = %s;", (row[0],))
        
        cur.execute("INSERT INTO documents (filename) VALUES (%s) RETURNING id;", (file.filename,))
        doc_id = cur.fetchone()[0]
        
        # 4. Generate embeddings and insert chunks
        # Call Gemini Embedding API in batches to avoid rate limits
        batch_size = 15
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i+batch_size]
            
            # Embed content
            response = client.models.embed_content(
                model="text-embedding-004",
                contents=batch_chunks
            )
            
            # Insert into database
            for j, embedding_obj in enumerate(response.embeddings):
                chunk_idx = i + j
                chunk_txt = batch_chunks[j]
                vector_val = embedding_obj.values
                
                # Convert list of floats to Postgres vector string representation
                vector_str = "[" + ",".join(map(str, vector_val)) + "]"
                
                cur.execute(
                    "INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding) VALUES (%s, %s, %s, %s::vector);",
                    (doc_id, chunk_idx, chunk_txt, vector_str)
                )
        
        conn.commit()
        logger.info(f"Ingestion successful for {file.filename} (ID: {doc_id})")
        return {"status": "success", "document_id": doc_id, "chunks_count": len(chunks)}
    except Exception as e:
        conn.rollback()
        logger.error(f"Ingestion failed: {e}")
        raise HTTPException(status_code=500, detail=f"Ingestion database error: {str(e)}")
    finally:
        cur.close()
        conn.close()

@app.post("/query")
async def query_rag(
    filename: str = Form(...),
    analysis_type: str = Form(...),
    language: str = Form(...)
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key missing on Engine")

    logger.info(f"Querying document: {filename} with analysis: {analysis_type} ({language})")

    conn = get_db_connection()
    cur = conn.cursor()
    
    try:
        # 1. Retrieve document ID
        cur.execute("SELECT id FROM documents WHERE filename = %s;", (filename,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found. Please upload first.")
        doc_id = row[0]

        # 2. Build target prompts
        prompts = {
            "comprehensive": "You are a senior investment analyst. Perform a deep institutional research analysis based on the retrieved context.",
            "compliance": "You are a senior compliance officer. Audit the retrieved context for risk disclosures and regulatory red flags.",
            "quick": "You are a fund manager's assistant. Provide a high-speed 3-minute executive brief based on the retrieved context."
        }
        
        lang_map = {
            "en": "English",
            "zh_cn": "Simplified Chinese (简体中文)",
            "zh_hk": "Traditional Chinese (繁體中文)"
        }
        target_lang = lang_map.get(language, "English")
        
        # 3. Query embedding
        query_text = prompts.get(analysis_type, "Analyze this report")
        emb_response = client.models.embed_content(
            model="text-embedding-004",
            contents=query_text
        )
        query_vector = emb_response.embeddings[0].values
        query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        # 4. Semantic Search in pgvector
        cur.execute(
            """
            SELECT chunk_text 
            FROM document_chunks 
            WHERE document_id = %s 
            ORDER BY embedding <=> %s::vector 
            LIMIT 5;
            """,
            (doc_id, query_vector_str)
        )
        
        rows = cur.fetchall()
        retrieved_context = "\n\n".join([r[0] for r in rows])
        
        # 5. Generate final response using Gemini
        language_instruction = (
            f"IMPORTANT: The user has selected {target_lang} as their preferred language. "
            f"You MUST generate the entire report in {target_lang}. Use professional financial terminology appropriate for that language."
        )
        
        final_prompt = (
            f"{language_instruction}\n\n"
            f"{query_text}\n\n"
            f"[RETIREVED CONTEXT FROM SEC 10-K FILING]:\n{retrieved_context}"
        )

        # Dual-path fallback
        try:
            interaction = client.interactions.create(
                model="gemini-2.5-flash",
                input=final_prompt
            )
            analysis_result = interaction.outputs[-1].text
        except Exception:
            response = client.models.generate_content(
                model="gemini-1.5-flash",
                contents=final_prompt
            )
            analysis_result = response.text

        return {"analysis": analysis_result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Query execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"RAG search failed: {str(e)}")
    finally:
        cur.close()
        conn.close()
