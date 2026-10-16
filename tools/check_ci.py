import urllib.request
import json
import time

def check():
    req = urllib.request.Request(
        'https://api.github.com/repos/kenne839/MSP-Runway-Tracker/actions/runs',
        headers={'User-Agent': 'Python'}
    )
    res = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    runs = res.get('workflow_runs', [])
    if runs:
        latest = runs[0]
        print(f"Run ID: {latest['id']} | Status: {latest['status']} | Conclusion: {latest['conclusion']} | Commit: {latest['head_sha'][:7]} | URL: {latest['html_url']}")
        
        # Check artifacts
        art_req = urllib.request.Request(latest['artifacts_url'], headers={'User-Agent': 'Python'})
        arts = json.loads(urllib.request.urlopen(art_req, timeout=10).read().decode())
        for a in arts.get('artifacts', []):
            print(f"  Artifact: {a['name']} ({a['size_in_bytes']} bytes)")

if __name__ == '__main__':
    check()
