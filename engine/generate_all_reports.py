import asyncio
import os
import sys
from main import _build_rag_context, _generate_report, get_db_connection, get_milvus_connection
from pymilvus import connections, Collection

# Ensure config environment variables
os.environ["DEEPSEEK_API_KEY"] = "sk-8c8956265b5f482bb32c1fc6c8878d72"
os.environ["GEMINI_API_KEY"] = "AIzaSy..." # Loaded from environment on container

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
    
    # 2. Setup standard facets for query context
    # Use standard facets to represent a thorough equity research analysis
    facets = [
        "Company overview, mission, history, core business segments, and main revenue drivers",
        "Financial performance, historical revenue growth, operating margins, EBITDA, and free cash flow trend",
        "Major operational risk factors, litigation, compliance issues, regulatory impact, and PFIC status",
        "Comparable valuation metrics, target price derivation, DCF model logic, and comparable comps multiple",
        "Management team profiles, corporate governance policies, executive compensation, and strategic vision"
    ]
    
    # 3. Retrieve RAG context via parallel sub-queries
    print("Retrieving semantic contexts from Milvus...")
    rag_context = await _build_rag_context(doc_id, facets)
    print(f"RAG Context retrieved. Total characters: {len(rag_context)}")
    
    # 4. Generate all 9 variants (3 types * 3 languages)
    analysis_types = ["comprehensive", "compliance", "quick"]
    languages = ["en", "zh_cn", "zh_tw"]
    
    for atype in analysis_types:
        for lang in languages:
            print(f"\n=========================================")
            print(f"REPORT TYPE: {atype.upper()} | LANGUAGE: {lang.upper()}")
            print(f"=========================================\n")
            
            # Run generator
            report = ""
            async for chunk in _generate_report(rag_context, atype, lang):
                report += chunk
                # Print output to terminal in real time
                sys.stdout.write(chunk)
                sys.stdout.flush()
            print("\n")

if __name__ == "__main__":
    asyncio.run(generate_all())
