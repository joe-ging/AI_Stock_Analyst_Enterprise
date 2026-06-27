import asyncio
import os
import sys

# Force proxy configuration internally inside Python process to redirect through the 1088 socat gateway
os.environ["HTTP_PROXY"] = "socks5h://host.docker.internal:1088"
os.environ["HTTPS_PROXY"] = "socks5h://host.docker.internal:1088"
os.environ["ALL_PROXY"] = "socks5h://host.docker.internal:1088"

# Change directory context so python can find modules
sys.path.append("/app")

from main import (
    get_db_connection, 
    get_milvus_connection, 
    build_final_prompt, 
    call_deepseek, 
    REPORT_TEMPLATES, 
    GENERIC_SUB_QUERIES,
    client
)
from pymilvus import connections, Collection
from google.genai import types

# Ensure config environment variables
os.environ["DEEPSEEK_API_KEY"] = "sk-8c8956265b5f482bb32c1fc6c8878d72"

async def generate_all():
    # Setup connection
    connections.connect("default", host="milvus", port="19530")
    
    filename = "FY2025 Annual Report_20-F.pdf"
    
    # 1. Fetch doc_id from PostgreSQL
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents WHERE filename = %s ORDER BY id DESC LIMIT 1;", (filename,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    
    if not row:
        print("Error: Document not found in DB.")
        return
    doc_id = row[0]
    print(f"Using doc_id: {doc_id} for document {filename}")
    
    analysis_types = ["comprehensive", "compliance", "quick"]
    languages = ["en", "zh_cn", "zh_hk"]
    
    # We will retrieve contexts for each analysis type because each has different prompt queries
    for atype in analysis_types:
        template = REPORT_TEMPLATES[atype]
        target_query = template["query"]
        struct_instructions = template["struct"]
        
        # Parallel sub-query embedding via asyncio
        sub_queries = [target_query] + GENERIC_SUB_QUERIES
        
        print(f"\nEmbedding sub-queries for analysis type: {atype}...")
        
        async def embed_single(sq: str):
            try:
                # Direct SDK call
                emb_res = client.models.embed_content(
                    model="gemini-embedding-2",
                    contents=sq,
                    config=types.EmbedContentConfig(output_dimensionality=768)
                )
                return emb_res.embeddings[0].values
            except Exception as e:
                sys.stderr.write(f"Embedding failed for '{sq[:30]}...': {e}\n")
                sys.stderr.flush()
                return None

        query_vectors = []
        for sq in sub_queries:
            vec = await embed_single(sq)
            if vec is not None:
                query_vectors.append(vec)
            
        if not query_vectors:
            print("Error: Failed to embed any queries. Aborting this analysis type.")
            continue
            
        collection = Collection("stock_analysis_chunks")
        collection.load()
        
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
                            "parent_text": parent_txt
                        })
        
        retrieved_context = ""
        for item in retrieved_items:
            page = item["page_number"]
            parent_txt = item["parent_text"]
            retrieved_context += f"\n--- [Page {page}] ---\n{parent_txt}\n"

        lang_map = {
            "en": "English",
            "zh_cn": "Simplified Chinese (简体中文)",
            "zh_hk": "Traditional Chinese (繁體中文)"
        }
        
        for lang in languages:
            target_lang = lang_map[lang]
            print(f"\n=======================================================")
            print(f"REPORT TYPE: {atype.upper()} | LANGUAGE: {target_lang.upper()}")
            print(f"=======================================================\n")
            
            final_prompt = build_final_prompt(target_query, struct_instructions, retrieved_context, target_lang, filename)
            
            # Direct Call DeepSeek
            try:
                report_content = call_deepseek(final_prompt, model="deepseek-chat")
                sys.stderr.write(f"\n--- GENERATED REPORT ---\n{report_content}\n")
                sys.stderr.flush()
                
                # Append to file
                with open("/app/reports_output.md", "a", encoding="utf-8") as f:
                    f.write(f"\n# REPORT TYPE: {atype.upper()} | LANGUAGE: {target_lang.upper()}\n")
                    f.write(report_content)
                    f.write("\n\n---\n")
            except Exception as e:
                sys.stderr.write(f"DeepSeek call failed: {e}\n")
                sys.stderr.flush()

if __name__ == "__main__":
    asyncio.run(generate_all())
