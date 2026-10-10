"""Correct stored translations safely (no JSON editing by hand). Edit the two sections below, then run:
    python fix_translations.py
FIXES:        replace a whole message.  FIXES[language][english message] = corrected text
REPLACEMENTS: change a word or phrase everywhere it appears in a language's messages."""
import json
import os

FIXES = {
    "yo": {
        "Is that correct? Please say yes or no.": "Ṣé ó tọ̀nà? Jọ̀wọ́ sọ bẹ́ẹ̀ni tàbí bẹ́ẹ̀kọ́.",
    },
    "ha": {},
    "ig": {},
}

REPLACEMENTS = {
    "yo": {
        "Bawo. ": "Báwo ni. ",       # the greeting should say "Báwo ni", not just "Bawo"
        "Báwo. ": "Báwo ni. ",
    },
    "ha": {},
    "ig": {},
}

path = "translations.json"
data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
for lang, items in FIXES.items():
    data.setdefault(lang, {})
    for english, fixed in items.items():
        data[lang][english] = fixed
        print("fixed:", lang, "->", english[:50])
for lang, pairs in REPLACEMENTS.items():
    for english, local in list(data.get(lang, {}).items()):
        new = local
        for old, repl in pairs.items():
            new = new.replace(old, repl)
        if new != local:
            data[lang][english] = new
            print("replaced in:", lang, "->", english[:50])
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print("saved", path)
