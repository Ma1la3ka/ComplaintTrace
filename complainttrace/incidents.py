"""ComplaintTrace incident detection for the live screen.

Takes every complaint from every channel (the synthetic history plus live voice calls) that has been linked to a
transaction, and answers the questions in the brief:
  - Is this an ISOLATED case or an EMERGING / SYSTEMIC problem?   (clusters of complaints sharing one error code)
  - What is the likely ROOT CAUSE?                               (what the failing transactions have in common)
  - Where should the bank intervene FIRST?                       (priority, from money stuck and refund deadlines)
  - Who is affected but has NOT complained yet?                  (so the bank can act before they escalate)
Also flags a spike of complaints with no failing transactions behind it (likely a rumour, not a fault).

Rules are deliberately simple and visible. Thresholds are assumptions, not bank policy.
"""
import csv, os
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decide import REVERSAL_HOURS

FMT = "%Y-%m-%d %H:%M:%S"
CLUSTER_GAP = timedelta(hours=6)
WATCH_MIN, SYSTEMIC_MIN = 3, 5            # 3 related complaints = early warning, 5 = systemic
SPIKE_WIN, SPIKE_MIN = timedelta(hours=3), 10
HIGH_AMOUNT = 200_000                     # unreversed money (naira) that makes an incident high priority
iso = lambda d: d.isoformat(sep=" ") if d else None


def load_history(data_dir="complainttrace_data", links_file="links.csv"):
    """Complaints from the dataset, with the transaction each was linked to (run linker.py first)."""
    if not os.path.exists(links_file): return []
    comp = {r["ref"]: r for r in csv.DictReader(open(os.path.join(data_dir, "complaints.csv"), encoding="utf-8"))}
    out = []
    for l in csv.DictReader(open(links_file, encoding="utf-8")):
        c = comp.get(l["ref"])
        if c: out.append(dict(filed=datetime.strptime(c["created_at"], FMT), ref=l["ref"], channel=c["channel"],
                              txn_id=l["txn"] if l["status"] == "linked" else None))
    return out


def _runs(seq, gap, key):
    out, cur = [], []
    for x in seq:
        if cur and key(x) - key(cur[-1]) > gap: out.append(cur); cur = []
        cur.append(x)
    return out + ([cur] if cur else [])


def _cause(failed, everything):
    notes = []
    for attr in ("rail", "channel", "counterparty_bank", "region"):
        fc, ac = Counter(t[attr] for t in failed if t[attr]), Counter(t[attr] for t in everything if t[attr])
        for val, n in fc.items():
            share, base = n / len(failed), ac[val] / len(everything)
            if share >= 0.8 and base > 0 and share / base >= 2:
                notes.append((share / base, attr, val, f"{attr}={val} ({share:.0%} of failures vs {base:.0%} of traffic)"))
    notes.sort(reverse=True)
    return notes


def _action(notes):
    attrs = {(a, v) for _, a, v, _ in notes}
    if any(a == "counterparty_bank" for a, _ in attrs): return "Raise with the receiving bank and the switch (NIBSS); message affected customers; plan a bulk refund."
    if ("rail", "ATM") in attrs: return "Check the ATM fleet in the affected region (recent software or cash-dispenser fault); refund affected withdrawals."
    if ("rail", "AIRTIME_DATA") in attrs: return "Contact the airtime/data vendor; run a bulk refund for pending purchases."
    if ("channel", "ussd") in attrs: return "Check the USSD gateway for session timeouts; refund affected sessions."
    return "Investigate with the owning team."


