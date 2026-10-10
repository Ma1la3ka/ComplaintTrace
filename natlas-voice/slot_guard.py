"""Removes details the language model made up, so the app asks the caller instead of saving a guess.

Lives in the natlas-voice folder, next to dialogue.py.   Self-test:  python slot_guard.py   (should print ALL PASS)

How it works: a time is kept only if the caller's own words contain a time cue (yesterday, 7pm, lanaa, jiya, unyaahu ...).
A bank is kept only if it is not a placeholder (and, in English, only if the caller said it).
Words are compared with accents and tone marks removed, so "lánàá" and "lana" both match.

Limits, so nobody over-trusts it:
 - The time-word lists for Yoruba, Hausa and Igbo were written by an AI, not by native speakers. Ask a speaker of each
   language to add or correct words. A missing word is safe: the app simply asks the caller "when?".
 - Banks spoken in Yoruba, Hausa or Igbo cannot be checked word for word; only placeholder-looking banks are removed.
 - It cannot fix a wrong transcript (for example "transfer" heard as "transport"). That is the speech model's error.
"""
import re
import unicodedata

PLACEHOLDER = re.compile(
    r"\b(bank name|customer|unknown|unspecified|not specified|not stated|n/a|none|null|the bank|my bank|a bank)\b", re.I)
TIME_CUE_EN = re.compile(
    r"yesterday|today|tonight|morning|afternoon|evening|night|\bago\b|\bnoon\b|midnight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|last week|this week|"
    r"\d\s?(?:am|pm)\b|o'?clock|\d{1,2}(?:st|nd|rd|th)\b|"
    r"january|february|march|april|june|july|august|september|october|november|december", re.I)

# Whole-word cues, accents removed. Written by an AI: please have native speakers check and extend them.
CUES = {
    "yo": {"lana", "lanaa", "ana", "anaa", "oni", "lonii", "loni", "owuro", "aaro", "osan", "irole", "ale", "oru",
           "aago", "ago", "ojo", "ose", "osu", "odun", "wakati"},
    "ha": {"jiya", "yau", "safe", "safiya", "rana", "yamma", "dare", "daren", "karfe", "awa", "kwana", "kwanaki",
           "mako", "makon", "wata", "shekara"},
    "ig": {"unyaahu", "taa", "tata", "ututu", "ehihie", "mgbede", "abali", "elekere", "izu", "onwa", "ubochi"},
}


def _plain(text):
    """Lower-case, remove tone marks and dots under letters, map Hausa hooked letters, keep only letters and digits."""
    text = unicodedata.normalize("NFD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.translate(str.maketrans({"ƙ": "k", "ɓ": "b", "ɗ": "d", "ƴ": "y"}))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def has_time_cue(transcript, language):
    plain = _plain(transcript)
    if TIME_CUE_EN.search((transcript or "").lower()) or TIME_CUE_EN.search(plain):
        return True                                    # English words and 7pm style times work in every language
    return bool(set(plain.split()) & CUES.get(language, set()))


def drop_invented(extracted: dict, transcript: str, language: str) -> dict:
    said = (transcript or "").lower()
    bank = str(extracted.get("bank") or "").strip()
    if bank:
        words = re.findall(r"[a-z]{3,}", bank.lower())
        if PLACEHOLDER.search(bank) or (language == "en" and not any(w in said for w in words)):
            extracted.pop("bank", None)
    if extracted.get("when") and not has_time_cue(transcript, language):
        extracted.pop("when", None)
    return extracted


if __name__ == "__main__":
    cases = [
        # (what the caller said, what the model returned, language, what should be kept)
        ("I sent 5400 naira to someone and it failed",
         {"amount": 5400, "when": "yesterday evening", "bank": "customer's bank name"}, "en", {"amount": 5400}),
        ("I sent 5400 naira yesterday around 5am. My bank is Eko Mutual",
         {"amount": 5400, "when": "yesterday around 5am", "bank": "Eko Mutual"}, "en",
         {"amount": 5400, "when": "yesterday around 5am", "bank": "Eko Mutual"}),
        ("it was last night", {"when": "last night"}, "en", {"when": "last night"}),
        ("I use Nigeria Bank", {"bank": "Nigeria Bank"}, "en", {"bank": "Nigeria Bank"}),
        ("my bank", {"bank": "my bank"}, "en", {}),
        # Yoruba: the exact failure from testing (no time said, model invented "yesterday evening")
        ("báwo ni jọ́wọ́ mú fi 2,600 náírà fún mú fi transport, ṣùgbọ́n kò lọ wọ́n sì já nínú owó mi?",
         {"amount": 2600, "when": "yesterday evening"}, "yo", {"amount": 2600}),
        ("Mo fi two thousand six hundred naira ránṣẹ́ ní nǹkan bí aago méje alẹ́ sí àkáǹtì mi",
         {"amount": 2600, "when": "around 7pm"}, "yo", {"amount": 2600, "when": "around 7pm"}),
        ("Mo fi náírà ránṣẹ́ lánàá", {"when": "yesterday"}, "yo", {"when": "yesterday"}),
        ("Mo fi náírà ránṣẹ́", {"when": "yesterday", "bank": "customer's bank name"}, "yo", {}),
        ("Mo fi náírà ránṣẹ́ yesterday", {"when": "yesterday"}, "yo", {"when": "yesterday"}),      # English word inside Yoruba
        ("Na aika kudi amma bai isa ba", {"when": "yesterday"}, "ha", {}),
        ("Na aika kudi jiya da yamma", {"when": "yesterday evening"}, "ha", {"when": "yesterday evening"}),
        ("Zipụrụ m ego ma ọ dịghị aga", {"when": "yesterday"}, "ig", {}),
        ("Zipụrụ m ego ụnyaahụ", {"when": "yesterday"}, "ig", {"when": "yesterday"}),
    ]
    good = True
    for text, got, lang, want in cases:
        result = drop_invented(dict(got), text, lang)
        print("OK " if result == want else "BAD", f"[{lang}]", text[:50], "->", result)
        good &= result == want
    print("ALL PASS" if good else "FAILURES")