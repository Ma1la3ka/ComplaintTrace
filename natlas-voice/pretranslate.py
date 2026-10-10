"""Translate every fixed message once, so calls need no translation step. Run with the GPU on."""
import json
import os
from natlas_client import NatlasClient
from dialogue import DialogueEngine, LANGUAGES, FIXED_STRINGS

client = NatlasClient(os.environ["NATLAS_URL"], os.environ["NATLAS_KEY"])
engine = DialogueEngine(client, translations_path="__none__")
path = "translations.json"
data = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}

for lang in LANGUAGES:
    if lang == "en":
        continue                 # English needs no translation
    data.setdefault(lang, {})
    for text in FIXED_STRINGS:
        if text in data[lang]:
            continue
        print(lang, "->", text[:50], flush=True)
        data[lang][text] = engine.localize_live(text, lang)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
print("done. Open translations.json and have a native speaker correct any wording.")