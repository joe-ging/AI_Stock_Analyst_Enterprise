import httpx
import sys
import time

def test_health():
    print("=== Testing Health Endpoint ===")
    try:
        resp = httpx.get("http://localhost:80/health", timeout=10.0)
        print(f"Status Code: {resp.status_code}")
        print(f"Response Body: {resp.text}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "healthy" and data.get("engine_connection", {}).get("status") == "healthy":
                print("✅ Gateway and Engine are healthy!")
                return True
        print("❌ Health check mismatch or unhealthy status.")
        return False
    except Exception as e:
        print(f"❌ Failed to reach Gateway Health Endpoint: {e}")
        return False

def test_stream_analysis():
    print("\n=== Testing Stream Analysis Flow ===")
    url = "http://localhost:80/analyze/stream"
    try:
        # Load the real PDF from local directory
        with open("FY2025 Annual Report_20-F.pdf", "rb") as f:
            pdf_bytes = f.read()
    except Exception as e:
        print(f"❌ Failed to load local test PDF: {e}")
        return False
        
    files = {"file": ("FY2025 Annual Report_20-F.pdf", pdf_bytes, "application/pdf")}
    data = {"analysis_type": "quick", "language": "zh_cn"}
    
    start_time = time.time()
    first_token_time = None
    chunks_received = 0
    
    try:
        with httpx.stream("POST", url, files=files, data=data, timeout=60.0) as r:
            print(f"Response Status: {r.status_code}")
            print(f"Headers: {dict(r.headers)}")
            if r.status_code != 200:
                print(f"❌ Streaming failed with code {r.status_code}")
                # Try reading body
                r.read()
                print(f"Error details: {r.text}")
                return False
                
            for line in r.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    chunks_received += 1
                    if first_token_time is None:
                        first_token_time = time.time() - start_time
                        print(f"⏱️ First streaming chunk received in {first_token_time:.2f} seconds!")
                    
                    # Print first few chunks
                    if chunks_received <= 10:
                        print(f"Chunk {chunks_received}: {line}")
            
            print(f"\n✅ Streaming finished. Total chunks: {chunks_received}")
            return chunks_received > 0
            
    except Exception as e:
        print(f"❌ Error during streaming: {e}")
        return False

if __name__ == "__main__":
    health_ok = test_health()
    stream_ok = test_stream_analysis()
    
    if health_ok and stream_ok:
        print("\n🎉 ALL TESTS PASSED! The server is fully operational and streaming correctly.")
        sys.exit(0)
    else:
        print("\n🚨 TESTS FAILED! Please inspect logs.")
        sys.exit(1)
