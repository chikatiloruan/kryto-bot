import sqlite3
import threading
from typing import List, Tuple

DB = "bot_data.db"
_lock = threading.Lock()


def _conn():
    return sqlite3.connect(DB, check_same_thread=False)


def init_db():
    with _lock:
        conn = _conn()
        cur = conn.cursor()

        # ---------------- TRACKS ----------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            peer_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            type TEXT NOT NULL,
            last_id TEXT,
            PRIMARY KEY(peer_id, url)
        )""")

        # ---------------- WARNS ----------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS warns (
            peer_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY(peer_id, user_id)
        )""")

        # ---------------- BANS ----------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS bans (
            peer_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY(peer_id, user_id)
        )""")

        # ---------------- STATS ----------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS stats (
            name TEXT PRIMARY KEY,
            count INTEGER DEFAULT 0
        )""")

        conn.commit()
        conn.close()


# ---------------- TRACKS ----------------
def add_track(peer_id: int, url: str, type_: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO tracks (peer_id, url, type, last_id) VALUES (?, ?, ?, NULL)",
            (peer_id, url, type_)
        )
        conn.commit()
        conn.close()


def remove_track(peer_id: int, url: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM tracks WHERE peer_id=? AND url=?", (peer_id, url))
        conn.commit()
        conn.close()


def list_tracks(peer_id: int) -> List[Tuple[str, str, str]]:
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT url, type, last_id FROM tracks WHERE peer_id=?", (peer_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def list_all_tracks():
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
        cur.execute(
            "UPDATE tracks SET last_id=? WHERE peer_id=? AND url=?",
            (last_id, peer_id, url)
        )
        conn.commit()
        conn.close()


# ---------------- WARNS ----------------
def add_warn(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO warns (peer_id, user_id, count) VALUES (?, ?, 0)",
            (peer_id, user_id)
        )
        cur.execute(
            "UPDATE warns SET count = count + 1 WHERE peer_id=? AND user_id=?",
            (peer_id, user_id)
        )
        conn.commit()
        conn.close()


def get_warns(peer_id: int, user_id: int) -> int:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT count FROM warns WHERE peer_id=? AND user_id=?",
        (peer_id, user_id)
    )
    r = cur.fetchone()
    conn.close()
    return r[0] if r else 0


def clear_warns(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "UPDATE warns SET count=0 WHERE peer_id=? AND user_id=?",
            (peer_id, user_id)
        )
        conn.commit()
        conn.close()


# ---------------- BANS ----------------
def add_ban(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO bans (peer_id, user_id) VALUES (?, ?)",
            (peer_id, user_id)
        )
        conn.commit()
        conn.close()


def remove_ban(peer_id: int, user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM bans WHERE peer_id=? AND user_id=?",
            (peer_id, user_id)
        )
        conn.commit()
        conn.close()


def is_banned(peer_id: int, user_id: int) -> bool:
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM bans WHERE peer_id=? AND user_id=?",
        (peer_id, user_id)
    )
    r = cur.fetchone()
    conn.close()
    return bool(r)


# ---------------- STATS ----------------
def stat_inc(name: str):
    """Увеличить счетчик статистики на 1"""
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO stats (name, count)
            VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET count = count + 1
        """, (name,))
        conn.commit()
        conn.close()


def stat_get(name: str) -> int:
    """Получить текущее значение счетчика статистики"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT count FROM stats WHERE name=?", (name,))
    r = cur.fetchone()
    conn.close()
    return r[0] if r else 0
