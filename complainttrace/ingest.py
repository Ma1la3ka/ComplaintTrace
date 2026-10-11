"""ComplaintTrace live server. Receives finished voice calls, links each to a transaction, DECIDES what to do
(auto-resolve or hand to a human), and lets a human override any decision.

Run from the complainttrace folder (needs: pip install flask):
    python ingest.py            starts the server on http://127.0.0.1:8000
    python ingest.py --cases    prints three ready-to-speak demo calls taken from the dataset

Environment variables (all optional):
    DEMO_AS_OF   e.g. "2026-09-25 22:00". Treat every incoming call as filed at this moment. Demo switch: the synthetic
                 transaction log covers 14 Sep to 4 Oct 2026, not today, so without it nothing can match.
    CASES_DB     sqlite file (default cases.db)       PORT   default 8000       ESCALATE_AMOUNT   default 500000
Only the saved fields (issue, amount, when, bank, account) arrive here. The transcript never leaves the voice app.
All actions are simulated: no real bank system is connected.
"""
import csv, hmac, json, os, re, sqlite3, sys, threading
from datetime import datetime, timedelta
from linker import extract, link_facts, load_txns, FMT
from decide import decide
from reversals import due, policy_for, row_for, POLICY
from incidents import detect as detect_incidents, load_history

from envfile import load_env
load_env()                                    # reads .env next to this file; the terminal still overrides it

DEMO_AS_OF = os.environ.get("DEMO_AS_OF")
txns, ts_list, by_id = load_txns()
by_txn = {t["txn_id"]: t for t in txns}
FAILED_DEBITED = [t for t in txns if t["status"] != "SUCCESS" and t["debited"] == "1"]
HISTORY = load_history()          # dataset complaints already linked by linker.py (empty if links.csv is missing)
ALERTS = list(csv.DictReader(open("alerts.csv", encoding="utf-8"))) if os.path.exists("alerts.csv") else []


def alert_for(t):
    """Is this transaction part of an incident that detect.py already flagged?"""
    for a in ALERTS:
        if a["type"] != "failure_cluster" or a["label"] != t["error_code"] or not a["first_txn"]: continue
        start = datetime.strptime(a["first_txn"], FMT)
        if start - timedelta(hours=1) <= t["ts"] <= start + timedelta(hours=24): return a
    return None


def facts_from_call(slots):
    f = extract(str(slots.get("when") or ""))                  # reads only the day/time words from "when"
    f["ref"], f["issue"] = None, slots.get("issue_type") or "other"
    raw = re.sub(r"[^\d.]", "", str(slots["amount"]) if slots.get("amount") is not None else "")   # "N2,600" -> "2600"
    try: f["amount"] = int(float(raw)) if raw else None
    except ValueError: f["amount"] = None
    f["account"] = "".join(c for c in str(slots.get("beneficiary_account") or "") if c.isdigit()) or None
    return f


