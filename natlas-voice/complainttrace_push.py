"""Sends a finished call to ComplaintTrace and returns its answer. Lives in the natlas-voice folder, next to app.py.

Does nothing (returns None) unless the environment variable COMPLAINTTRACE_URL is set, e.g. http://127.0.0.1:8000,
so the voice app behaves exactly as before when ComplaintTrace is not running.
Only the saved fields are sent: reference, language, status, reason and slots. Never the transcript or audio.
A failure is printed and ignored, so it can never break a caller's session.
"""
import json
import os
import urllib.request
from datetime import datetime


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
        req = urllib.request.Request(url.rstrip("/") + "/ingest", data=body,
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=4))
    except Exception as e:
        print("ComplaintTrace unreachable (the call is unaffected):", e)
        return None