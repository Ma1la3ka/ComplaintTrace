"""Puts the ComplaintTrace cases screen inside the voice app, so there is one place to look.
Lives in the natlas-voice folder. app.py calls register_trace(app) once.
Adds:  /cases (the page)   /api/trace/cases   /api/trace/override/<ref>
"""
import json
import os
import urllib.request

from flask import jsonify, request


def _call(path, payload=None):
    base = os.environ.get("COMPLAINTTRACE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("COMPLAINTTRACE_URL is not set")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=4))


def register_trace(app):
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