def demo_cases():
    as_of = datetime(2026, 9, 25, 22, 0)
    def phrase(ts, day_word=""):
        h = ts.hour % 12 or 12
        return f"{day_word} around {h}{'am' if ts.hour < 12 else 'pm'}".strip()
    def first_that_links(rows, day_word):
        for t in rows:
            f = extract(phrase(t["ts"], day_word)); f["issue"], f["amount"], f["account"], f["ref"] = "failed_transfer", t["amount"], t["beneficiary_account"], None
            r = link_facts(f, "", as_of, txns, ts_list, by_id)
            if r["status"] == "linked" and r["txn"] == t["txn_id"]: return t
    day = lambda d: [t for t in txns if t["ts"].date() == d.date()]
    a = first_that_links([t for t in day(as_of) if t["error_code"] == "NIP_TIMEOUT_91" and t["debited"] == "1" and 19 <= t["ts"].hour <= 21], "")
    b = first_that_links([t for t in day(as_of - timedelta(days=1)) if t["rail"] == "NIP_TRANSFER" and t["error_code"].startswith("NIP_ERR") and t["debited"] == "1"], "yesterday")
    print('First set this in PowerShell, then start the server:\n  $env:DEMO_AS_OF="2026-09-25 22:00"; python ingest.py\n')
    print("Speak these as complaints in the voice app (any of Yoruba, Hausa, Igbo). Mention the account number out loud.\n")
    for name, t, day_word, note in (("CASE A", a, "", "part of the flagged Sahel Bank incident"),
                                    ("CASE B", b, "yesterday", "an ordinary one-off failure, not an incident")):
        if t:
            print(f"{name} ({note})\n  say: I sent {t['amount']} naira {phrase(t['ts'], day_word)} to account {t['beneficiary_account']}. "
                  f"It failed but I was debited.\n  bank to give when asked: any bank name\n  expected link: {t['txn_id']}\n")
    print("CASE C (nothing matches)\n  say: I sent 987600 naira yesterday around 4pm to account 1234567890. It failed but I was debited.\n  expected: unmatched, goes to a human\n")


