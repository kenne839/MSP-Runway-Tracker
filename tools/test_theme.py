import urllib.request
import json

raw = urllib.request.urlopen('http://192.168.0.213:8080/api/v1/office_replay_batch', timeout=5).read().decode('utf-8')
d = json.loads(raw)
minified = json.dumps(d, separators=(',', ':'))

target = 'effective_brightness":8}'
idx = minified.find(target)
print("Index of theme closing brace:", idx + len(target) - 1)
print("Total string up to theme closing brace len:", idx + len(target))

