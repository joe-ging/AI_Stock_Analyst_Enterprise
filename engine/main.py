import os
import time
import logging
import gc
import psycopg2
import uvicorn
import redis
import json
import asyncio
import uuid
import boto3
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
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

app = FastAPI()
client = genai.Client(api_key=API_KEY)

def call_deepseek(prompt: str, model: str = "deepseek-chat") -> str:
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

# Connect to Redis
redis_client = None
try:
    redis_client = redis.from_url(REDIS_URL, socket_connect_timeout=2)
    logger.info("Connected to Redis successfully.")
except Exception as e:
    logger.error(f"Failed to connect to Redis: {e}")

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

# --- 2. Milvus Connections ---
def get_milvus_connection():
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
    analysis_type: str = Form(...),
    language: str = Form(...)
):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API Key missing on Engine")

    start_time = time.time()
    cache_uuid = None
    
    # 1. Redis Semantic Cache (Cosine > 0.95 in Milvus cache index)
    query_text_map = {
        "comprehensive": (
            "You are a Lead Equity Research Analyst preparing an institutional-grade investment memorandum. Structure the report exactly as follows:\n"
            "1. **EXECUTIVE SUMMARY & INVESTMENT THESIS**: State the investment rating (Buy/Hold/Sell) and the core qualitative justification.\n"
            "2. **FINANCIAL PERFORMANCE & TREND AUDIT**: Analyze revenues, operational margins, and cash flow trends. Use markdown tables to compare fiscal years.\n"
            "3. **KEY INVESTMENT RISKS & MITIGATION**: Graded analysis (High/Medium/Low impact) of regulatory, competitive, and operational risks.\n"
            "4. **VALUATION & CAPITAL STRUCTURE AUDIT**: Analyze long-term investments, Level 3 asset valuations, and tax considerations (e.g., PFIC classification status).\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        ),
        "compliance": (
            "You are a Chief Compliance Officer preparing a regulatory audit report. Structure the report exactly as follows:\n"
            "1. **COMPLIANCE EXECUTIVE SUMMARY**: Overall compliance risk warning rating (High/Medium/Low Risk) and summary.\n"
            "2. **LITIGATION & INTELLECTUAL PROPERTY AUDIT**: Detail copyright disputes, historical judgements (e.g. GMAC/ETS case), damages paid, and policy gaps.\n"
            "3. **REGULATORY POLICY & SHIFT IMPACTS**: Analyze the impact of private education regulation changes on business transformation.\n"
            "4. **TAX COMPLIANCE & PFIC STATUS DISCLOSURE**: Detail the PFIC classification, tests (asset/income tests), and IRS implications for US investors.\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        ),
        "quick": (
            "You are a Senior Investment Analyst providing a high-speed brief for executive leadership (CEO/CFO). Structure the report exactly as follows:\n"
            "1. **EXECUTIVE ACTIONS & RECOMMENDATIONS**: One-sentence core thesis.\n"
            "2. **KEY FINANCIAL HIGHLIGHTS**: Bullet points of key revenue growth and margins.\n"
            "3. **IMMINENT RISK ALERTS**: Two major risk issues that cannot be ignored.\n"
            "4. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    }
    target_query = query_text_map.get(analysis_type, "Analyze this report")
    
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
        
        # We enforce a high semantic similarity (e.g. Cosine > 0.98)
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
    target_lang = lang_map.get(language, "English")
    logger.info(f"Router directed query: {analysis_type} | Language: {target_lang}")

    # Node 2: Retriever Node (Milvus Parent-Child Search with Query Decomposition)
    logger.info("LangGraph [Retriever Node] executing parent-child similarity search...")
    get_milvus_connection()
    collection = Collection("stock_analysis_chunks")
    
    # Decompose query into sub-queries to ensure all facets are covered
    sub_queries = [
        target_query,
        "新东方核心教育业务转型与营收业绩表现 东方甄选直播电商与与辉同行剥离",
        "新东方历史知识产权纠纷 ETS GMAC 侵权诉讼判决与合规政策漏洞",
        "新东方 PFIC 被动外国投资公司状态 资产测试 收入测试 美国投资者税务影响 长期投资公允价值变动 Level 3 资产减值"
    ]
    
    query_vectors = []
    for sq in sub_queries:
        try:
            emb_res = client.models.embed_content(
                model="gemini-embedding-2",
                contents=sq,
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            query_vectors.append(emb_res.embeddings[0].values)
        except Exception as e:
            logger.error(f"Embedding sub-query '{sq}' failed: {e}")

    if not query_vectors:
        query_vectors = [query_vector]
        
    retrieved_items = []
    seen_parents = set()
    for q_vec in query_vectors:
        search_res = collection.search(
            data=[q_vec],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=3,
            expr=f"document_id == {doc_id}",
            output_fields=["page_number", "parent_text", "child_text"]
        )
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

    # Node 3: Generator & Auditor Agent (Model Cascade: Flash -> Pro)
    logger.info("LangGraph [Auditor & Generator Node] executing Model Cascade...")
    language_instruction = (
        f"IMPORTANT: The user has selected {target_lang} as their preferred language. "
        f"You MUST generate the entire report in {target_lang}. Use professional financial terminology."
    )
    
    struct_instructions = ""
    if analysis_type == "comprehensive":
        struct_instructions = (
            "You are a Lead Equity Research Analyst preparing an institutional-grade investment memorandum for executive leadership. The report must be highly professional, avoiding generic filler, and structured exactly as follows:\n\n"
            "1. **EXECUTIVE SUMMARY & INVESTMENT THESIS**: State the rating (Buy/Hold/Sell) and the core justification.\n"
            "2. **FINANCIAL PERFORMANCE & TREND AUDIT**: Analyze revenues, margins, and cash flow trends. Use tables if appropriate.\n"
            "3. **KEY INVESTMENT RISKS & MITIGATION**: Graded analysis of regulatory, competitive, and operational risks.\n"
            "4. **VALUATION & CAPITAL STRUCTURE AUDIT**: Deep dive into long-term investments, Level 3 asset valuations, and tax considerations (e.g., PFIC status).\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    elif analysis_type == "compliance":
        struct_instructions = (
            "You are a Chief Compliance Officer preparing a regulatory audit report. The report must be highly professional, focusing strictly on risks and compliance framework, and structured exactly as follows:\n\n"
            "1. **COMPLIANCE EXECUTIVE SUMMARY**: Overall compliance risk warning rating (High/Medium/Low Risk) and summary.\n"
            "2. **LITIGATION & INTELLECTUAL PROPERTY AUDIT**: Detail copyright disputes, historical judgements (e.g. GMAC/ETS case), damages paid, and policy gaps.\n"
            "3. **REGULATORY POLICY & SHIFT IMPACTS**: Analyze the impact of private education regulation changes on business transformation.\n"
            "4. **TAX COMPLIANCE & PFIC STATUS DISCLOSURE**: Detail the PFIC classification, tests (asset/income tests), and IRS implications for US investors.\n"
            "5. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )
    elif analysis_type == "quick":
        struct_instructions = (
            "You are a Senior Investment Analyst providing a high-speed brief for executive leadership (CEO/CFO). The brief must be extremely concise, bulleted, and structured exactly as follows:\n\n"
            "1. **EXECUTIVE ACTIONS & RECOMMENDATIONS**: One-sentence core rating and actionable recommendation.\n"
            "2. **KEY FINANCIAL HIGHLIGHTS**: Bullet points of key revenue growth and margins.\n"
            "3. **IMMINENT RISK ALERTS**: Two major risk issues that cannot be ignored.\n"
            "4. **CITATIONS / REFERENCES**: List all footnote citations sequentially."
        )

    final_prompt = (
        f"{language_instruction}\n\n"
        f"{target_query}\n\n"
        f"IMPORTANT PROFESSIONAL FINANCIAL REPORTING INSTRUCTIONS:\n"
        f"{struct_instructions}\n\n"
        f"STRICT CITATION CONSTRAINTS (CRITICAL FOR FAITHFULNESS):\n"
        f"- You MUST ONLY use the facts, figures, and page numbers present in the [RETRIEVED DATA] block below. Do NOT use your pre-trained memory or make up page numbers (like Page 33, 67, etc.) that are not in the [RETRIEVED DATA] below.\n"
        f"- DO NOT introduce any external regulatory codes, custom legal formulas, tax form numbers (like IRS Form 8621), or specific tax rates UNLESS they are explicitly written in the [RETRIEVED DATA] below. If they are not in the text, you must not include them.\n"
        f"- For every financial figure, percentage, rate, date, or specific claim, you MUST append a sequential superscript footnote indicator (e.g., <sup>1</sup>, <sup>2</sup>).\n"
        f"- Format every citation in the 'Citations / References' section exactly as: [Footnote Number] New Oriental Education & Technology Group Inc., Annual Report (Form 20-F) for the Fiscal Year Ended May 31, 2025, at Page [Number] (where [Number] MUST be one of the actual page numbers from the retrieved context below).\n\n"
        f"[RETRIEVED DATA FROM SEC 10-K FILING]:\n{retrieved_context}"
    )

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
    
    # Audit & Final Polish by DeepSeek (deepseek-chat) with Gemini fallback
    audit_prompt = (
        f"You are a Senior Financial Audit Agent. Review the following draft report against the original source context. "
        f"Ensure that all dates, financial numbers, margins, and page references match the source exactly. "
        f"Correct any misstatements or formatting gaps.\n\n"
        f"IMPORTANT CITATION AUDIT:\n"
        f"1. Make sure every single number, percentage, and date has a superscript footnote indicator (e.g., <sup>1</sup>, <sup>2</sup>).\n"
        f"2. Validate that NO page numbers other than those in the retrieved context are cited. Correct any hallucinated page numbers.\n"
        f"3. Strip out any external tax forms (such as IRS Form 8621), tax rates, or law details that are not explicitly present in the retrieved context to maintain 100% faithfulness.\n"
        f"4. Ensure the 'Citations / References' section at the end is present, sequential, and formatted exactly as:\n"
        f"   [Footnote Number] New Oriental Education & Technology Group Inc., Annual Report (Form 20-F) for the Fiscal Year Ended May 31, 2025, at Page [Number].\n\n"
        f"Output the final polished report in {target_lang}.\n\n"
        f"[SOURCE CONTEXT]:\n{retrieved_context}\n\n"
        f"[DRAFT REPORT]:\n{draft_result}"
    )
    
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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
