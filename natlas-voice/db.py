"""SQLite storage for development. Tables map directly onto MySQL/TiDB later."""
import json
import sqlite3
import time
from dialogue import Session

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, ref TEXT, language TEXT, status TEXT, reason TEXT,
  slots TEXT, misses INTEGER, turns INTEGER, last_question TEXT,
  created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
  text TEXT, text_en TEXT, created_at REAL
);
"""


class Store:
    def __init__(self, path: str = "complaints.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def save(self, s: Session):
        now = time.time()
        self.conn.execute(
            """INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, reason=excluded.reason,
               slots=excluded.slots, misses=excluded.misses, turns=excluded.turns,
               last_question=excluded.last_question, updated_at=excluded.updated_at""",
            (s.id, s.ref, s.language, s.status, s.reason, json.dumps(s.slots),
             s.misses, s.turns, s.last_question, now, now))
        self.conn.commit()

    def load(self, session_id: str):
        r = self.conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not r:
            return None
        return Session(language=r["language"], id=r["id"], ref=r["ref"], status=r["status"],
                       reason=r["reason"], slots=json.loads(r["slots"]), misses=r["misses"],
                       turns=r["turns"], last_question=r["last_question"])

    def add_turn(self, session_id: str, role: str, text: str, text_en: str = ""):
        self.conn.execute("INSERT INTO turns (session_id, role, text, text_en, created_at) VALUES (?,?,?,?,?)",
                          (session_id, role, text, text_en, time.time()))
        self.conn.commit()

    def turns(self, session_id: str):
        rows = self.conn.execute("SELECT role, text, text_en FROM turns WHERE session_id=? ORDER BY id",
                                 (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def by_status(self, status: str):
        rows = self.conn.execute("SELECT id FROM sessions WHERE status=? ORDER BY updated_at DESC",
                                 (status,)).fetchall()
        return [self.load(r["id"]) for r in rows]



class MySQLStore:
    """MySQL / TiDB storage. Same methods as Store."""

    def __init__(self, host, user, password, database, port=3306, ssl=False, ssl_ca=None):
        import threading
        import pymysql
        self._pymysql = pymysql
        self._lock = threading.Lock()
        kw = dict(host=host, user=user, password=password, database=database, port=int(port),
                  charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                  connect_timeout=15)
        if ssl or ssl_ca:
            import certifi
            kw["ssl"] = {"ca": ssl_ca or certifi.where()}
        self.conn = pymysql.connect(**kw)

    def _run(self, sql, args=(), fetch=None):
        with self._lock:
            self.conn.ping(reconnect=True)
            with self.conn.cursor() as cur:
                cur.execute(sql, args)
                if fetch == "one":
                    return cur.fetchone()
                if fetch == "all":
                    return cur.fetchall()

    def save(self, s: Session):
        self._run(
            """INSERT INTO sessions (id, ref, language, status, reason, slots, misses, turns, last_question)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE status=VALUES(status), reason=VALUES(reason),
               slots=VALUES(slots), misses=VALUES(misses), turns=VALUES(turns),
               last_question=VALUES(last_question)""",
            (s.id, s.ref, s.language, s.status, s.reason, json.dumps(s.slots),
             s.misses, s.turns, s.last_question))

    def load(self, session_id: str):
        r = self._run("SELECT * FROM sessions WHERE id=%s", (session_id,), "one")
        if not r:
            return None
        slots = r["slots"]
        slots = json.loads(slots) if isinstance(slots, (str, bytes)) else (slots or {})
        return Session(language=r["language"], id=r["id"], ref=r["ref"], status=r["status"],
                       reason=r["reason"], slots=slots, misses=r["misses"],
                       turns=r["turns"], last_question=r["last_question"] or "")

    def add_turn(self, session_id: str, role: str, text: str, text_en: str = ""):
        self._run("INSERT INTO turns (session_id, role, text, text_en) VALUES (%s,%s,%s,%s)",
                  (session_id, role, text, text_en))

    def turns(self, session_id: str):
        rows = self._run("SELECT role, text, text_en FROM turns WHERE session_id=%s ORDER BY id",
                         (session_id,), "all")
        return [dict(r) for r in rows]

    def by_status(self, status: str):
        rows = self._run("SELECT id FROM sessions WHERE status=%s ORDER BY updated_at DESC",
                         (status,), "all")
        return [self.load(r["id"]) for r in rows]


def make_store():
    """MySQL if DB_HOST is set, otherwise local SQLite for quick testing."""
    import os
    if os.environ.get("DB_HOST"):
        return MySQLStore(
            host=os.environ["DB_HOST"], user=os.environ["DB_USER"],
            password=os.environ.get("DB_PASSWORD", ""), database=os.environ.get("DB_NAME", "natlas_voice"),
            port=os.environ.get("DB_PORT", 3306),
            ssl=os.environ.get("DB_SSL", "").lower() in ("1", "true", "yes"),
            ssl_ca=os.environ.get("DB_SSL_CA") or None)
    return Store(os.environ.get("DB_PATH", "complaints.db"))