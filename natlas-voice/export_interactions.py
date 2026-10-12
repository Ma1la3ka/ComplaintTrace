"""Export the sessions as a table you can attach as evidence of real user interactions.
Run:   python export_interactions.py                 (every session)
       python export_interactions.py 2026-10-10      (only sessions created on or after that date)
Output: interactions.csv. The app collects no names or phone numbers. As a safety net, any run of 6 or more digits in the
first caller message is replaced with [number], and the account number itself is never exported (only whether one was given).
"""
import csv
import json
import re
import sys
from datetime import datetime

from db import make_store
from envfile import load_env
load_env()                                   # read the same .env the app uses
import os
print("Reading from:", ("MySQL/TiDB host " + os.environ["DB_HOST"] + ", database " + os.environ.get("DB_NAME", "natlas_voice")) if os.environ.get("DB_HOST") else "local SQLite file " + os.environ.get("DB_PATH", "complaints.db"))

def _when(v):
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v)
    return v


def export(store, path="interactions.csv", since=None):
    sql = "SELECT id, ref, language, status, reason, slots, turns, created_at, updated_at FROM sessions"
    if hasattr(store, "_run"):                       # MySQL / TiDB
        rows = store._run(sql + " ORDER BY created_at", (), "all")
    else:                                            # SQLite
        rows = [dict(r) for r in store.conn.execute(sql + " ORDER BY created_at").fetchall()]
    out = []
    for r in rows:
        created, updated = _when(r["created_at"]), _when(r["updated_at"])
        if since and created.date() < since:
            continue
        slots = r["slots"]
        slots = json.loads(slots) if isinstance(slots, (str, bytes)) and slots else (slots or {})
        callers = [t["text"] for t in store.turns(r["id"]) if t["role"] == "caller"]
        first = re.sub(r"\d{6,}", "[number]", (callers[0] if callers else ""))[:80]
        out.append({"ref": r["ref"], "date": created.strftime("%Y-%m-%d %H:%M"), "language": r["language"],
                    "completed": "no" if r["status"] == "active" else "yes", "outcome": r["status"], "reason": r["reason"],
                    "caller_turns": len(callers), "minutes": round((updated - created).total_seconds() / 60, 1),
                    "issue_type": slots.get("issue_type", ""), "amount": slots.get("amount", ""),
                    "when": slots.get("when", ""), "bank": slots.get("bank", ""),
                    "account_given": "yes" if slots.get("beneficiary_account") else "no", "first_caller_message": first})
    cols = ["ref", "date", "language", "completed", "outcome", "reason", "caller_turns", "minutes", "issue_type", "amount",
            "when", "bank", "account_given", "first_caller_message"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    return out


if __name__ == "__main__":
    since = datetime.strptime(sys.argv[1], "%Y-%m-%d").date() if len(sys.argv) > 1 else None
    rows = export(make_store(), since=since)
    by_lang, done = {}, 0
    for r in rows:
        by_lang[r["language"]] = by_lang.get(r["language"], 0) + 1
        done += r["completed"] == "yes"
    print(f"{len(rows)} sessions exported to interactions.csv ({done} completed) | by language: {by_lang}")