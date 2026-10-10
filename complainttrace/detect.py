"""ComplaintTrace step 3: spot systemic incidents from linked complaints, and say what they have in common.

Run from the complainttrace folder AFTER linker.py:   python detect.py
Reads : complainttrace_data/*.csv and links.csv
Writes: alerts.csv

Two detectors:
 1. Failure clusters  - complaints whose linked transaction carries the same error code, close together in time.
                        Linking is what makes this possible: the complaint text never contains the error code.
 2. Unexplained spikes - many complaints that do NOT link to any failed transaction, while the transaction log
                        looks normal. That is the pattern of a rumour, not a system fault.
For each alert it also lists customers who were hit but have NOT complained yet (the proactive part).
"""
import csv, os, math
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta

DATA, FMT = "complainttrace_data", "%Y-%m-%d %H:%M:%S"
CLUSTER_GAP, MIN_CLUSTER = timedelta(hours=6), 5       # same error code, complaints <6h apart, at least 5
SPIKE_GAP, MIN_SPIKE = timedelta(hours=3), 10          # at least 10 unexplained complaints inside any 3-hour window
ts_ = lambda s: datetime.strptime(s, FMT) if s else None
read = lambda n, d=DATA: list(csv.DictReader(open(os.path.join(d, n), encoding="utf-8")))

txns = read("transactions.csv")
for t in txns: t["ts"], t["amount"], t["rev"] = ts_(t["ts"]), int(t["amount"]), ts_(t["reversed_at"])
txns.sort(key=lambda t: t["ts"]); times = [t["ts"] for t in txns]
by_id = {t["txn_id"]: t for t in txns}
complaints = {c["ref"]: c for c in read("complaints.csv")}
links = read("links.csv", ".")
for c in complaints.values(): c["filed"] = ts_(c["created_at"])
incidents = read("incidents.csv")
for i in incidents: i["start"], i["end"] = ts_(i["start"]), ts_(i["end"])

def runs(items, gap):
    """Split time-sorted items into runs where consecutive items are less than `gap` apart."""
    out, cur = [], []
    for it in items:
        if cur and it[0] - cur[-1][0] > gap: out.append(cur); cur = []
        cur.append(it)
    return out + ([cur] if cur else [])

def window(a, b): return txns[bisect_left(times, a):bisect_right(times, b)]

def hypothesis(failed, everything):
    """Which attribute values are over-represented among the failures, compared with all traffic in that window."""
    notes = []
    for attr in ("rail", "channel", "counterparty_bank", "region"):
        fc, ac = Counter(t[attr] for t in failed if t[attr]), Counter(t[attr] for t in everything if t[attr])
        for val, n in fc.items():
            share, base = n / len(failed), ac[val] / len(everything)
            if share >= 0.8 and base > 0 and share / base >= 2:
                notes.append((share / base, f"{attr}={val} ({share:.0%} of failures vs {base:.0%} of traffic)"))
    return "; ".join(n for _, n in sorted(notes, reverse=True)[:3]) or "no single attribute stands out"

# ---------- Detector 1: failure clusters by error code ----------------------------------------
by_code = defaultdict(list)
for l in links:
    if l["status"] == "linked":
        t = by_id[l["txn"]]
        if t["error_code"]: by_code[t["error_code"]].append((complaints[l["ref"]]["filed"], l["ref"], t))

alerts = []
for code, items in by_code.items():
    for run in runs(sorted(items, key=lambda x: x[0]), CLUSTER_GAP):
        if len(run) < MIN_CLUSTER: continue
        fire = run[MIN_CLUSTER - 1][0]                          # moment the 5th complaint arrives
        a, b = min(x[2]["ts"] for x in run) - timedelta(hours=1), max(x[2]["ts"] for x in run) + timedelta(hours=1)
        everything = window(a, b)
        failed = [t for t in everything if t["error_code"] == code]
        complained = {x[2]["customer_id"] for x in run}
        silent = {t["customer_id"] for t in failed if t["debited"] == "1"} - complained
        stuck = [t for t in failed if t["debited"] == "1" and t["rev"] is None]
        alerts.append(dict(type="failure_cluster", label=code, fire=fire, first_txn=min(x[2]["ts"] for x in run),
                           complaints=len(run), failed_txns=len(failed), silent_customers=len(silent),
                           unreversed=len(stuck), amount_unreversed=sum(t["amount"] for t in stuck),
                           hypothesis=hypothesis(failed, everything)))

