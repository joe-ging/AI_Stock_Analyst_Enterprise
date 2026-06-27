import os
import time
import logging
import psycopg2
import psycopg2.pool
import uvicorn
import redis
import json
import asyncio
import uuid
import boto3
from enum import Enum
from typing import AsyncGenerator
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse
import httpx
from google import genai
from google.genai import types
from io import BytesIO
from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection

# Import Celery task
from tasks import ingest_pdf_task

# --- 0. Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("RAG-Engine")

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/postgres")
REDIS_URL = os.environ.get("REDIS_URL", "redis://cache:6379/0")

# Milvus Connection Configs
MILVUS_HOST = os.environ.get("MILVUS_HOST", "milvus")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")

# S3 / MinIO Configs
s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "http://minio:9000")
s3_key_id = os.environ.get("S3_ACCESS_KEY_ID", "minioadmin")
s3_secret = os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin")

# --- Input Validation Enums ---
class AnalysisType(str, Enum):
    comprehensive = "comprehensive"
    compliance = "compliance"
    quick = "quick"

class Language(str, Enum):
    en = "en"
    zh_cn = "zh_cn"
    zh_hk = "zh_hk"

# --- Unified Report Templates (Single Source of Truth) ---
REPORT_TEMPLATES = {
    "comprehensive": {
        "role": "Lead Equity Research Analyst",
        "query": (
            "Prepare an institutional-grade investment memorandum. Structure the report exactly as follows:\n"
            "0. **REPORT HEADER**: Ticker, Sector, Current Stock Price (with currency), Current Rating (Buy/Hold/Sell), Price Target, and Market Capitalization. Presentation must look like a standard Wall Street analyst report.\n"
            "1. **EXECUTIVE SUMMARY & INVESTMENT THESIS**: State the investment rating and price target. Detail the core investment thesis and key near-term catalysts.\n"
            "2. **BUSINESS DESCRIPTION & SEGMENT BREAKDOWN**: Analyze the business model, reportable segments, and key revenue drivers. Use markdown tables to compare segment net revenues and operating income trends across fiscal years.\n"
            "3. **FINANCIAL PERFORMANCE & CASH FLOW AUDIT**: Deep dive into profit margins (Operating Margin, EBITDA trend), balance sheet leverage, capital expenditures, and free cash flow generation.\n"
            "4. **VALUATION, LEVEL 3 ASSETS & CAPITAL STRUCTURE AUDIT**: Audit the valuation methodologies (e.g., DCF model inputs, comparable multiples), unobservable inputs for Level 3 assets, and critical tax considerations (specifically PFIC classification status and cross-border tax treatment).\n"
            "5. **KEY INVESTMENT RISKS & MITIGATION MATRIX**: Provide a graded matrix table (High/Medium/Low impact) evaluating regulatory, geopolitical, competitive, and operational risks alongside specific mitigation factors.\n"
            "6. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        ),
        "struct": (
            "You are a Lead Equity Research Analyst preparing an institutional-grade investment memorandum for executive leadership and the investment committee. "
            "The report must be highly quantitative, objective, and structured exactly as follows:\n\n"
            "0. **REPORT HEADER**: Display standard Wall Street research header including Ticker, Rating, Target Price, and basic equity data.\n"
            "1. **EXECUTIVE SUMMARY & INVESTMENT THESIS**: Outline rating, target price, investment thesis, and near-term catalysts.\n"
            "2. **BUSINESS DESCRIPTION & SEGMENT BREAKDOWN**: Detailed analysis of segments and revenue streams with comparison tables.\n"
            "3. **FINANCIAL PERFORMANCE & CASH FLOW AUDIT**: Audit margins, leverage, and free cash flow trends.\n"
            "4. **VALUATION, LEVEL 3 ASSETS & CAPITAL STRUCTURE AUDIT**: Valuation model assumptions (DCF/comps), Level 3 assets unobservable inputs, and PFIC tax status analysis.\n"
            "5. **KEY INVESTMENT RISKS & MITIGATION MATRIX**: Graded table (High/Medium/Low) of regulatory, geopolitical, and business risks and mitigations.\n"
            "6. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    },
    "compliance": {
        "role": "Chief Compliance Officer",
        "query": (
            "Prepare a professional, institutional-grade regulatory audit report. Structure the report exactly as follows:\n"
            "0. **COMPLIANCE METRICS HEADER**: Ticker, SEC Filing Type (e.g., Form 20-F), Filing Date, Overall Compliance Risk Rating (High/Medium/Low Risk), Primary Jurisdictions (e.g., US/PRC/HK), and Lead Auditor/Firm.\n"
            "1. **COMPLIANCE EXECUTIVE SUMMARY**: Overall compliance risk posture summary, critical compliance vulnerabilities, and corrective action priority levels.\n"
            "2. **REGULATORY POLICY & SHIFT IMPACTS**: Detailed audit of the PCAOB audit inspection history, HFCAA compliance, data cross-border transfers (e.g., CAC filings), and Generative AI service regulatory compliance requirements.\n"
            "3. **LITIGATION, INTELLECTUAL PROPERTY & AUDIT GAPS**: Comprehensive analysis of copyrights/trademark disputes, historical administrative fines, material litigations, and control gaps in contract compliance.\n"
            "4. **VIE STRUCTURE, TAX COMPLIANCE & PFIC DISCLOSURE**: Audit of Variable Interest Entity (VIE) regulatory validity, foreign exchange repatriation rules, PFIC (Passive Foreign Investment Company) status tests, and U.S. federal income tax implications.\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        ),
        "struct": (
            "You are a Chief Compliance Officer preparing a regulatory audit report for the Board of Directors and the Audit Committee. "
            "The report must be highly formal, legally precise, and structured exactly as follows:\n\n"
            "0. **COMPLIANCE METRICS HEADER**: Display standard compliance metadata including Risk Rating and Jurisdictions.\n"
            "1. **COMPLIANCE EXECUTIVE SUMMARY**: Clear risk posture summary and correction action priority levels.\n"
            "2. **REGULATORY POLICY & SHIFT IMPACTS**: PCAOB, HFCAA, data security, and generative AI regulation audit.\n"
            "3. **LITIGATION, INTELLECTUAL PROPERTY & AUDIT GAPS**: Audits of legal disputes, fine history, and contract control gaps.\n"
            "4. **VIE STRUCTURE, TAX COMPLIANCE & PFIC DISCLOSURE**: Legal validity of VIE structure, capital control, and PFIC tax status.\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    },
    "quick": {
        "role": "Senior Investment Analyst",
        "query": (
            "Provide a high-speed brief for executive leadership (CEO/CFO). Structure the report exactly as follows:\n"
            "1. **EXECUTIVE ACTIONS & RECOMMENDATIONS**: One-sentence core thesis.\n"
            "2. **KEY FINANCIAL HIGHLIGHTS**: Bullet points of key revenue growth and margins.\n"
            "3. **IMMINENT RISK ALERTS**: Two major risk issues that cannot be ignored.\n"
            "4. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        ),
        "struct": (
            "You are a Senior Investment Analyst providing a high-speed brief for executive leadership (CEO/CFO). "
            "The brief must be extremely concise, bulleted, and structured exactly as follows:\n\n"
            "1. **EXECUTIVE ACTIONS & RECOMMENDATIONS**: One-sentence core rating and actionable recommendation.\n"
            "2. **KEY FINANCIAL HIGHLIGHTS**: Bullet points of key revenue growth and margins.\n"
            "3. **IMMINENT RISK ALERTS**: Two major risk issues that cannot be ignored.\n"
            "4. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    }
}

# Generic sub-queries for multi-faceted retrieval (works for any SEC filing)
GENERIC_SUB_QUERIES = [
    "Core business operations, revenue segments, and year-over-year financial performance trends",
    "Risk factors, pending litigation, intellectual property disputes, and regulatory compliance issues",
    "PFIC status, tax classification tests, cross-border tax implications for foreign investors, capital structure and Level 3 fair value measurements"
]

LANG_MAP = {
    "en": "English",
    "zh_cn": "Simplified Chinese (简体中文)",
    "zh_hk": "Traditional Chinese (繁體中文)"
}

def build_citation_instruction(filename: str) -> str:
    """Generates a generic citation format instruction based on the uploaded filename."""
    return (
        f"- Format every citation in the 'Citations / References' section exactly as: "
        f"[Footnote Number] Source: {filename}, at Page [Number] "
        f"(where [Number] MUST be one of the actual page numbers from the retrieved context below)."
    )

def build_final_prompt(target_query: str, struct_instructions: str, retrieved_context: str, target_lang: str, filename: str) -> str:
    """Builds the final LLM prompt with citation constraints."""
    language_instruction = (
        f"IMPORTANT: The user has selected {target_lang} as their preferred language. "
        f"You MUST generate the entire report in {target_lang}. Use professional financial terminology."
    )
    citation_instruction = build_citation_instruction(filename)
    return (
        f"{language_instruction}\n\n"
        f"{target_query}\n\n"
        f"IMPORTANT PROFESSIONAL FINANCIAL REPORTING INSTRUCTIONS:\n"
        f"{struct_instructions}\n\n"
        f"STRICT CITATION CONSTRAINTS (CRITICAL FOR FAITHFULNESS):\n"
        f"- You MUST ONLY use the facts, figures, and page numbers present in the [RETRIEVED DATA] block below. Do NOT use your pre-trained memory or make up page numbers.\n"
        f"- DO NOT introduce any external regulatory codes, tax form numbers, or specific tax rates UNLESS they are explicitly written in the [RETRIEVED DATA] below.\n"
        f"- For every financial figure, percentage, rate, date, or specific claim, you MUST append a sequential superscript footnote indicator (e.g., <sup>1</sup>, <sup>2</sup>).\n"
        f"{citation_instruction}\n\n"
        f"[RETRIEVED DATA FROM SEC FILING]:\n{retrieved_context}"
    )

def build_audit_prompt(draft: str, retrieved_context: str, target_lang: str, filename: str) -> str:
    """Builds the audit prompt for the second-pass LLM review."""
    citation_instruction = build_citation_instruction(filename)
    return (
        f"You are a Senior Financial Audit Agent. Review the following draft report against the original source context. "
        f"Ensure that all dates, financial numbers, margins, and page references match the source exactly. "
        f"Correct any misstatements or formatting gaps.\n\n"
        f"IMPORTANT CITATION AUDIT:\n"
        f"1. Make sure every single number, percentage, and date has a superscript footnote indicator.\n"
        f"2. Validate that NO page numbers other than those in the retrieved context are cited. Correct any hallucinated page numbers.\n"
        f"3. Strip out any external tax forms, tax rates, or law details that are not explicitly present in the retrieved context to maintain 100% faithfulness.\n"
        f"4. Ensure the 'Citations / References' section at the end is present, sequential, and formatted correctly.\n"
        f"{citation_instruction}\n\n"
        f"Output the final polished report in {target_lang}.\n\n"
        f"[SOURCE CONTEXT]:\n{retrieved_context}\n\n"
        f"[DRAFT REPORT]:\n{draft}"
    )

client = genai.Client(api_key=API_KEY)

app = FastAPI()

def call_deepseek(prompt: str, model: str = "deepseek-chat") -> str:
    """Synchronous DeepSeek call (kept for backward compat with tests/eval)"""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2
    }
    
    with httpx.Client(timeout=60.0, trust_env=False) as cl:
        response = cl.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

async def call_deepseek_async(prompt: str, model: str = "deepseek-chat") -> str:
    """Async DeepSeek call (non-blocking for the event loop)"""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2
    }
    
    async with httpx.AsyncClient(timeout=120.0) as cl:
        response = await cl.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

async def stream_deepseek(prompt: str, model: str = "deepseek-chat") -> AsyncGenerator[str, None]:
    """Streaming DeepSeek call — yields text chunks as they arrive"""
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY is not configured")
    
    url = "https://api.deepseek.com/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "stream": True
    }
    
    async with httpx.AsyncClient(timeout=120.0) as cl:
        async with cl.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue

# --- Infrastructure Connections (initialized at startup) ---
redis_client = None
db_pool = None

@app.on_event("startup")
def startup_init():
    """Initialize all infrastructure connections once at startup."""
    global redis_client, db_pool
    # Redis
    try:
        redis_client = redis.from_url(REDIS_URL, socket_connect_timeout=5)
        redis_client.ping()
        logger.info("Connected to Redis successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")
    
    # PostgreSQL connection pool
    try:
        db_pool = psycopg2.pool.ThreadedConnectionPool(minconn=2, maxconn=10, dsn=DATABASE_URL)
        logger.info("PostgreSQL connection pool initialized (2-10 connections).")
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL pool: {e}")
    
    # Milvus
    try:
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
        logger.info("Connected to Milvus successfully.")
    except Exception as e:
        logger.error(f"Failed to connect to Milvus: {e}")

# S3 Client Helper
def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=s3_key_id,
        aws_secret_access_key=s3_secret,
        endpoint_url=s3_endpoint
    )

# --- 1. Database Connection Helpers ---
def get_db_connection():
    """Get a connection from the pool, falling back to direct connect."""
    if db_pool:
        try:
            return db_pool.getconn()
        except Exception:
            pass
    # Fallback: direct connection with retries
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

def return_db_connection(conn):
    """Return a connection to the pool."""
    if db_pool:
        try:
            db_pool.putconn(conn)
            return
        except Exception:
            pass
    conn.close()

# --- 2. Milvus Connections ---
def get_milvus_connection():
    """Ensure Milvus connection is alive (reconnects if needed)."""
    try:
        if not connections.has_connection("default"):
            connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)
    except Exception:
        connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

def init_cache_collection():
    """Initializes the collection used for the semantic cache"""
    get_milvus_connection()
    collection_name = "semantic_cache_index"
    
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="query_text", dtype=DataType.VARCHAR, max_length=1024),
            FieldSchema(name="cache_key", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=768)
        ]
        schema = CollectionSchema(fields, "Semantic query cache mappings")
        collection = Collection(collection_name, schema)
        
        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 64}
        }
        collection.create_index("embedding", index_params)
        logger.info(f"Milvus semantic cache collection created: {collection_name}")
    else:
        collection = Collection(collection_name)
    
    collection.load()
    return collection