def build_app():
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.before_request
    def _shared_secret():
        """If TRACE_KEY is set, every request except /health must carry it. Unset = open (fine on your own computer)."""
        key = os.environ.get("TRACE_KEY", "")
        if key and request.path != "/health" and not hmac.compare_digest(request.headers.get("X-Trace-Key", "").encode(), key.encode()):
            return jsonify(error="forbidden"), 403

    db = sqlite3.connect(os.environ.get("CASES_DB", "cases.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS cases (ref TEXT PRIMARY KEY, received_at TEXT, filed_at TEXT, language TEXT,
        call_status TEXT, call_reason TEXT, issue TEXT, amount INTEGER, bank TEXT, account TEXT, when_text TEXT,
        link_status TEXT, txn_id TEXT, link_method TEXT, link_conf TEXT, cands TEXT, alert_id TEXT,
        decision TEXT, priority TEXT, reasons TEXT, action TEXT, caller_message TEXT,
        override_status TEXT, override_note TEXT, override_by TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ref TEXT, at TEXT, who TEXT, what TEXT, note TEXT);
    CREATE TABLE IF NOT EXISTS reversals (txn_id TEXT PRIMARY KEY, initiated_at TEXT, as_of TEXT, source TEXT, ref TEXT, rail TEXT,
        amount INTEGER, deadline_at TEXT, overdue INTEGER, hours_late REAL, rule TEXT, policy_status TEXT);
    """)
    lock = threading.Lock()
    now = lambda: datetime.now().isoformat(timespec="seconds")
    clock = lambda: datetime.fromisoformat(DEMO_AS_OF) if DEMO_AS_OF else datetime.now()

    def record(rows, source, ref, as_of):
        with lock:
            db.executemany("INSERT OR IGNORE INTO reversals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(r["txn_id"], now(), as_of.isoformat(sep=" "), source, ref, r["rail"], r["amount"], r["deadline_at"],
                  r["overdue"], r["hours_late"], r["rule"], r["policy_status"]) for r in rows])
            db.commit()

    def run_sweep(as_of=None):
        """Refunds that start by themselves: every failed, debited, unreversed transaction past its normal automatic
        window is picked up, whether or not the customer ever complains. Safe to repeat."""
        as_of = as_of or clock()
        with lock: have = {r[0] for r in db.execute("SELECT txn_id FROM reversals")}
        rows = due(FAILED_DEBITED, as_of, have)
        record(rows, "auto_sweep", None, as_of)
        return len(rows)

    def rev_summary():
        with lock:
            c = db.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) amt, COALESCE(SUM(overdue),0) late, "
                           "COALESCE(SUM(CASE WHEN overdue=1 THEN amount ELSE 0 END),0) late_amt FROM reversals").fetchone()
            by = {r["rail"]: r["n"] for r in db.execute("SELECT rail, COUNT(*) n FROM reversals GROUP BY rail")}
            auto = db.execute("SELECT COUNT(*) FROM reversals WHERE source='auto_sweep'").fetchone()[0]
        return dict(count=c["n"], amount=c["amt"], overdue=c["late"], overdue_amount=c["late_amt"], automatic=auto, by_rail=by,
                    policies=[dict(rail=k, deadline_hours=v["deadline_hours"], grace_minutes=v["grace_minutes"], status=v["status"],
                                   rule=v["rule"]) for k, v in POLICY.items()])

    def shape(r):
        d = dict(r); d["reasons"] = json.loads(d["reasons"] or "[]")
        d["status"] = d["override_status"] or d["decision"]          # a human override wins
        d["overridden"] = bool(d["override_status"])
        t = by_txn.get(d["txn_id"])
        d["transaction"] = None if not t else {k: (v.isoformat(sep=" ") if isinstance(v, datetime) else v) for k, v in t.items()
                           if k in ("txn_id", "ts", "rail", "channel", "amount", "status", "error_code", "debited", "counterparty_bank", "region")}
        return d

    def all_cases():
        with lock: rows = db.execute("SELECT * FROM cases ORDER BY received_at DESC").fetchall()
        return [shape(r) for r in rows]

    def stats(cs):
        n = len(cs); auto = sum(c["status"] == "auto_resolved" for c in cs)
        return dict(total=n, auto_resolved=auto, needs_human=sum(c["status"] == "needs_human" for c in cs),
                    resolved_by_human=sum(c["status"] == "resolved" for c in cs), overridden=sum(c["overridden"] for c in cs),
                    auto_rate=round(auto / n, 2) if n else 0)

    @app.get("/health")
    def health():
        return jsonify(ok=True, transactions=len(txns), demo_as_of=DEMO_AS_OF)

    @app.post("/ingest")
    def ingest():
        d = request.get_json(silent=True) or {}
        if not d.get("ref"): return jsonify(error="ref is required"), 400
        slots = d.get("slots") or {}
        f = facts_from_call(slots)
        filed = datetime.fromisoformat(DEMO_AS_OF) if DEMO_AS_OF else datetime.fromisoformat(d.get("created_at") or now())
        link = link_facts(f, "", filed, txns, ts_list, by_id)         # voice callers are not authenticated: no customer id
        t = by_txn.get(link["txn"]); a = alert_for(t) if t else None
        rev = bool(t and t["reversed_at"] and datetime.strptime(t["reversed_at"], FMT) <= filed)
        dec = decide(dict(status=d.get("status"), reason=d.get("reason"), issue=f["issue"]), link, t, a, filed, rev)
        run_sweep(filed)                                       # refunds start by themselves, complaint or not
        if t and dec["decision"] == "auto_resolved" and dec["action"].startswith("Reversal requested"):
            with lock: prior = db.execute("SELECT source FROM reversals WHERE txn_id=?", (t["txn_id"],)).fetchone()
            if prior and prior["source"] == "auto_sweep":
                hrs = policy_for(t["rail"])["deadline_hours"]
                dec["action"] = "Refund already started automatically (SIMULATED)"
                dec["caller_message"] = f"We had already started the refund for this failed transaction automatically, before you called. Expect it within {hrs} hours."
            elif not prior:
                record([row_for(t, filed)], "complaint", d["ref"], filed)
        with lock:
            keep = db.execute("SELECT override_status, override_note, override_by FROM cases WHERE ref=?", (d["ref"],)).fetchone()
            db.execute("INSERT OR REPLACE INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["ref"], now(), filed.isoformat(sep=" "), d.get("language"), d.get("status"), d.get("reason"), f["issue"],
                 f["amount"], slots.get("bank"), f["account"], slots.get("when"), link["status"], link["txn"], link["method"],
                 str(link["conf"]), link["cands"], a["id"] if a else None, dec["decision"], dec["priority"],
                 json.dumps(dec["reasons"]), dec["action"], dec["caller_message"],
                 keep["override_status"] if keep else None, keep["override_note"] if keep else None,
                 keep["override_by"] if keep else None, now()))
            db.execute("INSERT INTO audit (ref, at, who, what, note) VALUES (?,?,?,?,?)",
                       (d["ref"], now(), "system", dec["decision"], "; ".join(dec["reasons"])))
            db.commit()
        out = dict(ref=d["ref"], link_status=link["status"], confidence=link["conf"], method=link["method"], candidates=link["cands"],
                   decision=dec["decision"], human_required=dec["human_required"], priority=dec["priority"],
                   reasons=dec["reasons"], action=dec["action"], caller_message=dec["caller_message"])
        if a: out["part_of_incident"] = dict(alert=a["id"], error_code=a["label"], likely_cause=a["hypothesis"])
        return jsonify(out)

    def incident_view(cs):
        """Every channel together: dataset history plus live voice calls, as of the demo clock."""
        as_of = datetime.fromisoformat(DEMO_AS_OF) if DEMO_AS_OF else datetime.now()
        live = [dict(filed=datetime.fromisoformat(c["filed_at"]), ref=c["ref"], channel="voice_assistant",
                     txn_id=c["txn_id"] if c["link_status"] == "linked" else None) for c in cs]
        return detect_incidents(txns, ts_list, by_txn, HISTORY + live, as_of)

    @app.get("/cases")
    def cases():
        cs = all_cases()
        run_sweep()
        return jsonify(stats=stats(cs), cases=cs, reversals=rev_summary(), **incident_view(cs))

    @app.get("/reversals")
    def reversals():
        run_sweep()
        with lock: rows = [dict(r) for r in db.execute("SELECT * FROM reversals ORDER BY overdue DESC, amount DESC LIMIT 50")]
        return jsonify(summary=rev_summary(), top=rows)

    @app.get("/live")                                                   # old address, kept so earlier instructions still work
    def live(): return jsonify(all_cases())

    @app.get("/cases/<ref>")
    def one(ref):
        with lock:
            r = db.execute("SELECT * FROM cases WHERE ref=?", (ref,)).fetchone()
            log = [dict(x) for x in db.execute("SELECT at, who, what, note FROM audit WHERE ref=? ORDER BY id", (ref,))]
        return (jsonify(case=shape(r), audit=log) if r else (jsonify(error="unknown ref"), 404))

    @app.post("/cases/<ref>/override")
    def override(ref):
        d = request.get_json(silent=True) or {}
        if d.get("status") not in ("resolved", "needs_human", "auto_resolved"):
            return jsonify(error="status must be resolved, needs_human or auto_resolved"), 400
        with lock:
            if not db.execute("SELECT 1 FROM cases WHERE ref=?", (ref,)).fetchone(): return jsonify(error="unknown ref"), 404
            db.execute("UPDATE cases SET override_status=?, override_note=?, override_by=?, updated_at=? WHERE ref=?",
                       (d["status"], d.get("note", ""), d.get("by", "agent"), now(), ref))
            db.execute("INSERT INTO audit (ref, at, who, what, note) VALUES (?,?,?,?,?)",
                       (ref, now(), d.get("by", "agent"), "override:" + d["status"], d.get("note", "")))
            db.commit()
        return jsonify(ok=True)

    if not DEMO_AS_OF:
        print("WARNING: DEMO_AS_OF is not set. Calls are stamped with today's date, but the synthetic log ends 4 Oct 2026, so nothing will match.")
    run_sweep()
    print(f"ComplaintTrace ready: {len(txns):,} transactions, {len(ALERTS)} alerts loaded. As-of: {DEMO_AS_OF or 'real time'}")
    return app


if __name__ == "__main__":
    if "--cases" in sys.argv:
        demo_cases(); sys.exit()
    build_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)), debug=False)