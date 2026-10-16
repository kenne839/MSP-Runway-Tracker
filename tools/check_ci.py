import urllib.request
import json
import time

def check():
    req = urllib.request.Request(
        'https://api.github.com/repos/kenne839/MSP-Runway-Tracker/actions/runs/38855913652',
        headers={'User-Agent': 'Python'}
    )
    r = json.loads(urllib.request.urlopen(req, timeout=10).read().decode())
    print(f"Run ID: {r['id']} | Status: {r['status']} | Conclusion: {r['conclusion']} | Updated: {r['updated_at']}")

    # Check jobs
    jobs_req = urllib.request.Request(r['jobs_url'], headers={'User-Agent': 'Python'})
    jobs = json.loads(urllib.request.urlopen(jobs_req, timeout=10).read().decode())
    for j in jobs.get('jobs', []):
        print(f"  Job: {j['name']} | Status: {j['status']} | Conclusion: {j['conclusion']}")
        for step in j.get('steps', []):
            if step['status'] == 'in_progress':
                print(f"    -> In Progress Step: {step['name']}")

    # Check artifacts
    art_req = urllib.request.Request(r['artifacts_url'], headers={'User-Agent': 'Python'})
    arts = json.loads(urllib.request.urlopen(art_req, timeout=10).read().decode())
    for a in arts.get('artifacts', []):
        print(f"  Artifact: {a['name']} ({a['size_in_bytes']} bytes)")

if __name__ == '__main__':
    check()

