import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from dialogue import (DialogueEngine, Session, Config, parse_extraction, next_question,
                      EXTRACT_SYSTEM)
from db import Store
from app import create_app


@pytest.fixture(autouse=True)
def no_complainttrace(monkeypatch):
    """Tests must never send fake complaints to a running ComplaintTrace."""
    monkeypatch.delenv("COMPLAINTTRACE_URL", raising=False)


class FakeClient:
    """Plays scripted extraction results; 'translates' by tagging the language."""
    def __init__(self, scripted):
        self.scripted = list(scripted)

    def generate(self, system, messages, max_new_tokens=300, temperature=0.2):
        if system == EXTRACT_SYSTEM:
            return self.scripted.pop(0) if self.scripted else "{}"
        return "[local] " + messages[0]["content"]

    def transcribe(self, audio, language, filename="x"):
        return "audio text"

    def health(self):
        return {"ok": True}


def j(**kw):
    return json.dumps(kw)


def run(scripted, texts, cfg=None, language="yo"):
    eng = DialogueEngine(FakeClient(scripted), cfg or Config())
    s = Session(language=language)
    last = None
    for t in texts:
        last = eng.process_turn(s, t)
    return s, last


# A failed-transfer call up to the point where the account number is asked.
# The text of the 'when' turn must contain a time word, or the invented-detail guard drops the time.
TRANSFER = [j(issue_type="failed_transfer"), j(amount=2600), j(when="today"), j(bank="Unity")]
TRANSFER_TEXTS = ["a", "b", "today", "d"]


def test_parse_handles_noise_and_bad_values():
    raw = 'Sure! {"issue_type":"failed_transfer","amount":"50,000","bank":"Zenith","beneficiary_account":"123","wants_human":false} thanks'
    out = parse_extraction(raw)
    assert out["issue_type"] == "failed_transfer" and out["amount"] == 50000.0
    assert "beneficiary_account" not in out          # not 10 digits
    assert parse_extraction("no json here") == {}
    assert parse_extraction('{"issue_type":"made_up"}').get("issue_type") == "other"   # unknown types are kept as 'other'
    assert parse_extraction('{"issue_type":"null"}').get("issue_type") is None


def test_pin_like_account_is_dropped():
    assert "beneficiary_account" not in parse_extraction('{"beneficiary_account":"1234"}')
    assert parse_extraction('{"beneficiary_account":"0123456789"}')["beneficiary_account"] == "0123456789"


def test_full_flow_logs_complaint():
    s, last = run([j(issue_type="failed_transfer"), j(amount=20000), j(when="yesterday evening"), j(bank="Zenith")],
                  ["a", "b", "yesterday evening", "d", "skip", "yes"])
    assert s.status == "logged" and s.ref in last.reply_en
    assert next_question(s.slots) is None


# ---- the optional account-number question ----------------------------------------------------
def test_account_number_is_asked_saved_and_read_back():
    s, last = run(TRANSFER + [j(beneficiary_account="1178969835")], TRANSFER_TEXTS + ["1178969835"])
    assert s.slots["beneficiary_account"] == "1178969835"
    assert "1178969835" in last.reply_en             # read back, so a misheard digit can be caught


def test_ten_digits_are_trusted_even_if_the_model_misses_them():
    s, _ = run(TRANSFER, TRANSFER_TEXTS + ["1178969835"])           # the model returns nothing for the last turn
    assert s.slots["beneficiary_account"] == "1178969835"


def test_skip_moves_on_without_an_account():
    s, last = run(TRANSFER, TRANSFER_TEXTS + ["skip"])
    assert "beneficiary_account" not in s.slots and s.status == "active" and s.misses == 0
    assert "Is that correct" in last.reply_en and "account" not in last.reply_en.split("Is that correct")[0].split("-")[-1]


def test_partial_digits_get_one_retry_then_move_on():
    s, last = run(TRANSFER, TRANSFER_TEXTS + ["117896"])
    assert "ten digits" in last.reply_en
    s, last = run(TRANSFER, TRANSFER_TEXTS + ["117896", "117896"])
    assert "Is that correct" in last.reply_en and "beneficiary_account" not in s.slots


