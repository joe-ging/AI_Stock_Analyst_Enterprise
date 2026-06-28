import httpx
import json
import time

def simulate_frontend_js_stream():
    # --- 🟢 完全还原前端 gateway/index.html 中的 API_URL ---
    API_URL = "http://localhost:80/analyze/stream"
    
    # 模拟真实上传
    print("🚀 [JS Sim] Initiating fetch(API_URL, { method: 'POST', body: formData })...")
    
    files = {"file": ("FY2025 Annual Report_20-F.pdf", open("FY2025 Annual Report_20-F.pdf", "rb"), "application/pdf")}
    data = {"analysis_type": "quick", "language": "zh_cn"}
    
    try:
        # 1. 模拟 fetch 的 ReadableStream 连接
        with httpx.stream("POST", API_URL, files=files, data=data, timeout=60.0) as response:
            print(f"📡 [JS Sim] Response status: {response.status_code}")
            if response.status_code != 200:
                print(f"❌ [JS Sim] API Error: {response.status_code}")
                return False
                
            print("🟢 [JS Sim] response.body.getReader() established. Reading chunks...")
            print("\n" + "="*40 + " FRONTEND SCREEN OUTPUT " + "="*40)
            
            full_text = ""
            citations = []
            inference_time = None
            
            # 2. 模拟前端 while (!done) { const { value } = await reader.read() } 循环
            for line in response.iter_lines():
                if not line:
                    continue
                    
                # 3. 模拟 js: const trimmed = line.trim()
                trimmed = line.strip()
                
                # 4. 模拟 js: if (trimmed.startsWith("data: "))
                if trimmed.startswith("data: "):
                    try:
                        # 5. 模拟 js: const rawJson = trimmed.slice(6); const parsed = JSON.parse(rawJson);
                        raw_json = trimmed[6:]
                        parsed = json.loads(raw_json)
                        
                        # 6. 模拟 js: if (parsed.type === "token" && parsed.content) { fullText += parsed.content; setResult(fullText); }
                        if parsed.get("type") == "token" and parsed.get("content"):
                            token = parsed["content"]
                            full_text += token
                            # 实时在终端流式打出，模拟网页的即时渲染
                            print(token, end="", flush=True)
                            
                        # 7. 模拟 js: else if (parsed.type === "done")
                        elif parsed.get("type") == "done":
                            citations = parsed.get("citations", [])
                            inference_time = parsed.get("inference_time_ms")
                            print("\n" + "="*100)
                            print(f"\n🎉 [JS Sim] Stream DONE event received!")
                            print(f"⏱️ [JS Sim] Inference Latency: {inference_time}ms")
                            print(f"📚 [JS Sim] Citations retrieved: {len(citations)}")
                            
                    except Exception as e:
                        print(f"\n⚠️ [JS Sim] JSON Parse Error on line: {trimmed} | Err: {e}")
                        
            print("="*104)
            return len(full_text) > 0
            
    except Exception as e:
        print(f"\n❌ [JS Sim] Network Fetch Failed: {e}")
        return False

if __name__ == "__main__":
    success = simulate_frontend_js_stream()
    if success:
        print("\n✅ Front-end JS streaming logic verified successfully!")
    else:
        print("\n🚨 Front-end JS streaming logic test failed!")
