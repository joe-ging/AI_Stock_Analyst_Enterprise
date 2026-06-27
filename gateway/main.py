import os
import logging
import httpx
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

# --- 0. Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("API-Gateway")

ENGINE_URL = os.environ.get("ENGINE_URL", "http://engine:8001")

app = FastAPI()

# --- 1. CORS Configuration ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 2. Serve Static Frontend ---
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(index_path):
        return HTMLResponse("<h2>index.html missing on Gateway</h2>", status_code=404)
    with open(index_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(html_content)

@app.get("/health")
async def health():
    # Ping the engine service
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(f"{ENGINE_URL}/health")
            engine_status = resp.json() if resp.status_code == 200 else f"error_code_{resp.status_code}"
    except Exception as e:
        engine_status = f"failed_to_reach_engine: {str(e)}"

    return {
        "status": "healthy",
        "engine_connection": engine_status
    }

@app.post("/analyze")
async def analyze_document(
    file: UploadFile = File(...), 
    analysis_type: str = Form(...),
    language: str = Form(...)
):
    logger.info(f"Gateway received request: {file.filename} | Type: {analysis_type} | Lang: {language}")

    async with httpx.AsyncClient(timeout=600.0, trust_env=False) as client:
        # Step 1: Forward file to engine for RAG ingestion
        try:
            file_bytes = await file.read()
            files = {"file": (file.filename, file_bytes, file.content_type)}
            
            logger.info("Forwarding file to engine for ingestion...")
            ingest_resp = await client.post(f"{ENGINE_URL}/ingest", files=files)
            
            if ingest_resp.status_code != 200:
                logger.error(f"Engine ingestion failed: {ingest_resp.text}")
                raise HTTPException(
                    status_code=ingest_resp.status_code, 
                    detail=f"Engine Ingestion Failed: {ingest_resp.text}"
                )
            
            logger.info("Ingestion complete. Proceeding to RAG query...")
        except httpx.RequestError as e:
            logger.error(f"Error connecting to RAG Engine: {e}")
            raise HTTPException(status_code=502, detail=f"Cannot reach RAG Engine: {str(e)}")

        # Step 2: Query the RAG engine
        try:
            query_data = {
                "filename": file.filename,
                "analysis_type": analysis_type,
                "language": language
            }
            query_resp = await client.post(f"{ENGINE_URL}/query", data=query_data)
            
            if query_resp.status_code != 200:
                logger.error(f"Engine query failed: {query_resp.text}")
                raise HTTPException(
                    status_code=query_resp.status_code, 
                    detail=f"Engine Query Failed: {query_resp.text}"
                )
            
            return query_resp.json()
        except httpx.RequestError as e:
            logger.error(f"Error querying RAG Engine: {e}")
            raise HTTPException(status_code=502, detail=f"Query to RAG Engine failed: {str(e)}")

@app.post("/analyze/stream")
async def analyze_document_stream(
    file: UploadFile = File(...),
    analysis_type: str = Form(...),
    language: str = Form(...)
):
    """Streaming endpoint — ingests then streams SSE tokens from Engine"""
    logger.info(f"Gateway streaming request: {file.filename} | Type: {analysis_type} | Lang: {language}")

    # Step 1: Ingest (non-streaming, must complete first)
    async with httpx.AsyncClient(timeout=600.0, trust_env=False) as client:
        try:
            file_bytes = await file.read()
            files = {"file": (file.filename, file_bytes, file.content_type)}
            ingest_resp = await client.post(f"{ENGINE_URL}/ingest", files=files)
            if ingest_resp.status_code != 200:
                raise HTTPException(status_code=ingest_resp.status_code, detail=f"Ingestion Failed: {ingest_resp.text}")
            logger.info("Ingestion complete. Starting streaming query...")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Cannot reach RAG Engine: {str(e)}")

    # Step 2: Stream from Engine's /query/stream endpoint
    async def stream_passthrough():
        async with httpx.AsyncClient(timeout=600.0, trust_env=False) as client:
            query_data = {"filename": file.filename, "analysis_type": analysis_type, "language": language}
            async with client.stream("POST", f"{ENGINE_URL}/query/stream", data=query_data) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    yield f"data: {error_body.decode()}\\n\\n"
                    return
                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n\n"

    return StreamingResponse(stream_passthrough(), media_type="text/event-stream")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

