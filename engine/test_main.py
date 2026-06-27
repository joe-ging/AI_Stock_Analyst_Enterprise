import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

# Mock environmental dependencies before importing main app
with patch('psycopg2.connect'), patch('pymilvus.connections.connect'), patch('redis.from_url'):
    from main import app
    from tasks import chunk_text

client = TestClient(app)

def test_chunk_text():
    # Test short text (less than chunk_size)
    text = "Short text"
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert chunks == ["Short text"]

    # Test long text splitting
    text = "This is a longer text that should be split into multiple chunks because it exceeds the limit."
    chunks = chunk_text(text, chunk_size=20, overlap=5)
    assert len(chunks) > 1
    # Check that chunks are substrings of the original text
    for c in chunks:
        assert c in text

@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
def test_health_endpoint(mock_milvus_conn, mock_db_conn):
    # Mock DB cursor execution
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["database"] == "connected"
    assert data["milvus"] == "connected"

@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
@patch('main.utility.has_collection')
@patch('main.Collection')
def test_ingest_document_cache_hit(mock_collection_cls, mock_has_collection, mock_milvus_conn, mock_db_conn):
    # Mock database to return existing document ID
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = [100]  # doc_id = 100
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # Mock Milvus checks to simulate existing vectors (cache hit)
    mock_has_collection.return_value = True
    mock_collection = MagicMock()
    # Return some existing vector representation
    mock_collection.query.return_value = [{"id": 1}]
    mock_collection_cls.return_value = mock_collection

    # Trigger POST ingest
    file_content = b"PDF dummy content"
    response = client.post(
        "/ingest",
        files={"file": ("dummy.pdf", file_content, "application/pdf")}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"
    assert data["document_id"] == 100

@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
@patch('main.init_cache_collection')
@patch('main.Collection')
def test_query_endpoint(mock_collection_cls, mock_init_cache, mock_milvus_conn, mock_db_conn):
    # 1. Mock Database to return doc_id = 100
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = [100]
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # 2. Mock Gemini Embeddings Client
    mock_emb_res = MagicMock()
    mock_emb_res.embeddings = [MagicMock(values=[0.1] * 768)]
    
    # 3. Mock Milvus Chunks Search Results
    mock_chunk = MagicMock()
    mock_chunk.entity.get.side_effect = lambda field: {
        "page_number": 5,
        "parent_text": "Company revenues grew by 15% due to robust sales.",
        "child_text": "revenues grew by 15%"
    }[field]
    
    mock_collection = MagicMock()
    mock_collection.search.return_value = [[mock_chunk]]
    mock_collection_cls.return_value = mock_collection
    
    # Cache collection mock to simulate cache miss
    mock_cache_col = MagicMock()
    mock_cache_col.search.return_value = []
    mock_init_cache.return_value = mock_cache_col

    # 4. Mock Gemini Content Generation (Cascade)
    mock_gen_res = MagicMock()
    mock_gen_res.text = "Based on our analysis, the company's revenues grew by 15."

    # Patch the main.client and main.redis_client
    with patch('main.DEEPSEEK_API_KEY', None), \
         patch('main.client.models.embed_content', return_value=mock_emb_res), \
         patch('main.client.models.generate_content', return_value=mock_gen_res), \
         patch('main.redis_client') as mock_redis:
        
        # Simulate Cache Miss (Redis returns None)
        mock_redis.get.return_value = None

        # Trigger POST /query as Form Data
        response = client.post(
            "/query",
            data={
                "filename": "dummy.pdf",
                "analysis_type": "comprehensive",
                "language": "zh_cn"
            }
        )
        
        # 5. Assertions
        assert response.status_code == 200
        data = response.json()
        assert data["cache_hit"] is False
        assert "grew by 15" in data["analysis"]
        assert len(data["citations"]) > 0
        assert data["citations"][0]["chunk_index"] == 5
        assert "revenues grew by 15%" in data["citations"][0]["text"]


@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
@patch('main.init_cache_collection')
@patch('main.Collection')
@patch('main.call_deepseek')
def test_query_endpoint_deepseek_succeeds(mock_call_ds, mock_collection_cls, mock_init_cache, mock_milvus_conn, mock_db_conn):
    # 1. Mock DB
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = [100]
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # 2. Mock Embeddings
    mock_emb_res = MagicMock()
    mock_emb_res.embeddings = [MagicMock(values=[0.1] * 768)]

    # 3. Mock Milvus Chunks Search Results
    mock_chunk = MagicMock()
    mock_chunk.entity.get.side_effect = lambda field: {
        "page_number": 3,
        "parent_text": "Sample text",
        "child_text": "Sample"
    }[field]
    mock_collection = MagicMock()
    mock_collection.search.return_value = [[mock_chunk]]
    mock_collection_cls.return_value = mock_collection
    
    mock_cache_col = MagicMock()
    mock_cache_col.search.return_value = []
    mock_init_cache.return_value = mock_cache_col

    # 4. Mock DeepSeek Success Response
    mock_call_ds.return_value = "This is DeepSeek report response"

    # Set DeepSeek key to mock active
    with patch('main.DEEPSEEK_API_KEY', 'some_key'), \
         patch('main.client.models.embed_content', return_value=mock_emb_res), \
         patch('main.client.models.generate_content', side_effect=Exception("Should not be called")), \
         patch('main.redis_client') as mock_redis:
        
        mock_redis.get.return_value = None

        response = client.post(
            "/query",
            data={
                "filename": "dummy.pdf",
                "analysis_type": "comprehensive",
                "language": "en"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "DeepSeek report response" in data["analysis"]
        assert mock_call_ds.call_count == 2

@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
@patch('main.init_cache_collection')
@patch('main.Collection')
@patch('main.call_deepseek')
def test_query_endpoint_both_fail(mock_call_ds, mock_collection_cls, mock_init_cache, mock_milvus_conn, mock_db_conn):
    # 1. Mock DB
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = [100]
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # 2. Mock Embeddings
    mock_emb_res = MagicMock()
    mock_emb_res.embeddings = [MagicMock(values=[0.1] * 768)]

    # 3. Mock Milvus Chunks Search Results
    mock_chunk = MagicMock()
    mock_chunk.entity.get.side_effect = lambda field: {
        "page_number": 3,
        "parent_text": "Sample text",
        "child_text": "Sample"
    }[field]
    mock_collection = MagicMock()
    mock_collection.search.return_value = [[mock_chunk]]
    mock_collection_cls.return_value = mock_collection
    
    mock_cache_col = MagicMock()
    mock_cache_col.search.return_value = []
    mock_init_cache.return_value = mock_cache_col

    # 4. Mock DeepSeek Failure
    mock_call_ds.side_effect = Exception("DeepSeek rate limited")

    local_client = TestClient(app, raise_server_exceptions=False)
    with patch('main.DEEPSEEK_API_KEY', 'some_key'), \
         patch('main.client.models.embed_content', return_value=mock_emb_res), \
         patch('main.client.models.generate_content', side_effect=Exception("Gemini failed")), \
         patch('main.redis_client') as mock_redis:
        
        mock_redis.get.return_value = None

        # Verify that error is propagated as a server error
        response = local_client.post(
            "/query",
            data={
                "filename": "dummy.pdf",
                "analysis_type": "comprehensive",
                "language": "en"
            }
        )
        assert response.status_code == 500


@patch('main.get_db_connection')
@patch('main.get_milvus_connection')
@patch('main.init_cache_collection')
@patch('main.Collection')
@patch('main.call_deepseek')
def test_query_endpoint_deepseek_fails_gemini_succeeds(mock_call_ds, mock_collection_cls, mock_init_cache, mock_milvus_conn, mock_db_conn):
    # 1. Mock DB
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = [100]
    mock_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # 2. Mock Embeddings
    mock_emb_res = MagicMock()
    mock_emb_res.embeddings = [MagicMock(values=[0.1] * 768)]

    # 3. Mock Milvus Chunks Search Results
    mock_chunk = MagicMock()
    mock_chunk.entity.get.side_effect = lambda field: {
        "page_number": 3,
        "parent_text": "Sample text",
        "child_text": "Sample"
    }[field]
    mock_collection = MagicMock()
    mock_collection.search.return_value = [[mock_chunk]]
    mock_collection_cls.return_value = mock_collection
    
    mock_cache_col = MagicMock()
    mock_cache_col.search.return_value = []
    mock_init_cache.return_value = mock_cache_col

    # 4. Mock DeepSeek Failure
    mock_call_ds.side_effect = Exception("DeepSeek connection timed out")

    # 5. Mock Gemini Success Response (Cascade)
    mock_gen_res = MagicMock()
    mock_gen_res.text = "This is Gemini report response"

    # Set DeepSeek key to mock active
    with patch('main.DEEPSEEK_API_KEY', 'some_key'), \
         patch('main.client.models.embed_content', return_value=mock_emb_res), \
         patch('main.client.models.generate_content', return_value=mock_gen_res), \
         patch('main.redis_client') as mock_redis:
        
        mock_redis.get.return_value = None

        response = client.post(
            "/query",
            data={
                "filename": "dummy.pdf",
                "analysis_type": "comprehensive",
                "language": "en"
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "Gemini report response" in data["analysis"]
        assert mock_call_ds.call_count == 2



