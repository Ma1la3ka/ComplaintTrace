"""Run the extraction step on a whole set of sentences and report how many it gets right.
Needs the GPU on. Usage:  python eval_extraction.py
Add your own real transcripts (Yoruba, Hausa, Igbo, English) to eval_cases.json, then run again
after every prompt change. Results are also saved to eval_results.csv (useful validation evidence)."""
import csv
import json
import os
import sys
from dialogue import (EXTRACT_SYSTEM, GREETING, QUESTIONS, CONFIRM_ASK, parse_extraction,
                      thousands_number)

STAGES = {"start": GREETING, "amount": QUESTIONS["amount"], "when": QUESTIONS["when"],
          "bank": QUESTIONS["bank"], "confirm": CONFIRM_ASK}


def extract(client, text, stage):
    msg = f"Question asked: {STAGES[stage]}\nCustomer said: {text}"
    raw = client.generate(EXTRACT_SYSTEM, [{"role": "user", "content": msg}], max_new_tokens=200, temperature=0.0)
    got = parse_extraction(raw)
    num = thousands_number(text)
    if num is not None and (stage == "amount" or "amount" in got):
        got["amount"] = num                      # same safeguard the live app uses
    return got, raw


def matches(field, want, got):
    have = got.get(field)
    if isinstance(want, list):
        return have in want
    if field in ("when", "bank") and isinstance(want, str):
        return isinstance(have, str) and want.lower() in have.lower()
    return have == want


def run_eval(client, cases):
    rows, field_ok, field_all = [], {}, {}
    for c in cases:
        got, raw = extract(client, c["text"], c["stage"])
        fails = []
        for field, want in c["expect"].items():
            field_all[field] = field_all.get(field, 0) + 1
            if matches(field, want, got):
                field_ok[field] = field_ok.get(field, 0) + 1
            else:
                fails.append(f"{field}: wanted {want!r}, got {got.get(field)!r}")
        rows.append({"ok": not fails, "stage": c["stage"], "text": c["text"], "problems": "; ".join(fails),
                     "raw": raw.replace("\n", " ")})
    return rows, field_ok, field_all


def main():
    from natlas_client import NatlasClient
    cases = json.load(open("eval_cases.json", encoding="utf-8"))
    client = NatlasClient(os.environ["NATLAS_URL"], os.environ["NATLAS_KEY"])
    rows, field_ok, field_all = run_eval(client, cases)
    for r in rows:
        print(("PASS  " if r["ok"] else "FAIL  ") + f"[{r['stage']}] {r['text'][:60]}")
        if not r["ok"]:
            print("      " + r["problems"])
    passed = sum(r["ok"] for r in rows)
    print(f"\n{passed}/{len(rows)} sentences fully correct")
    for f in sorted(field_all):
        print(f"  {f}: {field_ok.get(f, 0)}/{field_all[f]}")
    with open("eval_results.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ok", "stage", "text", "problems", "raw"])
        w.writeheader()
        w.writerows(rows)
    print("saved eval_results.csv")


if __name__ == "__main__":
    main()