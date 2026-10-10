"""ComplaintTrace step 2: link each complaint to the transaction it is about.

Run from the complainttrace folder:   python linker.py
Reads : complainttrace_data/complaints.csv, transactions.csv  (and truth.csv, only to score the result)
Writes: links.csv

For every complaint it returns one of:
  linked      - one transaction clearly fits (method: reference or evidence)
  ambiguous   - several transactions fit about equally; top candidates are listed for a human
  unmatched   - nothing in the log fits
  not_linkable - the complaint is not about a specific transaction (general question, rumour)

Why 'ambiguous' exists: forcing a guess would put wrong evidence in front of a bank team.
"""
import csv, os, re
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta

DATA = "complainttrace_data"
FMT = "%Y-%m-%d %H:%M:%S"
WINDOW = timedelta(hours=72)            # a complaint is filed within 72h of the transaction
LINK_MIN_SCORE, LINK_MIN_MARGIN = 6.0, 1.5

RAILS_FOR = {"failed_transfer": {"NIP_TRANSFER"}, "debit_no_value": {"AIRTIME_DATA"},
             "atm_dispense": {"ATM"}, "pos_or_web": {"CARD_POS", "CARD_WEB"}}

# ---------- 1. pull facts out of the complaint text (rule-based) --------------------------
def extract(text):
    t = text.lower()
    out = {"ref": None, "amount": None, "account": None, "issue": "other", "day": None, "part": None, "hour": None}
    m = re.search(r"txn\d{8}", t)
    if m: out["ref"] = m.group(0).upper()
    m = re.search(r"(?:₦|\bn)\s?(\d[\d,]*)", t) or re.search(r"(\d[\d,]*)\s*naira", t)
    if m: out["amount"] = int(m.group(1).replace(",", ""))
    m = re.search(r"\b(\d{10})\b", t)
    if m: out["account"] = m.group(1)
    if re.search(r"did not authori|did not do this|no do this|took .* from my account|who take my money", t):
        out["issue"] = "suspected_fraud"
    elif re.search(r"\batm\b", t): out["issue"] = "atm_dispense"
    elif re.search(r"\b(airtime|data)\b", t): out["issue"] = "debit_no_value"
    elif re.search(r"\bcard\b", t) and re.search(r"charge|debited|payment|merchant", t): out["issue"] = "pos_or_web"
    elif re.search(r"transfer|\bsent\b|\bsend\b|beneficiary|receiver|reach", t): out["issue"] = "failed_transfer"
    # when did it happen?
    m = re.search(r"(\d+) days ago", t)
    if m: out["day"] = int(m.group(1))
    elif "yesterday" in t or "last night" in t: out["day"] = 1
    elif re.search(r"this morning|this afternoon|this evening|tonight|earlier today|\btoday\b", t): out["day"] = 0
    if "morning" in t: out["part"] = "morning"
    elif "afternoon" in t: out["part"] = "afternoon"
    elif re.search(r"evening|tonight|night", t): out["part"] = "night"
    m = re.search(r"around (\d{1,2})(am|pm)", t)
    if m:
        h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0)
        out["hour"] = h
        if out["day"] is None: out["day"] = 0
    return out

PARTS = {"morning": (5, 11), "afternoon": (12, 16), "night": (17, 23)}

def time_fit(f, txn_ts, filed):
    """Rough time check. Callers are vague, so a near miss still earns credit and a miss costs very little.
    Returns points to add to the score, or None when the caller gave no time words."""
    if f["day"] is None and f["hour"] is None and f["part"] is None: return None
    pts, h = 2.0, txn_ts.hour
    if f["day"] is not None:
        off = abs((filed.date() - txn_ts.date()).days - f["day"])
        if off > 1: return -0.5
        if off == 1: pts = 1.0
    if f["hour"] is not None:
        gap = abs(h - f["hour"]); gap = min(gap, 24 - gap)
        if gap > 3: return -0.5
        if gap > 1: pts = min(pts, 1.0)
    if f["part"]:
        lo, hi = PARTS[f["part"]]
        if lo <= h <= hi: pass
        elif lo - 1 <= h <= hi + 1: pts = min(pts, 1.0)
        else: return -0.5
    return pts

# ---------- 2. score candidate transactions --------------------------------------------------
def score(f, cust, filed, t):
    """Return (score, reasons) or None if this transaction cannot be the one."""
    s, why = 0.0, []
    fraud = f["issue"] == "suspected_fraud"
    if not fraud and t["status"] == "SUCCESS": return None
    if f["issue"] in RAILS_FOR and t["rail"] not in RAILS_FOR[f["issue"]]: return None
    if cust:
        if cust != t["customer_id"]: return None
        s += 4; why.append("customer")
    if f["amount"] is not None:
        diff = abs(f["amount"] - t["amount"])
        if diff == 0: s += 4; why.append("amount")
        elif diff <= 0.01 * t["amount"]: s += 3; why.append("amount~close")      # e.g. 5,419 heard for 5,400
        elif diff <= 1000 or diff <= 0.05 * t["amount"]: s += 1; why.append("amount~")
        else: return None
    if f["account"]:
        acct, real = f["account"], t["beneficiary_account"]
        if acct == real: s += 5; why.append("beneficiary")
        elif len(acct) == len(real) and sum(a != b for a, b in zip(acct, real)) == 1:
            s += 3; why.append("beneficiary~")                                    # one digit misheard
        else: return None
    fit = time_fit(f, t["ts"], filed)
    if fit is not None:
        s += fit; why.append("time" if fit > 0 else "time-miss")
    if t["debited"] == "1" and not fraud: s += 1
    gap_h = (filed - t["ts"]).total_seconds() / 3600
    s += 0.5 * max(0.0, 1 - gap_h / 72)                      # small tie-break: nearer in time is likelier
    return s, why

