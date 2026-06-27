import re

def validate_jsx():
    with open("gateway/index.html", "r", encoding="utf-8") as f:
        content = f.read()
    
    # Extract the babel script block
    script_match = re.search(r'<script type="text/babel">(.*?)</script>', content, re.DOTALL)
    if not script_match:
        print("❌ Could not find Babel script block!")
        return
        
    js_code = script_match.group(1)
    
    # 1. Check basic brace balance
    braces_open = 0
    braces_stack = []
    lines = js_code.split("\n")
    
    for line_num, line in enumerate(lines, 1):
        for char_idx, char in enumerate(line):
            if char == "{":
                braces_open += 1
                braces_stack.append((line_num, char_idx))
            elif char == "}":
                braces_open -= 1
                if braces_stack:
                    braces_stack.pop()
                else:
                    print(f"❌ Extra closing brace '}}' found at line {line_num}, char {char_idx}")
                    return

    if braces_open != 0:
        print(f"❌ Unclosed braces! Count balance = {braces_open}. Unclosed stack items: {braces_stack[-5:]}")
        return
    else:
        print("✅ Braces are perfectly balanced!")

    # 2. Check basic HTML tag closure inside JS
    # We will search for unclosed div tags or syntax anomalies in return block
    # Match the main return block of App component
    app_return_match = re.search(r'return\s*\(\s*(<div.*)\s*\)\s*;', js_code, re.DOTALL)
    if app_return_match:
        jsx_body = app_return_match.group(1)
        # Let's count div open vs close tags
        div_open = len(re.findall(r'<div', jsx_body))
        div_close = len(re.findall(r'</div>', jsx_body))
        print(f"Div tags: <div count={div_open}, </div> count={div_close}")
        if div_open != div_close:
            print("❌ Div tag mismatch!")
            return
            
    print("✅ JSX syntax pre-validation completed!")

if __name__ == "__main__":
    validate_jsx()
