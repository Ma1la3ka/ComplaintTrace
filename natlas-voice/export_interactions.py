"""Export the sessions as a table you can attach as evidence of real user interactions.
Run:   python export_interactions.py                 (every session)
       python export_interactions.py 2026-10-10      (only sessions created on or after that date)
Output: interactions.csv  (no names or phone numbers are collected by the app)"""
import csv
import json
import sys
from datetime import datetime

from db import make_store


def _when(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v)
    return v


def export(store, path="interactions.csv", since=None):
    sql = "SELECT id, ref, language, status, reason, slots, turns, created_at FROM sessions"
    if hasattr(store, "_run"):                       # MySQL / TiDB
        rows = store._run(sql + " ORDER BY created_at", (), "all")
    else:                                            # SQLite
        rows = [dict(r) for r in store.conn.execute(sql + " ORDER BY created_at").fetchall()]
    out = []
    for r in rows:
        created = _when(r["created_at"])
        if since and created.date() < since:
            continue
        slots = r["slots"]
        slots = json.loads(slots) if isinstance(slots, (str, bytes)) and slots else (slots or {})
        callers = [t["text"] for t in store.turns(r["id"]) if t["role"] == "caller"]
        out.append({"ref": r["ref"], "date": created.strftime("%Y-%m-%d %H:%M"), "language": r["language"],
                    "outcome": r["status"], "reason": r["reason"], "caller_turns": len(callers),
                    "issue_type": slots.get("issue_type", ""), "amount": slots.get("amount", ""),
                    "bank": slots.get("bank", ""), "first_caller_message": (callers[0] if callers else "")[:80]})
    cols = ["ref", "date", "language", "outcome", "reason", "caller_turns", "issue_type", "amount", "bank",
            "first_caller_message"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    return out


if __name__ == "__main__":
    since = datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else None
    rows = export(make_store(), since=since)
    by_lang = {}
    for r in rows:
        by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
    print(f"{len(rows)} interactions exported to interactions.csv | by language: {by_lang}")