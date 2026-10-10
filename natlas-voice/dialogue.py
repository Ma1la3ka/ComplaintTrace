"""Complaint dialogue: fixed script, read-back confirmation, rule-based escalation, N-ATLAS for language work."""
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from slot_guard import drop_invented

REQUIRED = ["issue_type", "amount", "when", "bank"]
ISSUE_TYPES = ["failed_transfer", "debit_no_value", "atm_dispense", "pos_or_web",
               "card", "ussd_or_app", "suspected_fraud", "other"]
LANGUAGES = {"yo": "Yoruba", "ha": "Hausa", "ig": "Igbo", "en": "English"}

GREETING = ("Hello. I am an AI assistant for bank complaints. I will ask a few short questions "
            "and pass your complaint to the bank. Please never tell me your PIN, OTP, password "
            "or full card number. You can ask for a human at any time. What happened?")

QUESTIONS = {
    "issue_type": "Please tell me what happened. For example, a transfer that did not arrive, "
                  "an ATM that did not pay out, or a payment you do not recognise.",
    "amount": "How much money was involved, in naira?",
    "when": "When did this happen? A day and rough time is fine.",
    "bank": "Which bank is your account with?",
}
RETRY = "Sorry, I did not catch that. Please say it again."
HANDOFF = ("I am connecting you to a person now. I have passed on what you told me, "
           "so you will not need to repeat it.")
DONE = ("Thank you. Your complaint is recorded. The bank team will review it. "
        "Please never share your PIN or OTP with anyone. Your reference number is")

# read-back confirmation
READBACK_LEAD = "Let me check what I understood:"
CONFIRM_ASK = "Is that correct? Please say yes or no."
CORRECT_PROMPT = "Sorry about that. Please tell me what is wrong: the problem, the amount, the time or the bank."
FRAUD_ASK = ("It sounds like money may have been taken from your account without your permission. "
             "Is that right? Please say yes or no.")
REVIEW_NOTICE = ("I am sorry, I could not understand everything. I have saved what you said and the bank team "
                 "will review it. Your reference number is")
ISSUE_LABELS = {
    "failed_transfer": "money sent but not received",
    "debit_no_value": "account debited but service not given",
    "atm_dispense": "ATM did not pay out",
    "pos_or_web": "card payment problem",
    "card": "card problem",
    "ussd_or_app": "USSD or app problem",
    "suspected_fraud": "suspected fraud",
    "other": "another problem",
}

FIXED_STRINGS = [GREETING, *QUESTIONS.values(), RETRY, HANDOFF, DONE,
                 READBACK_LEAD, CONFIRM_ASK, CORRECT_PROMPT, FRAUD_ASK, REVIEW_NOTICE, *ISSUE_LABELS.values()]