# ---------- Detector 2: unexplained complaint spikes ------------------------------------------
linked_refs = {l["ref"] for l in links if l["status"] == "linked"}
loose = sorted((c["filed"], c["ref"]) for r, c in complaints.items() if r not in linked_refs)
base_rate = sum(1 for t in txns if t["status"] != "SUCCESS") / len(txns)
loose_t = [t for t, _ in loose]
dense, triggers = set(), []
for j in range(len(loose)):                             # sliding window: a real spike packs many complaints into 3h
    lo = bisect_left(loose_t, loose_t[j] - SPIKE_GAP)
    if j - lo + 1 >= MIN_SPIKE:
        dense.update(range(lo, j + 1)); triggers.append(loose_t[j])
for run in runs([loose[k] for k in sorted(dense)], SPIKE_GAP):
    a, b = run[0][0], run[-1][0]
    fire_at = min(t for t in triggers if a <= t <= b)
    inwin = window(a, b)
    rate = sum(1 for t in inwin if t["status"] != "SUCCESS") / max(1, len(inwin))
    normal = rate <= 1.5 * base_rate
    alerts.append(dict(type="unexplained_spike" if normal else "spike_with_failures", label="no linked failures",
                       fire=fire_at, first_txn="", complaints=len(run), failed_txns=0,
                       silent_customers=0, unreversed=0, amount_unreversed=0,
                       hypothesis=(f"{len(run)} complaints with no failing transactions behind them; failure rate in the "
                                   f"window {rate:.1%} vs normal {base_rate:.1%}. Likely rumour or misinformation."
                                   if normal else "complaints coincide with elevated failures")))

# ---------- score against the planted incidents ----------------------------------------------
daily = Counter(c["filed"].date() for c in complaints.values())
vals = list(daily.values()); mean = sum(vals) / len(vals); sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))
naive_days = {d for d, v in daily.items() if v > mean + 2 * sd}

print(f"{len(alerts)} alerts raised\n")
hit = set()
for k, a in enumerate(sorted(alerts, key=lambda a: a["fire"]), 1):
    a["id"] = f"ALERT-{k:02d}"
    m = next((i for i in incidents if (i["error_code"] == a["label"] and i["start"] <= a["fire"] <= i["end"] + timedelta(hours=8))
              or (a["type"] == "unexplained_spike" and i["kind"] == "rumor_no_failure" and i["start"] <= a["fire"] <= i["end"] + timedelta(hours=8))), None)
    a["matches"] = m["incident_id"] if m else "NONE (false alert)"
    a["delay_h"] = round((a["fire"] - m["start"]).total_seconds() / 3600, 1) if m else ""
    if m: hit.add(m["incident_id"])
    naive = "caught late (end of day)" if m and m["start"].date() in naive_days else "MISSED by daily threshold"
    print(f"{a['id']} {a['type']}: {a['label']}  -> {a['matches']}")
    print(f"   fired {a['fire']:%d %b %H:%M}, {a['delay_h']}h after the incident began | a daily-count threshold: {naive}")
    print(f"   {a['complaints']} complaints" + (f", {a['failed_txns']} failed transactions in the log, "
          f"{a['silent_customers']} debited customers who have NOT complained yet, "
          f"{a['unreversed']} still unreversed (N{a['amount_unreversed']:,})" if a['failed_txns'] else ""))
    print(f"   likely cause: {a['hypothesis']}\n")

real = [i for i in incidents]
print(f"Planted incidents found: {len(hit)}/{len(real)}  ({', '.join(sorted(hit))})")
print(f"False alerts: {sum(1 for a in alerts if a['matches'].startswith('NONE'))}")
print(f"Days a simple daily-total threshold would flag: {len(naive_days)} (threshold {mean + 2 * sd:.0f} complaints/day)")

cols = ["id", "type", "label", "fire", "first_txn", "complaints", "failed_txns", "silent_customers", "unreversed",
        "amount_unreversed", "hypothesis", "matches", "delay_h"]
with open("alerts.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
    for a in sorted(alerts, key=lambda a: a["fire"]): w.writerow({c: a.get(c, "") for c in cols})
print("Saved alerts.csv")