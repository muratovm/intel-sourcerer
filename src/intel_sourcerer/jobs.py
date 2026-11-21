"""Bounded background jobs for long-running per-user discovery and playlist imports."""

from pathlib import Path
import threading
import time
import uuid

import httpx

JOB_RETENTION_SECONDS = 3600
MAX_JOBS = 500

_lock = threading.Lock()
_jobs = {}


class JobConflictError(Exception):
    """Raised when a user already has an active discovery/import job running."""


def _owner(database):
    return str(Path(database).resolve())


def _prune(now=None):
    now = time.monotonic() if now is None else now
    cutoff = now - JOB_RETENTION_SECONDS
    stale = [
        job_id
        for job_id, item in _jobs.items()
        if item["state"] != "running" and item["_at"] < cutoff
    ]
    for job_id in stale:
        _jobs.pop(job_id, None)
    if len(_jobs) > MAX_JOBS:
        terminal = sorted(
            (item["_at"], job_id)
            for job_id, item in _jobs.items()
            if item["state"] != "running"
        )
        for _, job_id in terminal[: len(_jobs) - MAX_JOBS]:
            _jobs.pop(job_id, None)


def _public(item):
    payload = {"id": item["id"], "kind": item["kind"], "state": item["state"]}
    if item["state"] == "done":
        payload["result"] = item["result"]
    elif item["state"] == "failed":
        payload["error"] = item["error"]
    return payload


def start_job(database, kind, user_lock, work):
    """Run work() on a background thread under user_lock; returns immediately with the job descriptor."""
    if not user_lock.acquire(blocking=False):
        raise JobConflictError("An article fetch is already running. Try again shortly.")
    job_id = uuid.uuid4().hex
    owner = _owner(database)
    entry = {
        "id": job_id,
        "owner": owner,
        "kind": kind,
        "state": "running",
        "result": None,
        "error": None,
        "_at": time.monotonic(),
    }
    with _lock:
        _prune()
        _jobs[job_id] = entry

    def run():
        try:
            result = work()
            with _lock:
                entry["state"] = "done"
                entry["result"] = result
                entry["_at"] = time.monotonic()
        except (ValueError, httpx.HTTPError) as exc:
            with _lock:
                entry["state"] = "failed"
                entry["error"] = str(exc)
                entry["_at"] = time.monotonic()
        except Exception:
            with _lock:
                entry["state"] = "failed"
                entry["error"] = "Background import failed."
                entry["_at"] = time.monotonic()
        finally:
            user_lock.release()

    threading.Thread(target=run, name=f"job-{kind}", daemon=True).start()
    return _public(entry)


def get_job(database, job_id):
    """Return a job's public state if it exists and belongs to this user's database."""
    if not isinstance(job_id, str) or not job_id:
        return None
    owner = _owner(database)
    with _lock:
        _prune()
        entry = _jobs.get(job_id)
        if not entry or entry["owner"] != owner:
            return None
        return _public(entry)