def link_one(complaint, txns, ts_list, by_id):
    return link_facts(extract(complaint["text"]), complaint["customer_id"],
                      datetime.strptime(complaint["created_at"], FMT), txns, ts_list, by_id)

def link_facts(f, cust, filed, txns, ts_list, by_id):
    """Core matcher. `f` holds the facts (issue, amount, account, ref, day/part/hour).
    Voice calls pass their already-saved fields straight in, so no text reading is needed."""
    if f["issue"] == "other" and not f["ref"]:
        return dict(status="not_linkable", method="", txn="", conf="", cands="")
    if f["ref"] in by_id:
        return dict(status="linked", method="reference", txn=f["ref"], conf=1.0, cands="")
    lo, hi = bisect_left(ts_list, filed - WINDOW), bisect_right(ts_list, filed)
    scored = []
    for t in txns[lo:hi]:
        r = score(f, cust, filed, t)
        if r: scored.append((r[0], t["txn_id"], r[1]))
    scored.sort(reverse=True)
    if not scored: return dict(status="unmatched", method="", txn="", conf="", cands="")
    top = scored[0]
    margin = top[0] - (scored[1][0] if len(scored) > 1 else 0)
    cands = "; ".join(f"{c[1]}({c[0]:.1f})" for c in scored[:3])
    if top[0] >= LINK_MIN_SCORE and margin >= LINK_MIN_MARGIN:
        return dict(status="linked", method="evidence:" + "+".join(top[2]), txn=top[1],
                    conf=round(min(0.99, 0.5 + 0.07 * top[0] + 0.05 * margin), 2), cands=cands)
    return dict(status="ambiguous", method="", txn="", conf="", cands=cands)

def load_txns(data_dir=DATA):
    rows = list(csv.DictReader(open(os.path.join(data_dir, "transactions.csv"), encoding="utf-8")))
    for t in rows: t["ts"] = datetime.strptime(t["ts"], FMT); t["amount"] = int(t["amount"])
    rows.sort(key=lambda t: t["ts"])
    return rows, [t["ts"] for t in rows], {t["txn_id"] for t in rows}

# ---------- 3. run it and score against the answer key ---------------------------------------
def main():
    read = lambda n: list(csv.DictReader(open(os.path.join(DATA, n), encoding="utf-8")))
    txns, ts_list, by_id = load_txns()
    complaints, truth = read("complaints.csv"), {r["ref"]: r for r in read("truth.csv")}

    rows = []
    for c in complaints:
        r = link_one(c, txns, ts_list, by_id)
        rows.append(dict(ref=c["ref"], channel=c["channel"], **r))
    with open("links.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

    st = Counter(r["status"] for r in rows)
    print("Results over", len(rows), "complaints:", dict(st))
    real = [r for r in rows if truth[r["ref"]]["gt_txn_id"]]            # complaints that do have a true transaction
    linked = [r for r in real if r["status"] == "linked"]
    right = sum(r["txn"] == truth[r["ref"]]["gt_txn_id"] for r in linked)
    print(f"\nComplaints that truly have a transaction: {len(real)}")
    print(f"  linked {len(linked)} ({len(linked)/len(real):.0%}), of which correct {right} ({right/max(1,len(linked)):.1%} precision)")
    print(f"  ambiguous {sum(r['status']=='ambiguous' for r in real)}, unmatched {sum(r['status']=='unmatched' for r in real)}")
    for ch in sorted({r["channel"] for r in real}):
        sub = [r for r in real if r["channel"] == ch]
        ok = sum(r["status"] == "linked" and r["txn"] == truth[r["ref"]]["gt_txn_id"] for r in sub)
        print(f"    {ch:16s} {ok}/{len(sub)} linked correctly")
    false = [r for r in rows if not truth[r["ref"]]["gt_txn_id"] and r["status"] == "linked"]
    print(f"Complaints with NO real transaction wrongly linked: {len(false)}")
    # extraction check on issue type
    acc = sum(extract(c["text"])["issue"] == truth[c["ref"]]["gt_issue_type"] for c in complaints)
    print(f"\nIssue-type extraction: {acc}/{len(complaints)} ({acc/len(complaints):.0%}) match the answer key")
    print("\nSaved links.csv")

if __name__ == "__main__":
    main()