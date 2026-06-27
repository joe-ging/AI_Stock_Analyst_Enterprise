import re

def locate_mismatches():
    with open("gateway/index.html", "r", encoding="utf-8") as f:
        content = f.read()
        
    script_match = re.search(r'<script type="text/babel">(.*?)</script>', content, re.DOTALL)
    if not script_match:
        print("No script block")
        return
        
    js_code = script_match.group(1)
    lines = js_code.split("\n")
    
    open_divs = []
    
    for idx, line in enumerate(lines, 1):
        pos = 0
        while True:
            open_pos = line.find("<div", pos)
            close_pos = line.find("</div>", pos)
            
            if open_pos == -1 and close_pos == -1:
                break
                
            if open_pos != -1 and (close_pos == -1 or open_pos < close_pos):
                if not line[open_pos:open_pos+15].count("/>"):
                    open_divs.append((idx, line.strip()))
                pos = open_pos + 4
            else:
                if open_divs:
                    open_divs.pop()
                else:
                    print(f"Extra closing div on line {idx}: {line.strip()}")
                pos = close_pos + 6
                
    print("\n--- Remaining Unclosed Div Stack ---")
    for l_num, text in open_divs:
        print(f"Line {l_num}: {text[:80]}")

if __name__ == "__main__":
    locate_mismatches()
