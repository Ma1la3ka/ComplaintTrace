"""Tiny .env reader with no extra install. Put this file next to app.py (and next to ingest.py in complainttrace).

Reads KEY=VALUE lines from a file called .env in the same folder as this file.
- Lines starting with # are ignored. Quotes around a value are optional.
- A real environment variable (for example $env:NAME="..." in PowerShell) always wins over the file.
- A line with an empty value (KEY=) is treated as not set, so defaults apply.
- Careful: for switches like DEBUG_AUDIO, ANY non-empty value turns it on, even 0. To turn one off, delete or comment out the line.
"""
import os


def load_env(path=None):
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return []
    loaded = []
    with open(path, encoding="utf-8-sig") as f:                 # utf-8-sig: Windows editors sometimes add an invisible mark
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            elif " #" in value:                                  # a trailing comment on an unquoted value
                value = value.split(" #", 1)[0].strip()
            if key and value and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    return loaded  