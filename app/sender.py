import threading

_jobs = {}
_seq = 0
_lock = threading.Lock()
_worker_alive = False
TERMINAL = {"done", "cancelled", "error"}


def enqueue(message_ids, dry_run=False):
    global _seq, _worker_alive
    ids = [int(m) for m in message_ids if m is not None]
    if not ids:
        return {"id": None, "status": "done", "total": 0, "done": 0, "results": [], "dry_run": bool(dry_run)}
    with _lock:
        _seq += 1
        job = {
            "id": _seq,
            "status": "queued",
            "cancel": False,
            "dry_run": bool(dry_run),
            "total": len(ids),
            "done": 0,
            "results": [],
            "messages": ids,
            "note": None,
        }
        _jobs[_seq] = job
        if not _worker_alive:
            _worker_alive = True
            threading.Thread(target=_worker, daemon=True).start()
    return _public(job)


def cancel(job_id):
    with _lock:
        job = _jobs.get(int(job_id))
        if not job or job["status"] in TERMINAL:
            return {"ok": False, "error": "job not found or already finished"}
        job["cancel"] = True
        return {"ok": True, "job_id": job["id"], "status": "cancelling"}


def list_jobs(limit=10):
    with _lock:
        recent = sorted(_jobs.values(), key=lambda j: j["id"])[-limit:]
        return [_public(j) for j in reversed(recent)]


def _public(job):
    summary = {}
    for r in job["results"]:
        key = r.get("outcome") or ("error" if r.get("error") else "unknown")
        summary[key] = summary.get(key, 0) + 1
    return {
        "id": job["id"],
        "status": job["status"],
        "dry_run": bool(job["dry_run"]),
        "total": job["total"],
        "done": job["done"],
        "cancelled": bool(job["cancel"]),
        "note": job["note"],
        "summary": summary,
        "results": job["results"],
    }


def _worker():
    global _worker_alive
    while True:
        with _lock:
            job = next(
                (j for j in _jobs.values() if j["status"] in ("queued", "sending")), None
            )
            if not job:
                _worker_alive = False
                return
        _run(job)


def _run(job):
    from .db import get_conn
    from . import gmail_send
    from .services.sending import SendError, SendSkipped, send_message, wait_for_interval

    messages = job["messages"]
    stop_reason = None
    # Reuse a single authenticated SMTP connection across the whole real-send
    # batch. Dry runs open no connection at all.
    session = None if job["dry_run"] else gmail_send.SmtpSession()
    try:
        for mid in messages:
            with _lock:
                if job["cancel"]:
                    job["status"] = "cancelled"
                    job["cancel"] = False
                    return
                job["status"] = "sending"

            if stop_reason is not None:
                # A cap/window stop halts the rest of the batch but records why.
                out = {"message_id": mid, "outcome": "skipped", "error": stop_reason}
            else:
                try:
                    conn = get_conn()
                    try:
                        # Throttle consecutive real sends. Dry runs are not paced.
                        if not job["dry_run"]:
                            wait_for_interval(conn)
                        result = send_message(conn, mid, job["dry_run"], session)
                    finally:
                        conn.close()
                    out = {"message_id": mid, **result}
                except SendSkipped as e:
                    out = {"message_id": mid, "outcome": "skipped", "error": str(e)}
                    if e.kind in ("cap", "window"):
                        stop_reason = str(e)
                        with _lock:
                            job["note"] = stop_reason
                except SendError as e:
                    outcome = "send_unknown" if e.ambiguous else "send_failed"
                    out = {"message_id": mid, "outcome": outcome, "error": str(e)}
                except Exception as e:  # unexpected -> record, never retry blindly
                    out = {"message_id": mid, "outcome": "send_unknown", "error": str(e)}

            with _lock:
                job["results"].append(out)
                job["done"] = len(job["results"])
    finally:
        if session is not None:
            session.close()

    with _lock:
        job["status"] = "cancelled" if job["cancel"] else "done"
        job["cancel"] = False
