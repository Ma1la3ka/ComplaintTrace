"""ComplaintTrace synthetic data generator.

Run:  python generate_data.py          (standard library only, no installs)
Out:  complainttrace_data/
        transactions.csv   what the bank's transaction log would show
        complaints.csv     what the complaint system sees (raw text + channel). NO answers in here.
        truth.csv          the answers, for scoring: extraction, linking, incident detection
        incidents.csv      the failures we seeded (ground truth for detection + root cause)

Everything is SYNTHETIC. Bank names are fictional. Say so in the pitch.
The complaint fields use the same issue_type values as the voice app, so voice
complaints can be added later as channel = voice_assistant.
"""
import csv, os, random
from datetime import datetime, timedelta

random.seed(2026)
START, DAYS, TXN_PER_DAY = datetime(2026, 9, 14), 21, 4000
END = START + timedelta(days=DAYS)
OUT = "complainttrace_data"
os.makedirs(OUT, exist_ok=True)

BANKS = ["Unity Trust", "Sahel Bank", "Eko Mutual", "Delta Commerce",
         "Niger Capital", "Rivers Union", "Lagoon Bank", "Harmattan Bank"]
BANK_W = [.14, .25, .13, .12, .10, .10, .09, .07]          # Sahel Bank gets heavy traffic
REGIONS = ["Lagos", "Abuja", "Ibadan", "Port Harcourt", "Kano", "Enugu"]
REGION_W = [.40, .20, .12, .12, .10, .06]
RAIL_W = {"NIP_TRANSFER": .38, "CARD_POS": .20, "CARD_WEB": .08, "ATM": .10, "AIRTIME_DATA": .08}
# remaining share handled below so weights sum to what random.choices normalises
HOUR_W = [1,1,1,1,1,2,4,7,9,9,7,6,8,8,6,5,5,7,9,9,8,6,3,2]
BASE_FAIL = {"NIP_TRANSFER": .012, "CARD_POS": .02, "CARD_WEB": .03, "ATM": .015, "AIRTIME_DATA": .015}
BASE_CODES = {"NIP_TRANSFER": ["NIP_ERR_96", "NIP_ERR_25"], "CARD_POS": ["POS_DECLINE_05", "POS_TIMEOUT_68"],
              "CARD_WEB": ["WEB_3DS_FAIL", "WEB_TIMEOUT"], "ATM": ["ATM_DISP_01", "ATM_COMM_ERR"],
              "AIRTIME_DATA": ["VEND_ERR_12", "VEND_ERR_40"]}
ATMS = [(f"ATM-{i:03d}", random.choices(REGIONS, REGION_W)[0]) for i in range(1, 121)]
POSS = [(f"POS-{i:03d}", random.choices(REGIONS, REGION_W)[0]) for i in range(1, 301)]

def d(day, hh, mm=0):
    return START + timedelta(days=day, hours=hh, minutes=mm)

# ---- seeded systemic incidents (the thing the system must discover) -------------------
INCIDENTS = [
    dict(id="INC-01", kind="systemic", start=d(11, 18), end=d(11, 23, 30), rate=.65, code="NIP_TIMEOUT_91", debit=.9,
         pred=lambda t: t["rail"] == "NIP_TRANSFER" and t["counterparty_bank"] == "Sahel Bank",
         scope="NIP transfers to Sahel Bank", cause="Counterparty gateway timing out (Sahel Bank NIP link)"),
    dict(id="INC-02", kind="systemic", start=d(15, 6), end=d(15, 18), rate=.50, code="ATM_DISP_07", debit=.9,
         pred=lambda t: t["rail"] == "ATM" and t["region"] == "Lagos",
         scope="ATM withdrawals, Lagos region", cause="Cash dispenser fault after software push to Lagos ATM fleet"),
    dict(id="INC-03", kind="systemic", start=d(17, 9), end=d(17, 13), rate=.70, code="VEND_PENDING_88", debit=.95,
         pred=lambda t: t["rail"] == "AIRTIME_DATA",
         scope="Airtime and data purchases, all channels", cause="Airtime vendor API returning pending, no top-up delivered"),
    dict(id="INC-04", kind="systemic", start=d(19, 12), end=d(19, 18), rate=.55, code="USSD_SESSION_TIMEOUT", debit=.5,
         pred=lambda t: t["channel"] == "ussd",
         scope="All USSD transactions", cause="USSD gateway session timeouts"),
]
RUMOR = dict(id="RUMOR-01", kind="rumor_no_failure", start=d(13, 14), end=d(13, 20),
             scope="Social media claims the bank was hacked", cause="No matching transaction failures: rumor, not a system fault")