EXTRACT_SYSTEM = """You read a bank customer's message, which may be in Yoruba, Hausa, Igbo or English, and describe it in English.
Return ONLY a JSON object. Include a key only when the customer gave that information, and leave out every key that would be null or false. The keys are:
issue_type: one of failed_transfer (money sent but not received), debit_no_value (account debited but service not given), atm_dispense (ATM did not pay out), pos_or_web (card payment problem), card, ussd_or_app, suspected_fraud (money taken without the customer's permission, scam, hacked account, unauthorised transaction), other
amount: number in naira
when: short time in English letters, translated into English even if the customer spoke Yoruba, Hausa or Igbo (for example "yesterday evening")
bank: bank name
beneficiary_account: 10-digit account number as a string
wants_human: true ONLY if the customer clearly asks to speak to a person or agent. Saying that SOMEONE did something (for example someone sent money, or the Yoruba word enikan which means someone) is NOT a request. Wanting to do something else, such as go somewhere or buy something, is NOT a request for a person.
confirmation: "yes" if the customer agrees that a summary is correct, "no" if they say it is wrong. Yes can be yes, bẹ́ẹ̀ni, ee, eh. No can be no, rárá, mba, a'a.
If the message is gibberish, random letters, or does not make sense, return {}.
Callers mix English into Yoruba, Hausa and Igbo, and the transcript spells English words the way they sound: transfer is written ránsúfà, account is àkáùntì, naira is náírà. Read them as the English words. A customer who cannot see money in their account, or says a transfer has not arrived, has a failed_transfer, NOT fraud. Use suspected_fraud only when the customer says someone took money or accessed the account without permission.
Callers often say English numbers inside Yoruba, Hausa or Igbo, and the transcript spells them the way they sound. For example "táti ta ọsán náírà" means thirty thousand naira. Always work out the whole number, including the part before "thousand". Never return only 1000 for a larger amount.
In numbers, a dot or comma followed by three digits is a thousands separator: 20.000 and 20,000 both mean twenty thousand, never twenty.
Do not guess. Never include PINs, OTPs or card numbers.
Example input: I want to speak to a person
Example output: {"wants_human":true}
Example input: I sent 5000 naira to my brother and it did not arrive
Example output: {"issue_type":"failed_transfer","amount":5000}
Example input: Someone took money from my account and I did not authorise it
Example output: {"issue_type":"suspected_fraud"}
Example input: yes, that is correct
Example output: {"confirmation":"yes"}
Example input: no, that is wrong
Example output: {"confirmation":"no"}
Example input: táti ta ọsán náírà
Example output: {"amount":30000}
Example input: irọlẹ àná
Example output: {"when":"yesterday evening"}
Example input: qwerty zzz
Example output: {}
Example input: asdf
Example output: {}"""

TRANSLATE_SYSTEM = ("You are a translator. Translate the user's message into {lang}. "
                    "The message is something a bank assistant will say to a caller. "
                    "If the message is a question, translate the question itself and never answer it. "
                    "Translate it faithfully: do not add numbers, amounts or facts, "
                    "and do not explain. Use simple spoken {lang}. Output only the translation.")


class Raw(str):
    """Text that is shown as it is and never sent for translation (numbers, names)."""


@dataclass
class Config:
    escalate_amount: float = 500_000
    max_turns: int = 16
    max_misses: int = 2


@dataclass
class Session:
    language: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ref: str = field(default_factory=lambda: "CMP-" + uuid.uuid4().hex[:8].upper())
    status: str = "active"          # active | logged | escalated | review
    reason: str = ""
    slots: dict = field(default_factory=dict)
    misses: int = 0
    turns: int = 0
    last_question: str = GREETING


@dataclass
class TurnResult:
    reply_en: str
    reply_local: str
    status: str
    reason: str
    transcript: str
    extracted: dict


def parse_amount(value):
    """Turn what the model returned into naira. A dot or comma followed by exactly three digits
    is a thousands separator (20.000 and 20,000 both mean twenty thousand)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        s = re.sub(r"[^\d.,]", "", value)
        if not s:
            return None
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
            return float(re.sub(r"[.,]", "", s))
        s = s.replace(",", ".")
        try:
            v = float(s) if s.count(".") <= 1 else None
        except ValueError:
            return None
        return v if v and v > 0 else None
    return None


def thousands_number(text: str):
    """Find a number written with thousands separators in the caller's own words, like 20.000 or 1,500,000."""
    m = re.search(r"\d{1,3}(?:[.,]\d{3})+(?!\d)", text or "")
    return float(re.sub(r"[.,]", "", m.group(0))) if m else None


