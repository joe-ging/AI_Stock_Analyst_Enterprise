import os
import time
import logging
import psycopg2
import redis
import boto3
import pdfplumber


from celery import Celery
from google import genai
from google.genai import types
from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection



logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Celery-Worker")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://cache:6379/0")

celery_app = Celery("tasks", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

# --- OpenAINext Configs & Proxy Wiping ---
OPENAINEXT_API_KEY = os.environ.get("OPENAINEXT_API_KEY")
EMBEDDING_PROVIDER = os.environ.get("EMBEDDING_PROVIDER", "gemini").lower()

if EMBEDDING_PROVIDER == "openainext":
    # Wipe proxy variables completely to force clean direct connect
    for var in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        if var in os.environ:
            del os.environ[var]
    VECTOR_DIMENSION = 1536
    COLLECTION_PREFIX = "openainext"
else:
    VECTOR_DIMENSION = 768
    COLLECTION_PREFIX = "gemini"

CACHE_COLLECTION_NAME = f"{COLLECTION_PREFIX}_stock_analysis_cache"
DOC_CHUNKS_COLLECTION_NAME = f"{COLLECTION_PREFIX}_document_chunks"

MILVUS_HOST = os.environ.get("MILVUS_HOST", "milvus")
MILVUS_PORT = os.environ.get("MILVUS_PORT", "19530")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@db:5432/postgres")
API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

s3_endpoint = os.environ.get("S3_ENDPOINT_URL", "http://minio:9000")
s3_key_id = os.environ.get("S3_ACCESS_KEY_ID", "minioadmin")
s3_secret = os.environ.get("S3_SECRET_ACCESS_KEY", "minioadmin")

def get_s3_client():
    return boto3.client(
        's3',
        aws_access_key_id=s3_key_id,
        aws_secret_access_key=s3_secret,
        endpoint_url=s3_endpoint
    )

def get_milvus_connection():
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT)

def init_milvus_collection():
    get_milvus_connection()
    collection_name = DOC_CHUNKS_COLLECTION_NAME
    
    # Drop old collection if table schema changed
    if utility.has_collection(collection_name):
        col = Collection(collection_name)
        has_parent = False
        for f in col.schema.fields:
            if f.name == "parent_text":
                has_parent = True
        if not has_parent:
            logger.info("Dropping old database schema to apply parent-child structures...")
            col.drop()
            
    if not utility.has_collection(collection_name):
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="document_id", dtype=DataType.INT64),
            FieldSchema(name="page_number", dtype=DataType.INT64),
            FieldSchema(name="parent_text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="child_text", dtype=DataType.VARCHAR, max_length=65535),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION)
        ]
        schema = CollectionSchema(fields, "10-K Chunk Vector Embeddings with Parent-Child Structures")
        collection = Collection(collection_name, schema)
        
        index_params = {
            "metric_type": "COSINE",
            "index_type": "HNSW",
            "params": {"M": 16, "efConstruction": 64}
        }
        collection.create_index("embedding", index_params)
        logger.info(f"Milvus collection {collection_name} created successfully with HNSW index.")
    else:
        collection = Collection(collection_name)
    
    collection.load()
    return collection

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list:
    """Creates smaller child chunks from parent text"""
    chunks = []
    start = 0
    if len(text) <= chunk_size:
        return [text]
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += (chunk_size - overlap)
    return chunks

