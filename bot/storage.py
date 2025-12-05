# bot/storage.py
import sqlite3
import threading
import os
from typing import List, Tuple, Optional

DB = os.getenv("BOT_DB", "bot_data.db")
_lock = threading.Lock()

def _conn():
    # ensure dir exists
    return sqlite3.connect(DB, check_same_thread=False)

def init_db():
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        # tracks: peer_id, url, type, last_id
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            peer_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            type TEXT NOT NULL,
            last_id TEXT,
            PRIMARY KEY(peer_id, url)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            peer_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY(peer_id, user_id)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            peer_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY(peer_id, user_id)
        )""")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            level TEXT,
            msg TEXT
        )""")
        conn.commit()
        conn.close()

# tracks
def add_track(peer_id: int, url: str, type_: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO tracks (peer_id, url, type, last_id) VALUES (?, ?, ?, NULL)", (peer_id, url, type_))
        conn.commit()
        conn.close()

def remove_track(peer_id: int, url: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tracks WHERE peer_id=? AND url=?", (peer_id, url))
        conn.commit()
        conn.close()

def list_tracks(peer_id: int) -> List[Tuple[str, str, Optional[str]]]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT url, type, last_id FROM tracks WHERE peer_id=?", (peer_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def list_all_tracks() -> List[Tuple[int, str, str, Optional[str]]]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT peer_id, url, type, last_id FROM tracks")
    rows = cur.fetchall()
    conn.close()
    return rows

def update_last(peer_id: int, url: str, last_id: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE tracks SET last_id=? WHERE peer_id=? AND url=?", (last_id, peer_id, url))
        conn.commit()
        conn.close()

# warns
def add_warn(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO warns (peer_id, user_id, count) VALUES (?, ?, 0)", (peer_id, user_id))
        cur.execute("UPDATE warns SET count = count + 1 WHERE peer_id=? AND user_id=?", (peer_id, user_id))
        conn.commit()
        conn.close()

def get_warns(peer_id: int, user_id: int) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT count FROM warns WHERE peer_id=? AND user_id=?", (peer_id, user_id))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else 0

def clear_warns(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE warns SET count=0 WHERE peer_id=? AND user_id=?", (peer_id, user_id))
        conn.commit()
        conn.close()

# bans
def add_ban(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO bans (peer_id, user_id) VALUES (?, ?)", (peer_id, user_id))
        conn.commit()
        conn.close()

def remove_ban(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM bans WHERE peer_id=? AND user_id=?", (peer_id, user_id))
        conn.commit()
        conn.close()

def is_banned(peer_id: int, user_id: int) -> bool:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM bans WHERE peer_id=? AND user_id=?", (peer_id, user_id))
    r = cur.fetchone()
    conn.close()
    return bool(r)

# logs
def log_write(level: str, msg: str):
    try:
        with _lock:
            conn = _conn()
            cur = conn.cursor()
            cur.execute("INSERT INTO logs (ts, level, msg) VALUES (strftime('%s','now'), ?, ?)", (level, msg))
            conn.commit()
            conn.close()
    except Exception:
        pass

# storage.py (append)
def init_templates_table(conn=None):
    conn_local = conn or _conn()
    cur = conn_local.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS templates (
        name TEXT PRIMARY KEY,
        text TEXT NOT NULL
    )""")
    conn_local.commit()
    if conn is None:
        conn_local.close()

def add_template(name: str, text: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO templates (name, text) VALUES (?, ?)", (name, text))
        conn.commit()
        conn.close()

def remove_template(name: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM templates WHERE name=?", (name,))
        conn.commit()
        conn.close()

def get_template(name: str) -> Optional[str]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT text FROM templates WHERE name=?", (name,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else None

def list_templates() -> List[str]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM templates")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