def test_account_digits_are_never_taken_as_an_amount():
    s, _ = run(TRANSFER + [j(amount=1178969835)], TRANSFER_TEXTS + ["1178969835"])
    assert s.slots["amount"] == 2600 and s.status == "active"        # no false high-value escalation


def test_account_not_asked_for_other_problems():
    s, last = run([j(issue_type="atm_dispense"), j(amount=5000), j(when="today"), j(bank="Unity")], TRANSFER_TEXTS)
    assert "Is that correct" in last.reply_en and "account number" not in last.reply_en


def test_account_not_asked_again_if_volunteered():
    s, last = run([j(issue_type="failed_transfer", amount=2600, beneficiary_account="1178969835"), j(when="today"), j(bank="Unity")],
                  ["a", "today", "d"])
    assert "Is that correct" in last.reply_en and "1178969835" in last.reply_en


# ---- guard against invented details ----------------------------------------------------------
def test_invented_time_is_dropped_and_asked_for():
    s, last = run([j(issue_type="failed_transfer", amount=5000, when="yesterday evening")], ["Mo fi 5000 naira ránṣẹ́"])
    assert "when" not in s.slots and "When did this happen" in last.reply_en


# ---- escalation rules ------------------------------------------------------------------------
def test_fraud_is_checked_with_the_caller_before_escalating():
    s, last = run([j(issue_type="suspected_fraud")], ["x"])
    assert s.status == "active" and "without your permission" in last.reply_en
    s, _ = run([j(issue_type="suspected_fraud")], ["x", "yes"])
    assert s.status == "escalated" and s.reason == "suspected_fraud"
    s, _ = run([j(issue_type="suspected_fraud")], ["x", "no"])
    assert s.status == "active" and "issue_type" not in s.slots


def test_plain_human_request_escalates():
    s, _ = run([j(wants_human=True)], ["x"])
    assert s.status == "escalated" and s.reason == "caller_requested_human"


def test_complaint_details_beat_a_human_request():
    s, _ = run([j(wants_human=True, issue_type="failed_transfer")], ["x"])      # treated as a false alarm
    assert s.status == "active" and s.slots["issue_type"] == "failed_transfer"


def test_high_value_escalates():
    s, _ = run([j(issue_type="failed_transfer"), j(amount=900000)], ["a", "b"])
    assert s.reason == "high_value"


def test_two_misses_go_to_review_and_empty_audio_counts():
    s, last = run(["{}"], ["", "   "])
    assert s.status == "review" and s.reason == "needs_review" and s.ref in last.reply_en


def test_retry_repeats_question():
    s, last = run([j(issue_type="failed_transfer"), "{}"], ["a", "blah"])
    assert s.status == "active"
    assert "did not catch" in last.reply_en and "How much money" in last.reply_en


def test_turn_limit_goes_to_review():
    s, _ = run([j(bank="A"), j(bank="B"), j(bank="C")], ["1", "2", "3"], Config(max_turns=3))
    assert s.status == "review" and s.reason == "needs_review"


# ---- the web app ------------------------------------------------------------------------------
def test_api_flow(tmp_path):
    client = FakeClient([j(issue_type="failed_transfer"), j(amount=100), j(when="today"), j(bank="Zenith")])
    app = create_app(client=client, store=Store(str(tmp_path / "t.db")))
    c = app.test_client()
    assert c.post("/api/sessions", json={"language": "xx"}).status_code == 400
    r = c.post("/api/sessions", json={"language": "ha"}).get_json()
    sid = r["session_id"]
    for text in ["hello", "hello", "today", "hello", "skip", "yes"]:      # ... bank, account question, confirmation
        out = c.post(f"/api/sessions/{sid}/turn", json={"text": text}).get_json()
    assert out["status"] == "logged"
    assert c.post(f"/api/sessions/{sid}/turn", json={"text": "more"}).status_code == 409
    assert c.get(f"/api/sessions/{sid}").get_json()["slots"]["bank"] == "Zenith"
    assert len(c.get("/api/complaints").get_json()) == 1


def test_pages_served(tmp_path):
    app = create_app(client=FakeClient([]), store=Store(str(tmp_path / "p.db")))
    c = app.test_client()
    assert c.get("/").status_code == 200
    assert c.get("/console").status_code == 200
    assert c.get("/cases").status_code == 200