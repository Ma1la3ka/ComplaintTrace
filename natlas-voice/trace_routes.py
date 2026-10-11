"""Staff screens and protection for the voice app. Lives in the natlas-voice folder. app.py calls register_trace(app).

Adds:  /cases (the page)   /api/trace/cases   /api/trace/override/<ref>
Protects the staff pages with a password when STAFF_PASSWORD is set (the browser shows a sign-in box; any user name works):
  /cases   /console   /api/trace/*   /api/handoffs   /api/complaints
Callers' own pages (/ and /api/sessions...) stay open. If STAFF_PASSWORD is not set, nothing is protected (fine on your own computer).
Also wakes a sleeping ComplaintTrace when someone opens the page or starts a call.
"""
import hmac
import json
import os
import urllib.request

from flask import Response, jsonify, request

from complainttrace_push import trace_headers, wake

STAFF_PATHS = ("/cases", "/console", "/api/trace/", "/api/handoffs", "/api/complaints")


def _call(path, payload=None):
    base = os.environ.get("COMPLAINTTRACE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("COMPLAINTTRACE_URL is not set")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, headers=trace_headers())
    return json.load(urllib.request.urlopen(req, timeout=15))


def register_trace(app):
    @app.before_request
    def _staff_password_and_wake():
        path = request.path
        if path == "/api/status" or (path == "/api/sessions" and request.method == "POST"):
            wake()
        password = os.environ.get("STAFF_PASSWORD", "")
        if password and (path == "/cases" or path == "/console" or path.startswith(STAFF_PATHS[2:])):
            given = request.authorization.password if request.authorization and request.authorization.password else ""
            if not hmac.compare_digest(given.encode(), password.encode()):
                return Response("Staff sign-in required.", 401, {"WWW-Authenticate": 'Basic realm="Staff"'})

    @app.get("/cases")
    def cases_page():
        return app.send_static_file("cases.html")

    @app.get("/api/trace/cases")
    def trace_cases():
        try:
            return jsonify(_call("/cases"))
        except Exception as e:
            return jsonify({"error": f"ComplaintTrace unreachable: {e}"}), 502

    @app.post("/api/trace/override/<ref>")
    def trace_override(ref):
        try:
            return jsonify(_call(f"/cases/{ref}/override", request.get_json(silent=True) or {}))
        except Exception as e:
            return jsonify({"error": f"ComplaintTrace unreachable: {e}"}), 502