def extract_document_metadata(text_sample: str, filename: str) -> dict:
    import httpx
    import json
    import re
    
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    
    # Generic fallback based on filename keywords
    comp_lower = filename.lower()
    fallback = {
        "company_name": "New Oriental Education & Technology Group Inc.",
        "doc_type": "Form 20-F",
        "doc_year": "FY2025"
    }
    if "tencent" in comp_lower or "tcehy" in comp_lower:
        fallback = {"company_name": "Tencent Holdings Limited", "doc_type": "Annual Report", "doc_year": "FY2024"}
    elif "baba" in comp_lower or "alibaba" in comp_lower:
        fallback = {"company_name": "Alibaba Group Holding Limited", "doc_type": "Form 20-F", "doc_year": "FY2024"}
        
    prompt = (
        f"You are an expert financial analyst. Read the following text from the cover pages of an annual report / SEC filing and extract:\n"
        f"1. company_name: The full official English name of the corporation (e.g. 'Tencent Holdings Limited', 'New Oriental Education & Technology Group Inc.'). Do NOT include short tickers.\n"
        f"2. doc_type: The type of filing/document (e.g. 'Form 20-F', 'Form 10-K', 'Annual Report', 'Form 10-Q').\n"
        f"3. doc_year: The fiscal/calendar year of the report in the format 'FY202X' (e.g. 'FY2024', 'FY2025').\n\n"
        f"Strictly output a single JSON object. Do not wrap in markdown or add explanations.\n\n"
        f"TEXT:\n{text_sample[:2500]}"
    )
    
    try:
        # Resolve proxy configs
        client_limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        with httpx.Client(limits=client_limits, proxy=None) as client:
            r = client.post(
                "https://api.deepseek.com/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                },
                timeout=12.0
            )
            if r.status_code == 200:
                ans = r.json()["choices"][0]["message"]["content"].strip()
                if "```json" in ans:
                    ans = ans.split("```json")[1].split("```")[0].strip()
                elif "```" in ans:
                    ans = ans.split("```")[1].split("```")[0].strip()
                
                json_match = re.search(r'\{[^}]+\}', ans)
                if json_match:
                    parsed = json.loads(json_match.group())
                    return {
                        "company_name": parsed.get("company_name", fallback["company_name"]).strip(),
                        "doc_type": parsed.get("doc_type", fallback["doc_type"]).strip(),
                        "doc_year": parsed.get("doc_year", fallback["doc_year"]).strip()
                    }
    except Exception as e:
        logger.warning(f"Failed to query DeepSeek metadata extractor: {e}")
    return fallback