@app.get("/health")
async def health():
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1;")
        cur.close()
        conn.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    # Test Milvus Connection
    try:
        get_milvus_connection()
        milvus_status = "connected"
    except Exception as e:
        milvus_status = f"error: {str(e)}"
        
    return {
        "status": "healthy",
        "gemini_api": "active" if API_KEY else "missing",
        "database": db_status,
        "milvus": milvus_status
    }

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key missing on Engine")

    logger.info(f"Ingest endpoint hit for: {file.filename}")
    
    # 1. Check PostgreSQL first to skip parsing if already ingested
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM documents WHERE filename = %s;", (file.filename,))
        row = cur.fetchone()
        if row:
            doc_id = row[0]
            # Verify Milvus has vectors for this document
            get_milvus_connection()
            if utility.has_collection("stock_analysis_chunks"):
                collection = Collection("stock_analysis_chunks")
                collection.load()
                cnt_res = collection.query(expr=f"document_id == {doc_id}", output_fields=["id"])
                if len(cnt_res) > 0:
                    logger.info(f"Document {file.filename} already fully indexed in Milvus (doc_id={doc_id}, vectors={len(cnt_res)}). Skipping Ingestion.")
                    return {"status": "skipped", "document_id": doc_id, "chunks_count": len(cnt_res)}
            
            # If collection doesn't exist or is empty, delete PG record to proceed fresh
            cur.execute("DELETE FROM documents WHERE id = %s;", (doc_id,))
            conn.commit()
    except Exception as e:
        logger.warning(f"Database pre-check encountered warning: {e}. Attempting recovery.")
        try:
            cur.execute("DELETE FROM documents WHERE filename = %s;", (file.filename,))
            conn.commit()
        except Exception as db_err:
            logger.error(f"Recovery PG delete failed: {db_err}")
    finally:
        cur.close()
        conn.close()

    # 2. Upload file to MinIO (S3) bucket "sec-filings"
    s3 = get_s3_client()
    try:
        # Create bucket if it doesn't exist
        try:
            s3.create_bucket(Bucket="sec-filings")
        except s3.exceptions.BucketAlreadyExists:
            pass
        except s3.exceptions.BucketAlreadyOwnedByYou:
            pass
        
        # Upload
        file_bytes = await file.read()
        s3.upload_fileobj(BytesIO(file_bytes), "sec-filings", file.filename)
        logger.info(f"Uploaded raw PDF bytes to S3/MinIO bucket 'sec-filings' under key '{file.filename}'")
    except Exception as e:
        logger.error(f"S3 upload to MinIO failed: {e}")
        raise HTTPException(status_code=500, detail=f"S3/MinIO Object Storage upload failed: {str(e)}")

    # 3. Create document registry in PostgreSQL
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO documents (filename) VALUES (%s) RETURNING id;", (file.filename,))
        doc_id = cur.fetchone()[0]
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Failed to write metadata registry to PostgreSQL: {e}")
        raise HTTPException(status_code=500, detail=f"Metadata registry failed: {str(e)}")
    finally:
        cur.close()
        conn.close()

    # 4. Trigger Celery Task & wait asynchronously (non-blocking event loop)
    logger.info(f"Triggering Celery background parsing task for doc_id={doc_id}")
    task = ingest_pdf_task.delay(file.filename, doc_id)
    
    # Async polling
    timeout_limit = 600.0
    elapsed = 0.0
    while not task.ready():
        await asyncio.sleep(0.5)
        elapsed += 0.5
        if elapsed > timeout_limit:
            logger.error(f"Ingestion Celery task timed out after {timeout_limit} seconds")
            raise HTTPException(status_code=504, detail="Background ingestion parsing timed out.")
    
    # Get Celery result
    res = task.result
    if isinstance(res, dict) and res.get("status") == "failed":
        logger.error(f"Ingestion worker failed: {res.get('error')}")
        raise HTTPException(status_code=500, detail=f"Parsing task failed: {res.get('error')}")

    logger.info(f"Document ingestion completely successful: {file.filename}")
    return {"status": "success", "document_id": doc_id, "chunks_count": res.get("chunks_count")}