def detect(txns, ts_list, by_txn, items, as_of):
    """items: list of dict(filed, ref, channel, txn_id or None). Returns incidents plus a count of isolated cases."""
    items = sorted((i for i in items if i["filed"] <= as_of), key=lambda i: i["filed"])
    window = lambda a, b: txns[bisect_left(ts_list, a):bisect_right(ts_list, min(b, as_of))]
    parse = lambda s: datetime.strptime(s, FMT) if s else None
    def unreversed(t):
        r = parse(t["reversed_at"]); return t["debited"] == "1" and not (r and r <= as_of)

    by_code = defaultdict(list)
    for i in items:
        t = by_txn.get(i["txn_id"]) if i["txn_id"] else None
        if t and t["error_code"]: by_code[t["error_code"]].append((i, t))

    found, clustered = [], set()
    for code, rows in by_code.items():
        for run in _runs(rows, CLUSTER_GAP, lambda x: x[0]["filed"]):
            if len(run) < WATCH_MIN: continue
            clustered.update(i["ref"] for i, _ in run)
            a, b = min(t["ts"] for _, t in run) - timedelta(hours=1), max(t["ts"] for _, t in run) + timedelta(hours=1)
            everything = window(a, b); failed = [t for t in everything if t["error_code"] == code]
            complained = {t["customer_id"] for _, t in run}
            silent = {t["customer_id"] for t in failed if t["debited"] == "1"} - complained
            stuck = [t for t in failed if unreversed(t)]
            late = [t for t in stuck if as_of > t["ts"] + timedelta(hours=REVERSAL_HOURS.get(t["rail"], 24))]
            amount = sum(t["amount"] for t in stuck)
            level = "systemic" if len(run) >= SYSTEMIC_MIN else "emerging"
            prio = "low" if level == "emerging" else ("high" if late or amount >= HIGH_AMOUNT else "medium")
            notes = _cause(failed, everything) if failed else []
            found.append(dict(kind="failure_cluster", title=code, level=level, priority=prio,
                              complaints=len(run), channels=dict(Counter(i["channel"] for i, _ in run)),
                              failed_txns=len(failed), silent_customers=len(silent), unreversed=len(stuck),
                              unreversed_amount=amount, overdue=len(late),
                              cause="; ".join(n[3] for n in notes[:3]) or "no single attribute stands out",
                              action=_action(notes), first_complaint=iso(run[0][0]["filed"]),
                              early_warning_at=iso(run[WATCH_MIN - 1][0]["filed"]),
                              systemic_at=iso(run[SYSTEMIC_MIN - 1][0]["filed"]) if len(run) >= SYSTEMIC_MIN else None))

    # a burst of complaints that link to NO failing transaction: rumour or misinformation, not a fault
    loose = [i for i in items if not i["txn_id"]]
    lt = [i["filed"] for i in loose]; dense, trig = set(), []
    for j in range(len(loose)):
        lo = bisect_left(lt, lt[j] - SPIKE_WIN)
        if j - lo + 1 >= SPIKE_MIN: dense.update(range(lo, j + 1)); trig.append(lt[j])
    base = sum(1 for t in txns if t["status"] != "SUCCESS") / len(txns)
    for run in _runs([loose[k] for k in sorted(dense)], SPIKE_WIN, lambda x: x["filed"]):
        a, b = run[0]["filed"], run[-1]["filed"]; inwin = window(a, b)
        rate = sum(1 for t in inwin if t["status"] != "SUCCESS") / max(1, len(inwin))
        if rate <= 1.5 * base:
            found.append(dict(kind="unexplained_spike", title="Spike with no failing transactions", level="spike",
                              priority="medium", complaints=len(run), channels=dict(Counter(i["channel"] for i in run)),
                              failed_txns=0, silent_customers=0, unreversed=0, unreversed_amount=0, overdue=0,
                              cause=f"Failure rate in the window is {rate:.1%} vs normal {base:.1%}: likely a rumour, not a system fault.",
                              action="Check the source channel, publish a clarification, and reassure customers.",
                              first_complaint=iso(run[0]["filed"]), early_warning_at=iso(min(t for t in trig if a <= t <= b)),
                              systemic_at=None))
    order = {"high": 0, "medium": 1, "low": 2}
    found.sort(key=lambda x: (order[x["priority"]], -x["unreversed_amount"], -x["complaints"]))
    isolated = sum(1 for i in items if i["txn_id"] and i["ref"] not in clustered)
    return dict(incidents=found, isolated_cases=isolated)