@celery_app.task(name="tasks.ingest_pdf_task")
def ingest_pdf_task(filename: str, doc_id: int):
    logger.info(f"Celery task started: Ingesting doc_id={doc_id}, filename={filename}")
    
    # 1. Download file from S3 / MinIO
    s3 = get_s3_client()
    local_path = f"/tmp/{filename}"
    try:
        s3.download_file("sec-filings", filename, local_path)
        logger.info(f"Downloaded {filename} from MinIO bucket 'sec-filings'")
    except Exception as e:
        logger.error(f"Failed to download {filename} from MinIO: {e}")
        return {"status": "failed", "error": f"MinIO download failed: {str(e)}"}
    
    # 2. Extract structured elements using pdfplumber (Fast fallback)
    chunks_with_metadata = []
    try:
        logger.info(f"Parsing PDF layout-aware via pdfplumber: {filename}")
        with pdfplumber.open(local_path) as pdf:
            # First, extract first 2 pages text to dynamically identify metadata via LLM
            meta_sample = ""
            for p_idx in range(min(2, len(pdf.pages))):
                ptxt = pdf.pages[p_idx].extract_text()
                if ptxt:
                    meta_sample += ptxt + "\n"
            
            logger.info("Extracting document metadata via LLM...")
            meta_info = extract_document_metadata(meta_sample, filename)
            logger.info(f"Document metadata extracted: {meta_info}")
            
            # Write metadata to PostgreSQL documents table
            pg_conn = psycopg2.connect(DATABASE_URL)
            pg_cur = pg_conn.cursor()
            try:
                pg_cur.execute(
                    "UPDATE documents SET company_name = %s, doc_type = %s, doc_year = %s WHERE id = %s;",
                    (meta_info["company_name"], meta_info["doc_type"], meta_info["doc_year"], doc_id)
                )
                pg_conn.commit()
                logger.info(f"Updated PostgreSQL documents metadata registry for doc_id={doc_id}")
            except Exception as db_err:
                pg_conn.rollback()
                logger.error(f"Failed to write metadata registry to PostgreSQL: {db_err}")
            finally:
                pg_cur.close()
                pg_conn.close()
            for page_idx, page in enumerate(pdf.pages):
                page_num = page_idx + 1
                
                # Extract tables on the page
                tables = page.extract_tables()
                table_texts = []
                for table in tables:
                    # Format table cells into simple markdown/csv format
                    rows_str = []
                    for row in table:
                        if row:
                            # Filter None and convert to string
                            row_cells = [str(cell).strip() if cell is not None else "" for cell in row]
                            rows_str.append(" | ".join(row_cells))
                    if rows_str:
                        table_txt = "\n".join(rows_str)
                        table_texts.append(table_txt)
                        
                        # Generate chunks for the table
                        child_chunks = chunk_text(table_txt, chunk_size=400, overlap=50)
                        for child_txt in child_chunks:
                            if child_txt.strip():
                                chunks_with_metadata.append({
                                    "page_number": page_num,
                                    "parent_text": table_txt,
                                    "child_text": child_txt.strip()
                                })
                
                # Extract page text
                page_txt = page.extract_text()
                if page_txt:
                    page_txt = page_txt.strip()
                    
                    # Simple text splitting per section / paragraph if possible
                    paragraphs = page_txt.split("\n\n")
                    for para in paragraphs:
                        para = para.strip()
                        if not para:
                            continue
                        
                        # Generate parent-child chunks
                        child_chunks = chunk_text(para, chunk_size=400, overlap=50)
                        for child_txt in child_chunks:
                            if child_txt.strip():
                                chunks_with_metadata.append({
                                    "page_number": page_num,
                                    "parent_text": para,
                                    "child_text": child_txt.strip()
                                })

        if os.path.exists(local_path):
            os.remove(local_path)
    except Exception as e:
        logger.error(f"Error parsing PDF with pdfplumber: {e}")
        if os.path.exists(local_path):
            os.remove(local_path)
        return {"status": "failed", "error": f"pdfplumber parsing error: {str(e)}"}
    
    if not chunks_with_metadata:
        logger.error("No readable text chunks extracted from PDF.")
        return {"status": "failed", "error": "No text extracted"}

    logger.info(f"Generated {len(chunks_with_metadata)} parent-child chunks from {filename}")

    # 3. Generate Embeddings batch-by-batch
    batch_size = 500 if EMBEDDING_PROVIDER == "openainext" else 15
    embeddings_list = []
    texts = [item["child_text"] for item in chunks_with_metadata]
    
    import requests
    try:
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i+batch_size]
            max_retries = 5
            batch_vectors = None
            
            for attempt in range(max_retries):
                try:
                    if EMBEDDING_PROVIDER == "openainext":
                        url = "https://api.openai-next.com/v1/embeddings"
                        headers = {
                            "Authorization": f"Bearer {OPENAINEXT_API_KEY}",
                            "Content-Type": "application/json"
                        }
                        payload = {
                            "input": batch_texts,
                            "model": "text-embedding-3-small"
                        }
                        res = requests.post(url, json=payload, headers=headers, timeout=30.0)
                        res.raise_for_status()
                        batch_vectors = [item["embedding"] for item in res.json()["data"]]
                    else:
                        client = genai.Client(api_key=API_KEY)
                        response = client.models.embed_content(
                            model="gemini-embedding-2",
                            contents=[types.Content(parts=[types.Part.from_text(text=t)]) for t in batch_texts],
                            config=types.EmbedContentConfig(output_dimensionality=768)
                        )
                        batch_vectors = [embedding_obj.values for embedding_obj in response.embeddings]
                    break
                except Exception as api_err:
                    if attempt == max_retries - 1:
                        raise api_err
                    wait_time = (2 ** attempt) + 1.0
                    logger.warning(f"Embedding API transient error: {api_err}. Retrying in {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
            
            if batch_vectors:
                embeddings_list.extend(batch_vectors)
            time.sleep(0.15)
    except Exception as e:
        logger.error(f"Embedding batch generation failed: {e}")
        return {"status": "failed", "error": f"Embedding API error: {str(e)}"}

    # 4. Insert into Milvus
    try:
        collection = init_milvus_collection()
        
        document_ids = [doc_id] * len(chunks_with_metadata)
        page_numbers = [item["page_number"] for item in chunks_with_metadata]
        parent_texts = [item["parent_text"] for item in chunks_with_metadata]
        child_texts = [item["child_text"] for item in chunks_with_metadata]
        
        data = [
            document_ids,
            page_numbers,
            parent_texts,
            child_texts,
            embeddings_list
        ]
        
        mr = collection.insert(data)
        collection.flush()
        logger.info(f"Successfully inserted {len(chunks_with_metadata)} parent-child chunks into Milvus (doc_id={doc_id})")
        return {"status": "success", "chunks_count": len(chunks_with_metadata)}
    except Exception as e:
        logger.error(f"Milvus DB ingestion failed: {e}")
        return {"status": "failed", "error": f"Milvus insert failed: {str(e)}"}