def parse_extraction(raw: str) -> dict:
    """Pull a JSON object out of model output and clean every field."""
    if not raw:
        return {}
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    it = str(data.get("issue_type") or "").lower().replace(" ", "_")
    if it in ISSUE_TYPES:
        out["issue_type"] = it
    elif any(w in it for w in ("fraud", "unauthor", "scam", "hack", "stolen", "theft")):
        out["issue_type"] = "suspected_fraud"
    elif it and it != "null":
        out["issue_type"] = "other"
    amt = parse_amount(data.get("amount"))
    if amt:
        out["amount"] = amt
    for key, limit in (("when", 80), ("bank", 60)):
        v = data.get(key)
        if isinstance(v, str) and v.strip() and v.strip().lower() != "null":
            out[key] = v.strip()[:limit]
    acct = data.get("beneficiary_account")
    if isinstance(acct, (str, int)) and re.fullmatch(r"\d{10}", str(acct)):
        out["beneficiary_account"] = str(acct)   # only a valid 10-digit account number is kept
    out["wants_human"] = data.get("wants_human") is True
    out["frustrated"] = data.get("frustrated") is True
    conf = str(data.get("confirmation") or "").strip().lower()
    out["confirmation"] = conf if conf in ("yes", "no") else None
    return out


YES_WORDS = {"yes", "yeah", "yep", "yup", "ok", "okay", "correct", "right", "beeni", "ee", "eeh", "eh"}
NO_WORDS = {"no", "nope", "wrong", "incorrect", "rara", "beeko", "mba", "aa"}


def keyword_confirmation(text: str):
    """Recognise a plain yes or no, including Yoruba, Hausa and Igbo words, ignoring tone marks.
    Returns "yes", "no" or None (None means: let the model decide)."""
    if not text or re.search(r"\d", text):          # a number means a correction: let the model read it
        return None
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").replace("'", "").replace("\u2019", "")
    tokens = re.findall(r"[a-z]+", t)
    if not tokens or len(tokens) > 3:
        return None
    if tokens[0] in YES_WORDS:
        return "yes"
    if tokens[0] in NO_WORDS:
        return "no"
    return None


def next_question(slots: dict):
    for key in REQUIRED:
        if key not in slots:
            return QUESTIONS[key]
    return None


def readback_parts(slots: dict) -> list:
    issue = ISSUE_LABELS.get(slots.get("issue_type"), ISSUE_LABELS["other"])
    amount = f"₦{slots['amount']:,.0f}"
    return [READBACK_LEAD, issue, Raw(f"- {amount} - {slots.get('when')} - {slots.get('bank')}."), CONFIRM_ASK]


def check_escalation(session: Session, extracted: dict, cfg: Config):
    """Immediate handoffs only: the caller asked for a person, or the amount is above the limit."""
    if extracted.get("wants_human"):
        return "caller_requested_human"
    if session.slots.get("amount", 0) >= cfg.escalate_amount:
        return "high_value"
    return None


def handoff_summary(session: Session, transcripts: list) -> str:
    s = session.slots
    lines = [f"Reference {session.ref}. Language: {LANGUAGES.get(session.language, session.language)}.",
             f"Escalation reason: {session.reason}."]
    for key in REQUIRED + ["beneficiary_account"]:
        lines.append(f"{key}: {s.get(key, 'not given')}")
    if transcripts:
        lines.append("Last caller messages: " + " | ".join(transcripts[-3:]))
    return "\n".join(lines)


