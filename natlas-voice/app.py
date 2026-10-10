import os
from envfile import load_env
load_env()                                    # reads the .env file next to this one; the terminal still overrides it

from flask import Flask, jsonify, request
from natlas_client import NatlasClient, NatlasError
from dialogue import DialogueEngine, Config, Session, LANGUAGES, handoff_summary
from db import Store, make_store
from complainttrace_push import push_if_done
from trace_routes import register_trace
from live_switch import register_live_switch


def create_app(client=None, store=None, cfg=None):
    app = Flask(__name__, static_folder="static", static_url_path="")
    register_trace(app)
    client = client or NatlasClient(os.environ["NATLAS_URL"], os.environ["NATLAS_KEY"])
    store = store or make_store()
    cfg = cfg or Config(escalate_amount=float(os.environ.get("ESCALATE_AMOUNT", 500000)))
    engine = DialogueEngine(client, cfg)

    def view(s: Session):
        return {"session_id": s.id, "ref": s.ref, "status": s.status, "reason": s.reason, "slots": s.slots}

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/console")
    def console():
        return app.send_static_file("console.html")

    @app.get("/api/health")
    def health():
        try:
            return jsonify({"app": "ok", "natlas": client.health()})
        except Exception as e:
            return jsonify({"app": "ok", "natlas": "unreachable", "detail": str(e)}), 503

    @app.post("/api/sessions")
    def start():
        language = (request.get_json(silent=True) or {}).get("language")
        if language not in LANGUAGES:
            return jsonify({"error": "language must be one of yo, ha, ig, en"}), 400
        s = Session(language=language)
        try:
            en, local = engine.greeting(language)
        except NatlasError as e:
            return jsonify({"error": str(e)}), 502
        store.save(s)
        store.add_turn(s.id, "assistant", local, en)
        return jsonify({**view(s), "reply": local, "reply_en": en})

    @app.post("/api/sessions/<sid>/turn")
    def turn(sid):
        s = store.load(sid)
        if not s:
            return jsonify({"error": "unknown session"}), 404
        if s.status != "active":
            return jsonify({"error": f"session is {s.status}"}), 409
        try:
            if "audio" in request.files:
                f = request.files["audio"]
                data = f.read()
                if os.environ.get("DEBUG_AUDIO"):
                    os.makedirs("debug_audio", exist_ok=True)
                    with open(f"debug_audio/{s.id}_{s.turns}.webm", "wb") as out:
                        out.write(data)
                transcript = client.transcribe(data, s.language, f.filename or "clip.webm")
            else:
                transcript = (request.get_json(silent=True) or {}).get("text", "")  # typed fallback
            result = engine.process_turn(s, transcript)
        except NatlasError as e:
            return jsonify({"error": str(e)}), 502
        store.add_turn(s.id, "caller", result.transcript)
        store.add_turn(s.id, "assistant", result.reply_local, result.reply_en)
        store.save(s)

        trace = push_if_done(s)
        reply, reply_en = result.reply_local, result.reply_en
        if trace and trace.get("decision") == "auto_resolved":
            reply = reply_en = f"Thank you. Your reference number is {s.ref}. {trace['caller_message']}"
        elif trace:
            reply = f"{reply}\n\n{trace['caller_message']}"
            reply_en = f"{reply_en}\n\n{trace['caller_message']}"
        return jsonify({**view(s), "transcript": result.transcript, "trace": trace,
                        "reply": reply, "reply_en": reply_en})

    @app.get("/api/sessions/<sid>")
    def get_session(sid):
        s = store.load(sid)
        if not s:
            return jsonify({"error": "unknown session"}), 404
        return jsonify({**view(s), "turns": store.turns(sid)})

    @app.get("/api/handoffs")
    def handoffs():
        out = []
        for s in store.by_status("escalated") + store.by_status("review"):
            callers = [t["text"] for t in store.turns(s.id) if t["role"] == "caller"]
            out.append({**view(s), "summary": handoff_summary(s, callers)})
        return jsonify(out)

    @app.get("/api/complaints")
    def complaints():
        return jsonify([view(s) for s in store.by_status("logged") + store.by_status("escalated") + store.by_status("review")])

    register_live_switch(app, client, store)  # the AI online/offline bar and the notebook's address announcement
    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)