# ---- transactions ---------------------------------------------------------------------
def pick_channel(rail):
    if rail == "NIP_TRANSFER": return random.choices(["mobile_app", "ussd", "web"], [.6, .25, .15])[0]
    if rail == "AIRTIME_DATA": return random.choice(["mobile_app", "ussd"])
    return {"CARD_POS": "pos", "CARD_WEB": "web", "ATM": "atm"}[rail]

def amount_for(rail):
    if rail == "AIRTIME_DATA": return random.choice([100, 200, 500, 1000, 1500, 2000])
    return int(min(max(random.lognormvariate(8.8, 1.0), 500), 800000) // 100 * 100)

txns = []
n = 0
for day in range(DAYS):
    for _ in range(TXN_PER_DAY):
        n += 1
        hh = random.choices(range(24), HOUR_W)[0]
        ts = d(day, hh, random.randint(0, 59)) + timedelta(seconds=random.randint(0, 59))
        rail = random.choices(list(RAIL_W), list(RAIL_W.values()))[0]
        t = dict(txn_id=f"TXN{n:08d}", ts=ts, rail=rail, channel=pick_channel(rail),
                 customer_id=f"C{random.randint(1, 5000):05d}", amount=amount_for(rail),
                 counterparty_bank="", beneficiary_account="", terminal_id="", region="")
        if rail == "NIP_TRANSFER":
            t["counterparty_bank"] = random.choices(BANKS, BANK_W)[0]
            t["beneficiary_account"] = str(random.randint(10**9, 10**10 - 1))
        if rail == "ATM": t["terminal_id"], t["region"] = random.choice(ATMS)
        if rail == "CARD_POS": t["terminal_id"], t["region"] = random.choice(POSS)
        if not t["region"]: t["region"] = random.choices(REGIONS, REGION_W)[0]

        inc = next((i for i in INCIDENTS if i["start"] <= ts < i["end"] and i["pred"](t)), None)
        p = inc["rate"] if inc else BASE_FAIL[rail]
        failed = random.random() < p
        t.update(incident=inc["id"] if (inc and failed) else "", status="SUCCESS", error_code="",
                 debited=0, reversed_at="")
        if failed:
            t["status"] = "PENDING" if (inc and inc["id"] == "INC-03") else "FAILED"
            t["error_code"] = inc["code"] if inc else random.choice(BASE_CODES[rail])
            t["debited"] = int(random.random() < (inc["debit"] if inc else .5))
            if t["debited"]:
                fast = .4 if inc else .75          # incidents leave more cases unreversed
                if random.random() < fast:
                    t["reversed_at"] = ts + timedelta(hours=random.uniform(1, 36))
        txns.append(t)

# ---- complaint text -------------------------------------------------------------------
NAMES = ["Adaeze Okafor", "Tunde Bakare", "Musa Ibrahim", "Ngozi Eze", "Funke Adeyemi", "Emeka Obi", "Aisha Bello"]
def money(a):
    return random.choice([f"N{a:,}", f"₦{a:,}", f"{a} naira", f"{a:,} naira"])

def when_phrase(txn_ts, filed):
    days = (filed.date() - txn_ts.date()).days
    part = "this morning" if txn_ts.hour < 12 else "this afternoon" if txn_ts.hour < 17 else "tonight"
    h = txn_ts.hour % 12 or 12
    clock = f"around {h}{'am' if txn_ts.hour < 12 else 'pm'}"
    if days == 0: return random.choice([part, clock, "earlier today"])
    if days == 1: return random.choice(["yesterday", f"yesterday {clock}", "last night" if txn_ts.hour >= 17 else "yesterday"])
    return f"{days} days ago"

EN = {
 "failed_transfer": ["I sent {amt} {when} and it has not reached the receiver but my account was debited.",
                     "Transfer of {amt} {when} failed. Money left my account, beneficiary has not received it.",
                     "My transfer of {amt} {when} is pending and the person has not received it. Please reverse."],
 "debit_no_value": ["I bought {amt} airtime {when}, I was debited but never got the airtime.",
                    "Debited {amt} for data {when} and no data received."],
 "atm_dispense": ["I tried to withdraw {amt} at the ATM {when}. No cash came out but I was debited.",
                  "ATM did not dispense {amt} {when}, account debited."],
 "pos_or_web": ["My card was charged {amt} {when} but the payment failed at the merchant.",
                "I was debited {amt} {when} on a card payment that did not go through."],
 "suspected_fraud": ["I did not authorise a debit of {amt} {when}. Please investigate.",
                     "Someone took {amt} from my account {when}. I did not do this transaction."],
}
PCM = {
 "failed_transfer": ["I send {amt} {when}, e no reach the person but una don debit me. Abeg help me.",
                     "Dem debit me {amt} {when}, the transfer fail. My money dey where?"],
 "debit_no_value": ["I buy {amt} airtime {when}, dem debit me but airtime no enter."],
 "atm_dispense": ["I dey withdraw {amt} for ATM {when}, e no give me cash but dem debit me."],
 "pos_or_web": ["Dem charge my card {amt} {when} but the payment no pass."],
 "suspected_fraud": ["I no do this {amt} transaction {when}. Who take my money?"],
}
OTHER = ["Why was I charged an SMS alert fee this month?", "The mobile app is very slow and keeps logging me out.",
         "I need a new debit card, the old one is damaged.", "USSD code is not responding for me today.",
         "Please update my BVN details on my account.", "I want to know my account statement fee.",
         "App keeps crashing when I open it.", "Why was a maintenance charge taken from my account?"]
RUMOR_TXT = ["Heard the bank has been hacked, is my money safe?", "People saying the bank is hacked. Withdraw your money now!!",
             "Is it true that the bank system is down and customers are losing money?",
             "My balance is not showing, I think the bank has been hacked"]

def style(channel, text, name):
    if channel == "twitter": return f"@OurBank {text} {random.choice(['#fixthis', 'Disappointed.', 'Respond abeg.', ''])}".strip()
    if channel == "email": return f"Dear Sir/Madam, {text} Kindly resolve urgently. Regards, {name}"
    if channel == "call_center": return f"Caller states: {text.lower()} Caller requests resolution."
    if channel == "branch": return f"Walk-in customer. {text} Customer wants it escalated."
    if channel == "in_app_chat": return text.lower()
    return text

CH_W = {"call_center": .30, "in_app_chat": .22, "twitter": .16, "email": .10, "branch": .13, "voice_assistant": .09}
refs = set()
def new_ref():
    while True:
        r = "CMP-" + "".join(random.choices("0123456789ABCDEF", k=8))
        if r not in refs:
            refs.add(r); return r

complaints, truth = [], []
def add(created, channel, language, text, customer, gt_issue, gt_amt, gt_bank, gt_txn, gt_inc, gt_stated=""):
    if created >= END: return
    ref = new_ref()
    complaints.append(dict(ref=ref, created_at=created, channel=channel, language=language,
                           customer_id=customer, text=text))
    truth.append(dict(ref=ref, gt_issue_type=gt_issue, gt_amount=gt_amt, gt_bank=gt_bank,
                      gt_txn_id=gt_txn, gt_incident_id=gt_inc, gt_stated_amount=gt_stated))

def issue_for(t):
    return {"NIP_TRANSFER": "failed_transfer", "AIRTIME_DATA": "debit_no_value", "ATM": "atm_dispense",
            "CARD_POS": "pos_or_web", "CARD_WEB": "pos_or_web"}[t["rail"]]

def complaint_from_txn(t, issue, incident):
    lag = min(random.expovariate(1 / 3.0), 70)                   # hours, mean about 3
    filed = t["ts"] + timedelta(hours=lag, minutes=random.randint(1, 30))
    channel = random.choices(list(CH_W), list(CH_W.values()))[0]
    pcm = random.random() < .25
    base = random.choice((PCM if pcm else EN)[issue])
    amt_shown = t["amount"] if random.random() > .06 else max(100, t["amount"] + random.choice([-500, 500, 1000]))  # 6% wrong, never below 100
    text = base.format(amt=money(amt_shown), when=when_phrase(t["ts"], filed))
    if issue == "failed_transfer" and random.random() < .45:
        text += f" Beneficiary account {t['beneficiary_account']}."
    if issue == "failed_transfer" and t["counterparty_bank"] and random.random() < .35:
        text += f" It was to a {t['counterparty_bank']} account."
    if t["channel"] == "ussd" and random.random() < .5: text += " It was on USSD."
    if random.random() < .40: text += f" Ref: {t['txn_id']}."
    text = style(channel, text, random.choice(NAMES))
    cust = t["customer_id"] if (channel != "twitter" and random.random() < .85) or random.random() < .15 else ""
    add(filed, channel, "pcm" if pcm else "en", text, cust, issue, t["amount"],
        t["counterparty_bank"], t["txn_id"], incident, amt_shown)

for t in txns:
    if t["status"] == "SUCCESS": continue
    inc = t["incident"]
    p = (.35 if t["debited"] else .10) if inc else (.15 if t["debited"] else .04)
    if random.random() < p:
        complaint_from_txn(t, issue_for(t), inc)

# decoys: disputed *successful* transactions (suspected fraud, not systemic) ~2/day
ok = [t for t in txns if t["status"] == "SUCCESS"]
for t in random.sample(ok, DAYS * 2):
    complaint_from_txn(t, "suspected_fraud", "")
# noise: general complaints with no transaction ~10/day
for day in range(DAYS):
    for _ in range(10):
        ch = random.choices(list(CH_W), list(CH_W.values()))[0]
        txt = style(ch, random.choice(OTHER), random.choice(NAMES))
        add(d(day, random.choices(range(24), HOUR_W)[0], random.randint(0, 59)), ch, "en", txt,
            f"C{random.randint(1, 5000):05d}" if random.random() < .7 else "", "other", "", "", "", "")
# rumor spike: many tweets/calls, no failed transactions behind them
for _ in range(90):
    ch = random.choices(["twitter", "call_center", "in_app_chat"], [.7, .15, .15])[0]
    add(RUMOR["start"] + timedelta(minutes=random.randint(0, 360)), ch, "en",
        style(ch, random.choice(RUMOR_TXT), random.choice(NAMES)), "", "other", "", "", "", RUMOR["id"])

# ---- write ----------------------------------------------------------------------------
def write(name, rows, cols):
    with open(os.path.join(OUT, name), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in sorted(rows, key=lambda r: str(r.get(cols[1], ""))) if name != "incidents.csv" else rows:
            w.writerow({c: (r[c].strftime("%Y-%m-%d %H:%M:%S") if isinstance(r.get(c), datetime) else r.get(c, "")) for c in cols})

write("transactions.csv", txns, ["txn_id", "ts", "rail", "channel", "customer_id", "amount", "counterparty_bank",
      "beneficiary_account", "terminal_id", "region", "status", "error_code", "debited", "reversed_at"])
write("complaints.csv", complaints, ["ref", "created_at", "channel", "language", "customer_id", "text"])
write("truth.csv", truth, ["ref", "gt_issue_type", "gt_amount", "gt_stated_amount", "gt_bank", "gt_txn_id", "gt_incident_id"])
write("txn_truth.csv", [dict(txn_id=t["txn_id"], incident_id=t["incident"]) for t in txns if t["incident"]],
      ["txn_id", "incident_id"])
write("incidents.csv", [dict(incident_id=i["id"], kind=i["kind"], start=i["start"], end=i["end"], scope=i["scope"],
      error_code=i.get("code", ""), root_cause_truth=i["cause"]) for i in INCIDENTS + [RUMOR]],
      ["incident_id", "kind", "start", "end", "scope", "error_code", "root_cause_truth"])

failed = [t for t in txns if t["status"] != "SUCCESS"]
print(f"transactions: {len(txns):,} ({len(failed):,} failed/pending)")
print(f"complaints:   {len(complaints):,}")
for i in INCIDENTS:
    nf = sum(1 for t in txns if t["incident"] == i["id"])
    nc = sum(1 for x in truth if x["gt_incident_id"] == i["id"])
    print(f"  {i['id']}: {nf} failed txns, {nc} complaints")
print(f"  {RUMOR['id']}: {sum(1 for x in truth if x['gt_incident_id'] == RUMOR['id'])} complaints, 0 failed txns")
print("files written to", OUT)