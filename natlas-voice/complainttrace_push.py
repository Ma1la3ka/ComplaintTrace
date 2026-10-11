"""Sends a finished call to ComplaintTrace and returns its answer. Lives in the natlas-voice folder, next to app.py.

Does nothing (returns None) unless the environment variable COMPLAINTTRACE_URL is set, e.g. http://127.0.0.1:8000,
so the voice app behaves exactly as before when ComplaintTrace is not running.
Only the saved fields are sent: reference, language, status, reason and slots. Never the transcript or audio.
If TRACE_KEY is set it is sent as a header, so ComplaintTrace can refuse strangers.
A failure is printed and ignored, so it can never break a caller's session.
"""
import json
import os
import threading
import time
import urllib.request
from datetime import datetime

_LAST_WAKE = [0.0]


def trace_headers():
    h = {"Content-Type": "application/json"}
    if os.environ.get("TRACE_KEY"):
        h["X-Trace-Key"] = os.environ["TRACE_KEY"]
    return h


def wake():
    """Free hosting puts ComplaintTrace to sleep when idle and it needs about a minute to wake. Knock on its door in the
    background (at most once a minute) so it is already awake by the time a call ends."""
    url = os.environ.get("COMPLAINTTRACE_URL")
    if not url or time.time() - _LAST_WAKE[0] < 60:
        return
    _LAST_WAKE[0] = time.time()

    def knock():
        try:
            urllib.request.urlopen(url.rstrip("/") + "/health", timeout=90).read()
        except Exception:
            pass
    threading.Thread(target=knock, daemon=True).start()


def push_if_done(session):
    url = os.environ.get("COMPLAINTTRACE_URL")
    if not url or session.status == "active":
        return None
    body = json.dumps({
        "ref": session.ref, "language": session.language, "status": session.status,
        "reason": session.reason, "slots": session.slots,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url.rstrip("/") + "/ingest", data=body, headers=trace_headers())
        return json.load(urllib.request.urlopen(req, timeout=12))
    except Exception as e:
        print("ComplaintTrace unreachable (the call is unaffected):", e)
        return None