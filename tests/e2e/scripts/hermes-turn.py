#!/usr/bin/env python3
"""Sends one message to a Hermes-backed a-msg service and prints the outcome. usage: hermes-turn.py SPACE TEXT"""
import json, sys, time, urllib.request, uuid
from pathlib import Path

BASE = "http://127.0.0.1:8720"
TOKEN = Path("~/.cache/amsg/hermes-device-token").expanduser().read_text().strip()

def api(path, body=None):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(request, timeout=30))

spaces = api("/v1/spaces")
space = next(s for s in spaces if s["kind"] == "general" or s["title"] == sys.argv[1]) if sys.argv[1] == "General" else next(s for s in spaces if s["title"] == sys.argv[1])
receipt = api(f"/v1/spaces/{space['id']}/messages", {"client_message_id": str(uuid.uuid4()), "markdown": sys.argv[2]})
run = receipt["run"]["id"]
start = time.time()
last = None
while time.time() - start < 100:
    items = api(f"/v1/spaces/{space['id']}/messages?limit=50")["items"]
    reply = next((m for m in reversed(items) if m["run_id"] == run and m["role"] == "agent"), None)
    state = (reply or {}).get("status")
    if state != last:
        print(f"{time.time() - start:5.1f}s {state}")
        last = state
    if state in ("completed", "failed", "cancelled", "delivery_uncertain"):
        print("reply:", reply["markdown"][:700])
        print("error:", reply.get("error_message"), "artifacts:", [a["display_name"] for a in reply["artifacts"]])
        break
    time.sleep(0.5)
else:
    print("timed out at", last)
print("spaces:", [s["title"] for s in api("/v1/spaces")])