class DialogueEngine:
    def __init__(self, client, cfg: Config = None, translations_path: str = None):
        self.client = client
        self.cfg = cfg or Config()
        self._cache = {}
        self.fixed = {}
        path = translations_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "translations.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.fixed = json.load(f)

    def localize_live(self, text_en: str, language: str) -> str:
        system = TRANSLATE_SYSTEM.format(lang=LANGUAGES[language])
        return self.client.generate(system, [{"role": "user", "content": text_en}],
                                    max_new_tokens=250, temperature=0.1)

    def localize(self, text_en: str, language: str) -> str:
        if isinstance(text_en, Raw):
            return str(text_en)
        if language == "en":
            return text_en                     # English callers hear the English text as it is
        stored = self.fixed.get(language, {}).get(text_en)
        if stored:
            return stored                      # pre-translated: no model call, instant
        key = (language, text_en)
        if key not in self._cache:
            self._cache[key] = self.localize_live(text_en, language)
        return self._cache[key]

    def greeting(self, language: str) -> tuple:
        return GREETING, self.localize(GREETING, language)

    def process_turn(self, session: Session, transcript: str) -> TurnResult:
        session.turns += 1
        transcript = (transcript or "").strip()
        extracted = {}
        stage = session.last_question

        kw = keyword_confirmation(transcript) if stage in (CONFIRM_ASK, FRAUD_ASK) else None
        if kw:                                              # a plain yes or no: no model call needed
            extracted = {"wants_human": False, "frustrated": False, "confirmation": kw}
        elif transcript:
            user_msg = f"Question asked: {stage}\nCustomer said: {transcript}"
            raw = self.client.generate(EXTRACT_SYSTEM, [{"role": "user", "content": user_msg}],
                                       max_new_tokens=200, temperature=0.0)
            if os.environ.get("DEBUG_EXTRACT"):
                print("RAW EXTRACTION:", raw, flush=True)
            extracted = parse_extraction(raw)
            extracted = drop_invented(extracted, transcript, session.language)

        num = thousands_number(transcript)
        if num is not None and (stage == QUESTIONS["amount"] or "amount" in extracted):
            extracted["amount"] = num        # trust the digits the speech model wrote

        before = dict(session.slots)
        for key in REQUIRED + ["beneficiary_account"]:
            if key in extracted:
                session.slots[key] = extracted[key]
        progressed = session.slots != before
        if extracted.get("wants_human") and progressed:
            extracted["wants_human"] = False   # complaint details in the same message: likely a false alarm
        conf = extracted.get("confirmation")
        understood = progressed or extracted.get("wants_human") or conf in ("yes", "no")
        session.misses = 0 if understood else session.misses + 1

        reason = check_escalation(session, extracted, self.cfg)
        fraud_pending = session.slots.get("issue_type") == "suspected_fraud"
        parts, tail = [], ""                   # parts are fixed strings; tail is added untranslated
        if reason:                                             # caller asked for a person, or a very large amount
            session.status, session.reason = "escalated", reason
            parts = [HANDOFF]
        elif session.misses >= self.cfg.max_misses or session.turns >= self.cfg.max_turns:
            session.status, session.reason = "review", "needs_review"   # keep what they said, no handoff
            parts, tail = [REVIEW_NOTICE], session.ref + "."
        elif stage == FRAUD_ASK and fraud_pending and conf == "yes":    # caller confirmed possible fraud
            session.status, session.reason = "escalated", "suspected_fraud"
            parts = [HANDOFF]
        elif stage == FRAUD_ASK and fraud_pending and conf == "no":     # not fraud: start over on the problem
            session.slots.pop("issue_type", None)
            parts = [QUESTIONS["issue_type"]]
            session.last_question = QUESTIONS["issue_type"]
        elif fraud_pending and stage != FRAUD_ASK:                      # the model guessed fraud: check first
            parts = [FRAUD_ASK]
            session.last_question = FRAUD_ASK
        elif fraud_pending:                                             # unclear answer to the fraud question
            parts = [RETRY, FRAUD_ASK]
        else:
            q = next_question(session.slots)
            if q:                                              # still collecting details
                parts = [q] if (progressed or session.turns == 1) else [RETRY, q]
                session.last_question = q
            elif stage in (CONFIRM_ASK, CORRECT_PROMPT) and not progressed:
                if stage == CONFIRM_ASK and conf == "yes":     # caller confirmed: save
                    session.status = "logged"
                    parts, tail = [DONE], session.ref + "."
                elif stage == CONFIRM_ASK and conf == "no":    # caller says it is wrong
                    parts = [CORRECT_PROMPT]
                    session.last_question = CORRECT_PROMPT
                else:                                          # did not understand the answer
                    parts = [RETRY, stage]
            else:                                              # everything filled or corrected: read back
                parts = readback_parts(session.slots)
                session.last_question = CONFIRM_ASK

        reply_en = " ".join(parts) + ((" " + tail) if tail else "")
        reply_local = " ".join(self.localize(p, session.language) for p in parts) + ((" " + tail) if tail else "")
        return TurnResult(reply_en, reply_local, session.status, session.reason, transcript, extracted)