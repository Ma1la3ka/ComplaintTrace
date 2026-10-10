"""ComplaintTrace step 4: what does LINKING add?

Run from the complainttrace folder AFTER linker.py and detect.py:   python baseline_textonly.py
It uses ONLY the complaint text (no transaction log): count complaints by issue type and flag spikes.
Then it compares that with detect.py's results (alerts.csv) on the same planted incidents.
The answer key (incidents.csv) is used only to score.
"""
import csv, os, re
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta
from linker import extract            # same text-reading rules as the linker

DATA, FMT = "complainttrace_data", "%Y-%m-%d %H:%M:%S"
ts_ = lambda s: datetime.strptime(s, FMT)
read = lambda n, d=DATA: list(csv.DictReader(open(os.path.join(d, n), encoding="utf-8")))
# issue type -> (window, minimum complaints inside that window). Same strictness as detect.py.
RULE = {"other": (timedelta(hours=3), 10)}
DEFAULT = (timedelta(hours=6), 5)
BANKS = ["Unity Trust", "Sahel Bank", "Eko Mutual", "Delta Commerce", "Niger Capital", "Rivers Union", "Lagoon Bank", "Harmattan Bank"]
CODE_TYPES = {"NIP_TIMEOUT_91": {"failed_transfer"}, "ATM_DISP_07": {"atm_dispense"},
              "VEND_PENDING_88": {"debit_no_value"}, "USSD_SESSION_TIMEOUT": {"failed_transfer", "debit_no_value"}}

complaints = read("complaints.csv")
by_type = defaultdict(list)
for c in complaints:
    by_type[extract(c["text"])["issue"]].append((ts_(c["created_at"]), c["text"]))

alerts = []
for typ, items in by_type.items():
    items.sort(); times = [t for t, _ in items]
    win, need = RULE.get(typ, DEFAULT)
    dense, trig = set(), []
    for j in range(len(items)):
        lo = bisect_left(times, times[j] - win)
        if j - lo + 1 >= need: dense.update(range(lo, j + 1)); trig.append(times[j])
    idx = sorted(dense); run = []
    for k in idx + [None]:
        if run and (k is None or times[k] - times[run[-1]] > win):
            a, b = times[run[0]], times[run[-1]]
            texts = [items[r][1] for r in run]
            alerts.append(dict(type=typ, fire=min(t for t in trig if a <= t <= b), n=len(run),
                               bank=sum(any(bk in t for bk in BANKS) for t in texts),
                               ussd=sum("ussd" in t.lower() for t in texts)))
            run = []
        if k is not None: run.append(k)

incidents = read("incidents.csv")
for i in incidents: i["start"], i["end"] = ts_(i["start"]), ts_(i["end"])
def matches(a, i):
    ok_type = (a["type"] == "other") if i["kind"] == "rumor_no_failure" else a["type"] in CODE_TYPES.get(i["error_code"], set())
    return ok_type and i["start"] <= a["fire"] <= i["end"] + timedelta(hours=8)

linked = {r["matches"]: r for r in read("alerts.csv", ".")}
print(f"{'Incident':10s} {'Text only (complaint counts)':34s} Linked detector")
for i in incidents:
    hits = sorted((a for a in alerts if matches(a, i)), key=lambda a: a["fire"])
    t_ = (f"found +{(hits[0]['fire'] - i['start']).total_seconds() / 3600:.1f}h as '{hits[0]['type']}'" if hits else "MISSED")
    l_ = linked.get(i["incident_id"])
    l_ = f"found +{l_['delay_h']}h" if l_ and l_["delay_h"] != "" else ("found" if l_ else "MISSED")
    print(f"{i['incident_id']:10s} {t_:34s} {l_}")

false_t = [a for a in alerts if not any(matches(a, i) for i in incidents)]
print(f"\nFalse alerts: text only {len(false_t)}, linked detector {sum(1 for r in linked.values() if r['matches'].startswith('NONE'))}")

print("\nWhat the text-only version can say about each incident:")
for i in incidents:
    hit = next((a for a in sorted(alerts, key=lambda a: a["fire"]) if matches(a, i)), None)
    if hit and i["kind"] != "rumor_no_failure":
        print(f"  {i['incident_id']}: 'spike in {hit['type']}'. Bank named in {hit['bank']}/{hit['n']} complaints, USSD named in {hit['ussd']}/{hit['n']}. No error code, no failed-transaction count.")
    elif hit:
        print(f"  {i['incident_id']}: 'spike in general complaints'. Cannot tell a rumour from a real outage without the transaction log.")

cl = [r for r in linked.values() if r["type"] == "failure_cluster"]
silent, money = sum(int(r["silent_customers"]) for r in cl), sum(int(r["amount_unreversed"]) for r in cl)
print(f"\nInvisible to a text-only system: {silent} debited customers who never complained, and N{money:,} still unreversed.")