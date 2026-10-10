"""ComplaintTrace decision engine: solve the case automatically when that is safe, hand it to a human when it is not.

Every decision carries its reasons, so a human can see WHY. Actions are SIMULATED: no real bank system is connected.
Settings (environment variables): ESCALATE_AMOUNT (default 500000).
Refund timelines live in reversals.py, which says which numbers are CBN rules and which are our assumptions.
"""
import os
from datetime import timedelta
from reversals import POLICY, policy_for

ESCALATE_AMOUNT = float(os.environ.get("ESCALATE_AMOUNT", 500000))
MIN_CONFIDENCE = 0.75
REVERSAL_HOURS = {rail: p["deadline_hours"] for rail, p in POLICY.items()}   # from reversals.py; kept because incidents.py uses it

# What the caller is told. Deliberately general: callers are not verified, so no account or transaction details.
SAY = {
    "caller_request": "I have passed your complaint to a bank officer, as you asked. They will contact you.",
    "not_understood": "I could not fully understand, so I have passed what you said to a bank officer, who will contact you.",
    "fraud": "This may be an unauthorised transaction, so I have marked it urgent and passed it to a bank officer. If your card is lost or stolen, call the bank straight away.",
    "no_match": "I could not find a matching transaction, so I have recorded your complaint and passed it to a bank officer, who will contact you.",
    "several_matches": "I found more than one possible match, so I have passed your complaint to a bank officer, who will check it and contact you.",
    "not_a_transaction": "I have recorded your complaint and passed it to a bank officer, who will contact you.",
    "low_confidence": "I am not fully sure I found the right transaction, so I have passed it to a bank officer, who will check it and contact you.",
    "high_value": "Because of the amount, I have passed your complaint to a bank officer, who will contact you.",
    "txn_succeeded": "Our records show that transaction went through, so I have passed it to a bank officer, who will look into it and contact you.",
    "refund_overdue": "We confirmed this failed. The refund is later than normal, so I have marked it urgent and passed it to a bank officer.",
    "already_reversed": "Our records show the money from this failed transaction has already been returned to your account.",
    "no_debit": "Our records show this transaction failed and no money was taken from your account.",
    "reversal_started": "We confirmed this transaction failed and have started the refund. Expect it within {hours} hours.",
}


def _out(decision, code, why, priority="normal", action="", extra="", **fmt):
    msg = SAY[code].format(**fmt) + (" " + extra if extra else "")
    return dict(decision=decision, priority=priority, reasons=[code, why], action=action,
                caller_message=msg, human_required=decision == "needs_human")


def decide(call, link, txn, alert, filed, reversed_by_then):
    """call: dict(status, reason, issue). link: dict(status, conf, cands). txn/alert may be None."""
    human = lambda code, why, priority="normal", action="": _out("needs_human", code, why, priority, action)
    if call.get("status") == "escalated":
        return human("caller_request", f"Caller asked for a person ({call.get('reason') or 'escalated'})")
    if call.get("status") == "review":
        return human("not_understood", "The assistant could not understand the call well enough")
    if call.get("issue") == "suspected_fraud":
        return human("fraud", "Possible unauthorised transaction", "high", "Recommend card review (not executed)")
    if link["status"] == "not_linkable":
        return human("not_a_transaction", "Not about a specific transaction")
    if link["status"] == "unmatched":
        return human("no_match", "No transaction in the log fits what the caller described")
    if link["status"] == "ambiguous":
        return human("several_matches", "Several transactions fit about equally: " + (link.get("cands") or ""))
    if float(link.get("conf") or 0) < MIN_CONFIDENCE:
        return human("low_confidence", f"Match confidence {link.get('conf')} is below {MIN_CONFIDENCE}")
    if txn["amount"] >= ESCALATE_AMOUNT:
        return human("high_value", f"Amount {txn['amount']:,} is at or above the {ESCALATE_AMOUNT:,.0f} limit", "high")
    if txn["status"] == "SUCCESS":
        return human("txn_succeeded", "The matched transaction was successful")

    # From here the transaction really failed, so the system can often settle it alone.
    hours = policy_for(txn["rail"])["deadline_hours"]
    incident = f" Part of known incident {alert['id']} ({alert['label']})." if alert else ""
    note = " This was caused by a known service problem that is also affecting other customers." if alert else ""
    if txn["debited"] != "1":
        return _out("auto_resolved", "no_debit", "Failed transaction, customer was not debited" + incident,
                    action="Closed: nothing to refund")
    if reversed_by_then:
        return _out("auto_resolved", "already_reversed", "Failed transaction, already reversed" + incident,
                    action="Closed: refund already processed", extra=note)
    if filed > txn["ts"] + timedelta(hours=hours):
        return human("refund_overdue", f"Debited, not reversed, past the {hours}h limit" + incident, "high",
                     "Chase refund urgently")
    return _out("auto_resolved", "reversal_started", f"Failed and debited; reversal due within {hours}h" + incident,
                action=f"Reversal requested (SIMULATED), due within {hours}h", extra=note, hours=hours)