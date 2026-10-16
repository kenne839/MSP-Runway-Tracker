import urllib.request
import json

raw = urllib.request.urlopen('http://192.168.0.213:8080/api/v1/office_replay_batch', timeout=5).read().decode('utf-8')
data_dict = json.loads(raw)
minified = json.dumps(data_dict, separators=(',', ':'))

s_rx_head = 0
s_brace_depth = 0
s_in_string = False
s_escape = False

print(f"Total minified length: {len(minified)}")

for i, c in enumerate(minified):
    s_rx_head += 1
    if s_escape:
        s_escape = False
    elif c == '\\':
        if s_in_string:
            s_escape = True
    elif c == '"':
        s_in_string = not s_in_string
    elif not s_in_string:
        if c == '{':
            s_brace_depth += 1
        elif c == '}':
            s_brace_depth -= 1
            if s_brace_depth == 0:
                print(f"Hit depth 0 at idx {i} (head={s_rx_head})")
                snippet = minified[max(0, i-30):min(len(minified), i+30)]
                print(f"  Snippet: {snippet}")

print(f"Final depth: {s_brace_depth}, in_string: {s_in_string}")
