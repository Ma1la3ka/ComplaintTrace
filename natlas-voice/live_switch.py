"""'Wake the AI on request' support. Lives in the natlas-voice folder, next to app.py.

The web app can stay online all the time (for example on Render) while the GPU server only runs when needed.
- The GPU notebook announces its new tunnel address to the app when it starts (no redeploy needed).
- The page shows whether the AI is online and, if not, gives WhatsApp / email buttons to ask for a live session.

Environment variables:
  ADMIN_KEY          secret shared with the notebook (required for the notebook to announce its address)
  CONTACT_WHATSAPP   digits with country code, e.g. 2348012345678
  CONTACT_EMAIL      address for the email button
  AI_HOST_ALLOWLIST  comma-separated host suffixes the notebook may announce (default: trycloudflare.com)

Why the allowlist: the app sends callers' audio and text to whatever address is announced. If ADMIN_KEY ever leaked,
an unrestricted address would let a stranger collect that data, so only the tunnel provider's hosts are accepted.
"""
import hmac
import os
import time
from urllib.parse import urlparse

import requests
from flask import jsonify, request

_CACHE = {"at": 0.0, "online": False}
_READY = set()                                             # stores whose settings table already exists


def _ensure_table(store):
    if id(store) in _READY:
        return
    if hasattr(store, "_run"):                             # MySQL / TiDB
        store._run("CREATE TABLE IF NOT EXISTS settings (k VARCHAR(64) PRIMARY KEY, v TEXT) CHARACTER SET utf8mb4")
    else:                                                  # SQLite
        store.conn.execute("CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT)")
        store.conn.commit()
    _READY.add(id(store))


def _setting_get(store, key):
    _ensure_table(store)
    if hasattr(store, "_run"):
        row = store._run("SELECT v FROM settings WHERE k=%s", (key,), "one")
    else:
        row = store.conn.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return row["v"] if row else None


def _setting_set(store, key, value):
    _ensure_table(store)
    if hasattr(store, "_run"):
        store._run("INSERT INTO settings (k, v) VALUES (%s, %s) ON DUPLICATE KEY UPDATE v=VALUES(v)", (key, value))
    else:
        store.conn.execute("INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
        store.conn.commit()


def allowed_address(url):
    """Only https addresses on an allowed host (the host itself or a subdomain of it)."""
    if not url or len(url) > 200:
        return False
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    suffixes = [s.strip().lower() for s in os.environ.get("AI_HOST_ALLOWLIST", "trycloudflare.com").split(",") if s.strip()]
    return any(host == s or host.endswith("." + s) for s in suffixes)


def register_live_switch(app, client, store):
    # If you edit NATLAS_URL in .env and restart, that new address wins over an older announced one.
    # (Otherwise an old announced address stored in the database would silently override your edit.)
    env_url = (os.environ.get("NATLAS_URL") or "").strip().rstrip("/")
    if env_url and _setting_get(store, "natlas_env_seen") != env_url:
        _setting_set(store, "natlas_url", env_url)
        _setting_set(store, "natlas_env_seen", env_url)

    @app.before_request
    def _use_latest_ai_address():
        if request.path.startswith("/api/sessions") and hasattr(client, "base"):
            url = _setting_get(store, "natlas_url")
            if url:
                client.base = url.rstrip("/")

    @app.post("/api/admin/natlas-url")
    def set_ai_address():
        admin = os.environ.get("ADMIN_KEY", "")
        sent = request.headers.get("X-Admin-Key", "")
        if not admin or not hmac.compare_digest(sent.encode(), admin.encode()):
            return jsonify({"error": "forbidden"}), 403
        url = ((request.get_json(silent=True) or {}).get("url") or "").strip().rstrip("/")
        if not allowed_address(url):
            return jsonify({"error": "url must be an https address on an allowed host"}), 400
        _setting_set(store, "natlas_url", url)
        _CACHE["at"] = 0.0
        return jsonify({"ok": True})

    @app.get("/api/status")
    def ai_status():
        contact = {"whatsapp": os.environ.get("CONTACT_WHATSAPP", ""), "email": os.environ.get("CONTACT_EMAIL", "")}
        if time.time() - _CACHE["at"] > 8:                 # check at most every 8 seconds
            base = _setting_get(store, "natlas_url") or getattr(client, "base", "")
            online = False
            if base:
                try:
                    online = requests.get(base.rstrip("/") + "/health", timeout=4).ok
                except requests.RequestException:
                    online = False
            _CACHE.update(at=time.time(), online=online)
        return jsonify({"online": _CACHE["online"], "contact": contact})