@app.post("/query")
async def query_rag(
    filename: str = Form(...),
    analysis_type: AnalysisType = Form(...),
    language: Language = Form(...)
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key missing on Engine")

    start_time = time.time()
    cache_uuid = None
    
    # 1. Redis Semantic Cache (Cosine > 0.97 in Milvus cache index)
    template = REPORT_TEMPLATES[analysis_type.value]
    target_query = template["query"]
    
    # Embed the query to check cache
    try:
        emb_query_res = client.models.embed_content(
            model="gemini-embedding-2",
            contents=target_query,
            config=types.EmbedContentConfig(output_dimensionality=768)
        )
        query_vector = emb_query_res.embeddings[0].values
    except Exception as e:
        logger.error(f"Embedding query failed: {e}")
        raise HTTPException(status_code=500, detail=f"Embedding calculation error: {str(e)}")

    # Check semantic cache
    cache_collection = init_cache_collection()
    search_params = {"metric_type": "COSINE", "params": {}}
    cache_results = cache_collection.search(
        data=[query_vector],
        anns_field="embedding",
        param=search_params,
        limit=1,
        output_fields=["cache_key", "query_text"]
    )
    
    if len(cache_results) > 0 and len(cache_results[0]) > 0:
        match = cache_results[0][0]
        similarity = match.distance
        matched_key = match.entity.get("cache_key")
        
        # Enforce high semantic similarity threshold (Cosine >= 0.97)
        if similarity >= 0.97 and redis_client:
            try:
                cached_data = redis_client.get(matched_key)
                if cached_data:
                    logger.info(f"Semantic Cache HIT (Score: {similarity:.4f}) for key: {matched_key}")
                    result = json.loads(cached_data.decode("utf-8"))
                    result["cache_hit"] = True
                    result["inference_time_ms"] = int((time.time() - start_time) * 1000)
                    return result
            except Exception as e:
                logger.error(f"Redis cache fetch failed: {e}")

    logger.info("Semantic Cache MISS. Proceeding with vector search and agent loop.")

    # 2. Retrieve document ID
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM documents WHERE filename = %s;", (filename,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found. Please upload first.")
        doc_id = row[0]
    finally:
        cur.close()
        conn.close()

    # 3. LangGraph Multi-Agent Orchestrator Emulation
    # Node 1: Router Node
    logger.info("LangGraph [Router Node] executing...")
    lang_map = {
        "en": "English",
        "zh_cn": "Simplified Chinese (简体中文)",
        "zh_hk": "Traditional Chinese (繁體中文)"
    }
    target_lang = LANG_MAP.get(language.value, "English")
    logger.info(f"Router directed query: {analysis_type.value} | Language: {target_lang}")

    # Node 2: Retriever Node (Milvus Parent-Child Search with Query Decomposition)
    logger.info("LangGraph [Retriever Node] executing parent-child similarity search...")
    get_milvus_connection()
    collection = Collection("stock_analysis_chunks")
    
    # Generic sub-queries that work for any SEC filing
    sub_queries = [target_query] + GENERIC_SUB_QUERIES
    
    # Parallel sub-query embedding via asyncio
    async def embed_single(sq):
        # Parallel sub-query embedding via standard REST API over requests (supporting socks5h)
        import requests
        proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
        proxies_map = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent?key={API_KEY}"
        payload = {
            "content": {
                "parts": [{"text": sq}]
            }
        }
        
        def _sync_embed():
            res = requests.post(url, json=payload, proxies=proxies_map, timeout=30.0)
            res.raise_for_status()
            return res.json()["embedding"]["values"]
            
        try:
            return await asyncio.to_thread(_sync_embed)
        except Exception as e:
            logger.error(f"Embedding subquery failed: {e}")
            return None
    
    embedding_results = await asyncio.gather(*[embed_single(sq) for sq in sub_queries])
    query_vectors = [v for v in embedding_results if v is not None]

    if not query_vectors:
        query_vectors = [query_vector]
    
    # Parallel Milvus search via asyncio
    def search_milvus_sync(q_vec):
        return collection.search(
            data=[q_vec],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=3,
            expr=f"document_id == {doc_id}",
            output_fields=["page_number", "parent_text", "child_text"]
        )
    
    search_results = await asyncio.gather(*[asyncio.to_thread(search_milvus_sync, qv) for qv in query_vectors])
    
    retrieved_items = []
    seen_parents = set()
    for search_res in search_results:
        if len(search_res) > 0:
            for match in search_res[0]:
                parent_txt = match.entity.get("parent_text")
                if parent_txt not in seen_parents:
                    seen_parents.add(parent_txt)
                    retrieved_items.append({
                        "page_number": match.entity.get("page_number"),
                        "parent_text": parent_txt,
                        "child_text": match.entity.get("child_text")
                    })
    
    # Build RAG context with parent text (keeps table structure and section intact)
    retrieved_context = ""
    citations = []
    for item in retrieved_items:
        page = item["page_number"]
        parent_txt = item["parent_text"]
        retrieved_context += f"\n--- [Page {page}] ---\n{parent_txt}\n"
        citations.append({
            "chunk_index": page, # We citation page number directly!
            "text": f"Page {page}: " + parent_txt[:200].strip() + "..."
        })

    # Node 3: Generator & Auditor Agent (Model Cascade)
    logger.info("LangGraph [Auditor & Generator Node] executing Model Cascade...")
    struct_instructions = template["struct"]
    final_prompt = build_final_prompt(target_query, struct_instructions, retrieved_context, target_lang, filename)

    # Initial Draft by DeepSeek (deepseek-chat) with Gemini fallback
    draft_result = None
    if DEEPSEEK_API_KEY:
        try:
            logger.info("Generating initial draft using DeepSeek Chat...")
            draft_result = call_deepseek(final_prompt, model="deepseek-chat")
        except Exception as ds_err:
            logger.warning(f"DeepSeek Chat draft generation failed: {ds_err}. Falling back to Gemini 2.5 Flash...")
            if API_KEY:
                try:
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=final_prompt
                    )
                    draft_result = response.text
                except Exception as gemini_err:
                    logger.error(f"Gemini draft fallback also failed: {gemini_err}")
                    raise ds_err
            else:
                raise ds_err
    else:
        logger.info("Generating initial draft using Gemini 2.5 Flash...")
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=final_prompt
        )
        draft_result = response.text
    
    # Audit & Final Polish — SKIP for quick mode (saves ~15s)
    if analysis_type == AnalysisType.quick:
        logger.info("Quick mode: skipping audit stage for faster output.")
        final_report = draft_result
    else:
        audit_prompt = build_audit_prompt(draft_result, retrieved_context, target_lang, filename)
        
        final_report = None
        if DEEPSEEK_API_KEY:
            try:
                logger.info("Performing audit & polish using DeepSeek Chat...")
                final_report = call_deepseek(audit_prompt, model="deepseek-chat")
            except Exception as ds_err:
                logger.warning(f"DeepSeek Chat audit failed: {ds_err}. Falling back to Gemini 2.5 Pro...")
                if API_KEY:
                    try:
                        pro_response = client.models.generate_content(
                            model="gemini-2.5-pro",
                            contents=audit_prompt
                        )
                        final_report = pro_response.text
                    except Exception as gemini_err:
                        logger.error(f"Gemini audit fallback also failed: {gemini_err}")
                        raise ds_err
                else:
                    raise ds_err
        else:
            logger.info("Cascading to Gemini 2.5 Pro for Auditor sanity check & factual verification...")
            pro_response = client.models.generate_content(
                model="gemini-2.5-pro",
                contents=audit_prompt
            )
            final_report = pro_response.text
    
    logger.info("LangGraph agent loop successfully completed.")

    # 4. Save to Redis and register in Semantic Cache Index
    output_data = {
        "analysis": final_report,
        "citations": citations,
        "retrieved_context": retrieved_context,
        "cache_hit": False,
        "inference_time_ms": int((time.time() - start_time) * 1000)
    }
    
    new_cache_key = f"analysis_cache_store:{uuid.uuid4()}"
    if redis_client:
        try:
            # Cache for 2 hours
            redis_client.setex(new_cache_key, 7200, json.dumps(output_data))
            
            # Index vector in Milvus
            cache_collection.insert([[target_query], [new_cache_key], [query_vector]])
            cache_collection.flush()
            logger.info(f"Saved query results to Redis and indexed query in Milvus cache: {new_cache_key}")
        except Exception as e:
            logger.error(f"Failed to update semantic cache store: {e}")

    return output_data

