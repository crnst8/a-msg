#!/usr/bin/env python3
"""Real scheduler gate: drives a Hermes job through a-msg and verifies each change on Hermes itself."""
import json, subprocess, sys, time, urllib.error, urllib.request, os
from pathlib import Path

BASE = "http://127.0.0.1:8720"
TOKEN = Path("~/.cache/amsg/hermes-device-token").expanduser().read_text().strip()
HERMES = os.path.expanduser("~/.cache/amsg/hermes-venv/bin/hermes")
ENV = {**os.environ, "HERMES_HOME": os.path.expanduser("~/.cache/amsg/hermes-home")}

def api(path, body=None):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request, timeout=60))

def on_hermes():
    return subprocess.run([HERMES, "cron", "list"], env=ENV, capture_output=True, text=True, timeout=60).stdout

def act(link, action, **extra):
    try:
        result = api(f"/v1/automations/{link}/actions", {"action": action, **extra})
        print(f"{action}: state={result['state']} space={result['space_id']}")
        return result
    except urllib.error.HTTPError as error:
        print(f"{action}: HTTP {error.code} {error.read()[:200]}")
        raise

start = time.time()
while time.time() - start < 130:
    jobs = api("/v1/automations")
    if jobs:
        break
    time.sleep(2)
job = jobs[0]
print(f"synced after {time.time() - start:.0f}s:", {k: job[k] for k in ("name", "schedule", "state", "next_run_at", "timezone", "stale")})
spaces = {s["title"]: s["id"] for s in api("/v1/spaces")}
print("linked to Harbour notes:", job["space_id"] == spaces["Harbour notes"])

act(job["id"], "pause")
print("hermes shows paused:", "[paused]" in on_hermes())
act(job["id"], "resume")
print("hermes shows active:", "[active]" in on_hermes())

before = len(api(f"/v1/spaces/{spaces['Harbour notes']}/messages")["items"])
act(job["id"], "run_now")
start = time.time()
while time.time() - start < 150:
    items = api(f"/v1/spaces/{spaces['Harbour notes']}/messages")["items"]
    if len(items) > before:
        print(f"delivered after {time.time() - start:.0f}s into Harbour notes:", repr(items[-1]["markdown"][:120]), "author:", items[-1]["author"])
        break
    time.sleep(2)
else:
    print("no delivery within 150s")
general_before = len(api(f"/v1/spaces/{spaces['General']}/messages")["items"])

act(job["id"], "redirect", destination_space_id=spaces["General"])
print("hermes deliver now General:", f"amsg:{spaces['General']}" in on_hermes())
act(job["id"], "remove")
print("hermes job gone:", job["provider_job_id"] not in on_hermes())
print("a-msg automations:", api("/v1/automations"))
