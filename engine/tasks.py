import os
import time
import logging
import psycopg2
import redis
import boto3


from celery import Celery
from google import genai
from google.genai import types
from pymilvus import connections, utility, FieldSchema, CollectionSchema, DataType, Collection
from docling.document_converter import DocumentConverter

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Celery-Worker")

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", "redis://cache:6379/0")

celery_app = Celery("tasks", broker=CELERY_BROKER_URL, backend=CELERY_RESULT_BACKEND)

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
    collection_name = "stock_analysis_chunks"
    
    # Drop old collection if table schema changed
    if utility.has_collection(collection_name):
        # We drop the collection to recreate it with the parent-child fields
        col = Collection(collection_name)
        # Check if schema contains parent_text, if not, drop it
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
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=768)
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
    import pdfplumber
    try:
        logger.info(f"Parsing PDF layout-aware via pdfplumber: {filename}")
        with pdfplumber.open(local_path) as pdf:
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
    client = genai.Client(api_key=API_KEY)
    batch_size = 15
    embeddings_list = []
    
    try:
        # Embed child chunks for indexing
        for idx in range(0, len(chunks_with_metadata), batch_size):
            batch = chunks_with_metadata[idx:idx+batch_size]
            texts = [item["child_text"] for item in batch]
            
            response = client.models.embed_content(
                model="gemini-embedding-2",
                contents=[types.Content(parts=[types.Part.from_text(text=t)]) for t in texts],
                config=types.EmbedContentConfig(output_dimensionality=768)
            )
            
            for embedding_obj in response.embeddings:
                embeddings_list.append(embedding_obj.values)
            
            time.sleep(0.1)
    except Exception as e:
        logger.error(f"Gemini embedding batch generation failed: {e}")
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