# --- Streaming SSE Endpoint ---

async def _build_rag_context(filename: str, analysis_type: str, language: str):
    """Shared helper: retrieves context, builds prompts using REPORT_TEMPLATES.
    Returns dict with prompt data, or None if cache hit."""
    template = REPORT_TEMPLATES.get(analysis_type)
    if not template:
        raise HTTPException(status_code=400, detail=f"Invalid analysis_type: {analysis_type}")
    target_query = template["query"]
    logger.info(f"[DEBUG] _build_rag_context: Query template found, embedding query text: {target_query[:60]}...")
    # Embed query via standard REST API over requests (supporting socks5h)
    import requests
    proxy_url = os.environ.get("HTTP_PROXY") or os.environ.get("ALL_PROXY")
    proxies_map = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent?key={API_KEY}"
    payload = {
        "content": {
            "parts": [{"text": target_query}]
        }
    }
    
    logger.info("[DEBUG] Requesting Gemini Embedding via standard REST API (requests)...")
    res = requests.post(url, json=payload, proxies=proxies_map, timeout=30.0)
    res.raise_for_status()
    query_vector = res.json()["embedding"]["values"]
        
    logger.info("[DEBUG] _build_rag_context: Embedding successful. Querying Milvus semantic cache...")
    
    # Check semantic cache
    cache_collection = init_cache_collection()
    search_params = {"metric_type": "COSINE", "params": {}}
    cache_results = cache_collection.search(
        data=[query_vector], anns_field="embedding", param=search_params,
        limit=1, output_fields=["cache_key", "query_text"]
    )
    if len(cache_results) > 0 and len(cache_results[0]) > 0:
        match = cache_results[0][0]
        if match.distance >= 0.97 and redis_client:
            cached_data = redis_client.get(match.entity.get("cache_key"))
            if cached_data:
                return None  # Signal: cache hit
    
    # Retrieve doc_id
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM documents WHERE filename = %s;", (filename,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found.")
        doc_id = row[0]
    finally:
        cur.close()
        return_db_connection(conn)
    
    target_lang = LANG_MAP.get(language, "English")
    logger.info(f"[DEBUG] _build_rag_context: Retreiving doc chunks from Milvus for doc_id={doc_id}...")
    
    # Parallel retrieval using generic sub-queries
    get_milvus_connection()
    collection = Collection("stock_analysis_chunks")
    sub_queries = [target_query] + GENERIC_SUB_QUERIES
    
    async def embed_single(sq):
        try:
            emb_res = await asyncio.to_thread(
                client.models.embed_content, model="gemini-embedding-2",
                contents=sq, config=types.EmbedContentConfig(output_dimensionality=768)
            )
            return emb_res.embeddings[0].values
        except Exception:
            return None
    
    embedding_results = await asyncio.gather(*[embed_single(sq) for sq in sub_queries])
    q_vectors = [v for v in embedding_results if v is not None] or [query_vector]
    
    def search_sync(qv):
        return collection.search(
            data=[qv], anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=3, expr=f"document_id == {doc_id}",
            output_fields=["page_number", "parent_text", "child_text"]
        )
    search_results = await asyncio.gather(*[asyncio.to_thread(search_sync, qv) for qv in q_vectors])
    
    retrieved_items = []
    seen_parents = set()
    for sr in search_results:
        if len(sr) > 0:
            for m in sr[0]:
                pt = m.entity.get("parent_text")
                if pt not in seen_parents:
                    seen_parents.add(pt)
                    retrieved_items.append({"page_number": m.entity.get("page_number"), "parent_text": pt})
    
    retrieved_context = ""
    citations = []
    for item in retrieved_items:
        page = item["page_number"]
        parent_txt = item["parent_text"]
        retrieved_context += f"\n--- [Page {page}] ---\n{parent_txt}\n"
        citations.append({"chunk_index": page, "text": f"Page {page}: " + parent_txt[:200].strip() + "..."})
    
    # Build prompts using shared helpers
    struct_instructions = template["struct"]
    final_prompt = build_final_prompt(target_query, struct_instructions, retrieved_context, target_lang, filename)
    
    return {
        "final_prompt": final_prompt,
        "citations": citations,
        "retrieved_context": retrieved_context,
        "target_query": target_query,
        "target_lang": target_lang,
        "query_vector": query_vector,
        "cache_collection": cache_collection,
        "analysis_type": analysis_type,
        "filename": filename
    }

