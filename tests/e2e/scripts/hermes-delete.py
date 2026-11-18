#!/usr/bin/env python3
"""Real scheduler gate for deletion: a schedule fires into its space, an outage keeps the space, retry finishes."""
import json, os, signal, subprocess, time, urllib.request, uuid
from pathlib import Path

BASE = "http://127.0.0.1:8720"
TOKEN = Path("~/.cache/amsg/hermes-device-token").expanduser().read_text().strip()
HOME = os.path.expanduser("~/.cache/amsg/hermes-home")
HERMES = os.path.expanduser("~/.cache/amsg/hermes-venv/bin/hermes")
ENV = {**os.environ, "HERMES_HOME": HOME}

def api(path, body=None):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(request, timeout=60))

def cli(*args):
    return subprocess.run([HERMES, *args], env=ENV, capture_output=True, text=True, timeout=90).stdout

def create(title, schedule):
    space = api("/v1/spaces", {"title": title, "kind": "thread", "icon": {"kind": "catalog", "value": "clock"}})
    out = cli("cron", "create", schedule, "Reply with exactly: tick", "--name", title, "--deliver", f"amsg:{space['id']}")
    job_id = next(line.split()[-1] for line in out.splitlines() if line.startswith("Created job"))
    return space, job_id

def synced(job_id):
    start = time.time()
    while time.time() - start < 130:
        found = [j for j in api("/v1/automations") if j["provider_job_id"] == job_id]
        if found:
            return found[0]
        time.sleep(2)
    raise SystemExit(f"{job_id} never synced")

def delete(space, policy):
    fresh = next(s for s in api("/v1/spaces") if s["id"] == space["id"])
    return api(f"/v1/spaces/{space['id']}/delete",
        {"policy": policy, "expected_revision": fresh["revision"], "idempotency_key": str(uuid.uuid4())})

def wait(operation, *states, limit=70):
    start = time.time()
    while time.time() - start < limit:
        state = api(f"/v1/operations/{operation}")
        if state["state"] in states:
            return state
        time.sleep(0.5)
    raise SystemExit(f"operation stayed {state['state']}")

print("== a short schedule fires into its space, unattended", flush=True)
space, job = create("Tick space", "every 1m")
start = time.time()
while time.time() - start < 170:
    items = api(f"/v1/spaces/{space['id']}/messages")["items"]
    if items:
        print(f"fired after {time.time() - start:.0f}s:", repr(items[-1]["markdown"][:60]), "meta:", items[-1]["meta"], flush=True)
        break
    time.sleep(3)
else:
    print("did not fire within 170s", flush=True)
cli("cron", "pause", job)
link = synced(job)
print("linked:", link["space_id"] == space["id"], "timezone:", link["timezone"], flush=True)

print("== scheduler disconnected during deletion keeps the space", flush=True)
gateway = int(subprocess.run("ps -eo pid,args | grep -E '[h]ermes gateway run' | awk '{print $1}' | head -1", shell=True, capture_output=True, text=True).stdout)
os.kill(gateway, signal.SIGTERM)
while Path(f"/proc/{gateway}").exists():
    time.sleep(0.3)
operation = delete(space, "stop_and_delete")
failed = wait(operation["id"], "failed", "space_deleted")
print("state:", failed["state"], "|", failed["error_message"], flush=True)
print("space kept:", any(s["id"] == space["id"] for s in api("/v1/spaces")), flush=True)
print("job still on Hermes:", job in cli("cron", "list"), flush=True)

print("== retry after reconnect completes without touching other jobs", flush=True)
log = open(os.environ["GATEWAY_LOG"], "a")
before = open(f"{HOME}/logs/gateway.log").read().count("Turn machinery warmed")
subprocess.Popen([HERMES, "gateway", "run"], env=ENV, stdout=log, stderr=log, start_new_session=True)
while open(f"{HOME}/logs/gateway.log").read().count("Turn machinery warmed") <= before:
    time.sleep(1)
keeper, keep_job = create("Keeper space", "every 6h")
synced(keep_job)
api(f"/v1/operations/{operation['id']}/retry", {})
done = wait(operation["id"], "space_deleted", "failed")
print("state:", done["state"], "| deleted job gone:", job not in cli("cron", "list"), "| other job kept:", keep_job in cli("cron", "list"), flush=True)

print("== keep automation and move results to General", flush=True)
move = delete(keeper, "keep_and_move")
done = wait(move["id"], "space_deleted", "failed")
general = next(s for s in api("/v1/spaces") if s["kind"] == "general")
listing = cli("cron", "list")
print("state:", done["state"], "| job kept:", keep_job in listing, "| delivers to General:", f"amsg:{general['id']}" in listing, flush=True)
print("space gone:", not any(s["id"] == keeper["id"] for s in api("/v1/spaces")), flush=True)
cli("cron", "remove", keep_job)
print("cleanup: jobs left", cli("cron", "list").count("[active]") + cli("cron", "list").count("[paused]"), flush=True)
