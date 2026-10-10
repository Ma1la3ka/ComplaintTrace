"""Automatic reversal engine, modelled on CBN timelines. ALL ACTIONS ARE SIMULATED: no real bank system is connected.

The idea: a failed transaction that debited a customer should be refunded by the system itself, without waiting for a
complaint. This module holds the timelines and decides which failed, debited, unreversed transactions are due.

Each rail has two numbers:
  grace_minutes  - how long the normal automatic reversal is given before this engine steps in
  deadline_hours - the longest the refund may take; after that it counts as OVERDUE (a breach)
`status` says where the number comes from. READ IT: only 'CBN rule' is a final rule. Others are a draft or our assumption.
Sources (checked October 2026): CBN ATM guidelines (March 2026), CBN e-payment circular (June 2020),
joint CBN-NCC exposure draft on airtime and data. Replace every value with the bank's own policy before real use.
"""
from datetime import datetime, timedelta

FMT = "%Y-%m-%d %H:%M:%S"
POLICY = {
    "ATM": dict(grace_minutes=5, deadline_hours=24, status="CBN rule",
                rule="Own-bank ATM failures: reversed instantly (automated within minutes); manual reversal within 24h if that fails. "
                     "Other-bank ATMs allow 48h but are not modelled here."),
    "CARD_POS": dict(grace_minutes=60, deadline_hours=72, status="CBN rule",
                     rule="Failed PoS/Web disputes resolved within 72h (2020 circular; one report says 48h). The 60 minute grace is our assumption."),
    "CARD_WEB": dict(grace_minutes=60, deadline_hours=72, status="CBN rule",
                     rule="Failed PoS/Web disputes resolved within 72h (2020 circular; one report says 48h). The 60 minute grace is our assumption."),
    "AIRTIME_DATA": dict(grace_minutes=0.5, deadline_hours=24, status="Draft",
                         rule="Joint CBN-NCC exposure draft (not final): automatic reversal within 30 seconds. The 24h fallback is our assumption."),
    "NIP_TRANSFER": dict(grace_minutes=60, deadline_hours=24, status="Assumption",
                         rule="No rule found. 24h deadline and 60 minute grace are our assumptions."),
}
DEFAULT = dict(grace_minutes=60, deadline_hours=24, status="Assumption", rule="No rule found. 24h assumed.")


def policy_for(rail):
    return POLICY.get(rail, DEFAULT)


def _reversed_by(t, as_of):
    r = t.get("reversed_at")
    return bool(r) and datetime.strptime(r, FMT) <= as_of


def row_for(t, as_of):
    p = policy_for(t["rail"])
    deadline = t["ts"] + timedelta(hours=p["deadline_hours"])
    late = as_of > deadline
    return dict(txn_id=t["txn_id"], rail=t["rail"], amount=t["amount"], deadline_at=deadline.isoformat(sep=" "),
                overdue=int(late), hours_late=round((as_of - deadline).total_seconds() / 3600, 1) if late else 0.0,
                rule=p["rule"], policy_status=p["status"])


def due(failed_debited, as_of, already):
    """Failed, debited transactions that are not reversed by `as_of`, are past the normal automatic window,
    and have no automatic reversal recorded yet (`already` is a set of transaction ids)."""
    out = []
    for t in failed_debited:
        if t["ts"] > as_of or t["txn_id"] in already or _reversed_by(t, as_of):
            continue
        if as_of < t["ts"] + timedelta(minutes=policy_for(t["rail"])["grace_minutes"]):
            continue                                   # still inside the normal automatic window: give it time
        out.append(row_for(t, as_of))
    return out