@app.post("/query/stream")
async def query_rag_stream(
    filename: str = Form(...),
    analysis_type: AnalysisType = Form(...),
    language: Language = Form(...)
):
    """Streaming SSE endpoint — tokens are pushed to client as they are generated"""
    if not DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="Streaming requires DeepSeek API Key")
    
    start_time = time.time()
    logger.info(f"[DEBUG] query_rag_stream: Endpoint hit for {filename}. Building RAG context...")
    ctx = await _build_rag_context(filename, analysis_type.value, language.value)
    logger.info(f"[DEBUG] query_rag_stream: Context built successfully (is_none={ctx is None}). Starting SSE generator...")
    
    if ctx is None:
        # Cache hit — not streamable, redirect to regular endpoint
        raise HTTPException(status_code=307, detail="Cache hit. Use /query endpoint.")
    
    final_prompt = ctx["final_prompt"]
    citations = ctx["citations"]
    retrieved_context = ctx["retrieved_context"]
    target_query = ctx["target_query"]
    target_lang = ctx["target_lang"]
    query_vector = ctx["query_vector"]
    cache_collection = ctx["cache_collection"]
    ctx_filename = ctx["filename"]
    
    async def sse_generator() -> AsyncGenerator[str, None]:
        full_text = ""
        
        if analysis_type == AnalysisType.quick:
            # Quick mode: stream draft directly, no audit
            logger.info("[Stream] Quick mode: streaming draft directly...")
            async for chunk in stream_deepseek(final_prompt):
                full_text += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        else:
            # Draft stage: stream to client
            logger.info("[Stream] Streaming draft generation...")
            draft_text = ""
            yield f"data: {json.dumps({'type': 'stage', 'content': 'draft'})}\n\n"
            async for chunk in stream_deepseek(final_prompt):
                draft_text += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
            
            # Audit stage: stream the polished version
            yield f"data: {json.dumps({'type': 'stage', 'content': 'audit'})}\n\n"
            logger.info("[Stream] Streaming audit & polish...")
            audit_prompt = build_audit_prompt(draft_text, retrieved_context, target_lang, ctx_filename)
            async for chunk in stream_deepseek(audit_prompt):
                full_text += chunk
                yield f"data: {json.dumps({'type': 'token', 'content': chunk})}\n\n"
        
        # Cache the final result
        if not full_text:
            full_text = draft_text if 'draft_text' in dir() else ""
        output_data = {
            "analysis": full_text,
            "citations": citations,
            "retrieved_context": retrieved_context,
            "cache_hit": False,
            "inference_time_ms": int((time.time() - start_time) * 1000)
        }
        new_cache_key = f"analysis_cache_store:{uuid.uuid4()}"
        if redis_client:
            try:
                redis_client.setex(new_cache_key, 7200, json.dumps(output_data))
                cache_collection.insert([[target_query], [new_cache_key], [query_vector]])
                cache_collection.flush()
                logger.info(f"[Stream] Cached results: {new_cache_key}")
            except Exception as e:
                logger.error(f"[Stream] Cache write failed: {e}")
        
        # Send final metadata event
        yield f"data: {json.dumps({'type': 'done', 'inference_time_ms': int((time.time() - start_time) * 1000), 'citations': citations})}\n\n"
    
    return StreamingResponse(sse_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)

