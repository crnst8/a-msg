import json

JOBS: dict[str, dict] = {}


def cronjob(action: str, job_id: str | None = None, deliver: str | None = None, include_disabled: bool = False, **_):
    if action == "list":
        return json.dumps({"success": True, "jobs": list(JOBS.values())})
    if job_id not in JOBS:
        return json.dumps({"success": False, "error": "Job not found"})
    if action == "pause":
        JOBS[job_id]["state"] = "paused"
    elif action == "resume":
        JOBS[job_id]["state"] = "scheduled"
    elif action == "update":
        JOBS[job_id]["deliver"] = deliver
    elif action == "remove":
        del JOBS[job_id]
    return json.dumps({"success": True})
