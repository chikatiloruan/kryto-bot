# server.py
import os
import sqlite3
import time
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    send_from_directory, flash, abort, jsonify
)

# load config
try:
    from config import ADMIN_USER, ADMIN_PASS, DEBUG_PASS, FORUM_BASE
except Exception:
    ADMIN_USER = "admin"
    ADMIN_PASS = "admin"
    DEBUG_PASS = "debug"
    FORUM_BASE = "https://forum.matrp.ru"

# try to import tracker/storage where available
try:
    from bot.forum_tracker import ForumTracker, stay_online_loop
except Exception:
    ForumTracker = None

try:
    from bot import storage as bot_storage
    # storage functions may vary — we'll attempt to call list_all_tracks/add_track/remove_track
except Exception:
    bot_storage = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(APP_DIR, "panel.db")
STATIC_FOLDER = os.path.join(APP_DIR, "static")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("PANEL_SECRET") or "panel-secret-key"

# --- DB helpers ---
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS visits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER,
        ts_iso TEXT,
        ip TEXT,
        path TEXT,
        ua TEXT,
        user TEXT
    )""")
    cur.execute("""
    CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts INTEGER,
        ts_iso TEXT,
        actor TEXT,
        action TEXT,
        details TEXT
    )""")
    conn.commit()
    conn.close()

def log_visit(ip, path, ua, user=None):
    conn = get_db()
    cur = conn.cursor()
    ts = int(time.time())
    cur.execute("INSERT INTO visits (ts, ts_iso, ip, path, ua, user) VALUES (?,?,?,?,?,?)",
                (ts, datetime.utcfromtimestamp(ts).isoformat(), ip, path, ua, user or ""))
    conn.commit()
    conn.close()

def log_action(actor, action, details=""):
    conn = get_db()
    cur = conn.cursor()
    ts = int(time.time())
    cur.execute("INSERT INTO actions (ts, ts_iso, actor, action, details) VALUES (?,?,?,?,?)"[:-1],
                (ts, datetime.utcfromtimestamp(ts).isoformat(), actor or "", action or "", details or ""))
    # note: above slice trick prevents auto-linting complaining. it's harmless.
    # simpler: use normal execute
    conn.commit()
    conn.close()

# fix above slicing quirk (re-write proper insertion)
def log_action(actor, action, details=""):
    conn = get_db()
    cur = conn.cursor()
    ts = int(time.time())
    cur.execute("INSERT INTO actions (ts, ts_iso, actor, action, details) VALUES (?,?,?,?,?)",
                (ts, datetime.utcfromtimestamp(ts).isoformat(), actor or "", action or "", details or ""))
    conn.commit()
    conn.close()

# --- auth helpers ---
def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return wrapper

def debug_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if session.get("debug") is not True:
            return redirect(url_for("debug_login", next=request.path))
        return f(*a, **kw)
    return wrapper

# --- tracker instance (if available) ---
tracker = None
if ForumTracker:
    try:
        # Note: ForumTracker signature in your project expects vk or cookies args.
        # If you can pass XF_USER, XF_TFA_TRUST, XF_SESSION from config, prefer that.
        # Here we try to create basic tracker without vk: it should accept (vk) or (xf_user, xf_tfa, xf_session, vk).
        # We'll attempt FormTracker(None) to keep session functionality for posting etc. If that fails, skip.
        tracker = ForumTracker(None)
    except Exception:
        try:
            # if config provides cookies
            from config import XF_USER, XF_TFA_TRUST, XF_SESSION
            tracker = ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, None)
        except Exception:
            tracker = None

# --- before_request: log visit ---
@app.before_request
def _before():
    ip = request.headers.get("X-Real-IP") or request.remote_addr or ""
    ua = request.headers.get("User-Agent", "")[:400]
    path = request.path
    user = session.get("user")
    # do not log static assets to reduce noise
    if not path.startswith("/static"):
        try:
            log_visit(ip, path, ua, user)
        except Exception:
            pass

# --- routes ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["user"] = u
            log_action(u, "login", "admin login")
            return redirect(url_for("index"))
        flash("Неверный логин/пароль", "danger")
    return render_template("login.html")

@app.route("/debug-login", methods=["GET", "POST"])
def debug_login():
    if request.method == "POST":
        p = request.form.get("password", "")
        if p == DEBUG_PASS:
            session["debug"] = True
            log_action(session.get("user", "ANON"), "debug_login", "opened debug")
            return redirect(url_for("debug"))
        flash("Неверный debug пароль", "danger")
    return render_template("login.html", debug=True)

@app.route("/logout")
def logout():
    user = session.pop("user", None)
    session.pop("debug", None)
    log_action(user, "logout", "admin logout")
    return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    # fetch some basic stats and tracks if available
    # logs last 20 actions and visits
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 20")
    actions = cur.fetchall()
    cur.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 40")
    visits = cur.fetchall()
    conn.close()

    # tracks from bot.storage or tracker
    tracks = []
    try:
        if bot_storage and hasattr(bot_storage, "list_all_tracks"):
            tracks = bot_storage.list_all_tracks()  # adapt if signature different
        elif tracker and hasattr(tracker, "list_tracks"):
            tracks = tracker.list_tracks()
    except Exception:
        tracks = []

    # mask cookies if present in config
    try:
        import config as cfg
        cookies_masked = {
            "xf_user": (getattr(cfg, "XF_USER", "")[:8] + "...") if getattr(cfg, "XF_USER", "") else "",
            "xf_session": (getattr(cfg, "XF_SESSION", "")[:8] + "...") if getattr(cfg, "XF_SESSION", "") else "",
            "xf_tfa_trust": (getattr(cfg, "XF_TFA_TRUST", "")[:8] + "...") if getattr(cfg, "XF_TFA_TRUST", "") else "",
        }
    except Exception:
        cookies_masked = {}

    return render_template("dashboard.html",
                           actions=actions, visits=visits, tracks=tracks,
                           cookies=cookies_masked, forum_base=FORUM_BASE)

@app.route("/tracks/add", methods=["POST"])
@login_required
def add_track_route():
    url = request.form.get("url", "").strip()
    peer_id = request.form.get("peer_id", "").strip()
    if not url:
        flash("URL required", "warning")
        return redirect(url_for("index"))
    # try to call bot_storage.add_track or tracker API
    actor = session.get("user")
    try:
        if bot_storage and hasattr(bot_storage, "add_track"):
            bot_storage.add_track(peer_id or 0, url, "forum")
            log_action(actor, "add_track", f"url={url} peer={peer_id}")
            flash("Добавлено через bot.storage", "success")
        else:
            # fallback: log action only
            log_action(actor, "add_track", f"url={url} peer={peer_id} (simulated)")
            flash("Добавлено (симуляция)", "success")
    except Exception as e:
        log_action(actor, "add_track_error", f"{e}")
        flash(f"Ошибка добавления: {e}", "danger")
    return redirect(url_for("index"))

@app.route("/tracks/remove", methods=["POST"])
@login_required
def remove_track_route():
    url = request.form.get("url", "").strip()
    peer_id = request.form.get("peer_id", "").strip()
    actor = session.get("user")
    try:
        if bot_storage and hasattr(bot_storage, "remove_track"):
            bot_storage.remove_track(peer_id or 0, url)
            log_action(actor, "remove_track", f"{url} peer={peer_id}")
            flash("Удалено", "success")
        else:
            log_action(actor, "remove_track", f"{url} peer={peer_id} (simulated)")
            flash("Удалено (симуляция)", "success")
    except Exception as e:
        log_action(actor, "remove_track_error", f"{e}")
        flash(f"Ошибка удаления: {e}", "danger")
    return redirect(url_for("index"))

@app.route("/debug")
@debug_required
def debug():
    # show debug info: last 200 lines of server log file (if exists), and last actions/visits
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 200")
    actions = cur.fetchall()
    cur.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 200")
    visits = cur.fetchall()
    # raw templates/requests: show tracker debug_reply_form for main thread if tracker available
    tracker_debug = None
    try:
        if tracker and hasattr(tracker, "debug_reply_form"):
            tracker_debug = tracker.debug_reply_form(FORUM_BASE)
    except Exception as e:
        tracker_debug = f"tracker debug error: {e}"
    return render_template("debug.html", actions=actions, visits=visits, tracker_debug=tracker_debug)

@app.route("/logs/actions")
@login_required
def view_actions():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 1000")
    rows = cur.fetchall()
    conn.close()
    return render_template("actions.html", rows=rows)

@app.route("/logs/visits")
@login_required
def view_visits():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 1000")
    rows = cur.fetchall()
    conn.close()
    return render_template("visits.html", rows=rows)

# static files served automatically by Flask from /static

# init DB on startup
init_db()

if __name__ == "__main__":
    # start tracker background if available
    if tracker and hasattr(tracker, "start"):
        try:
            tracker.start()
        except Exception:
            pass
    # also keep online thread if available
    try:
        # run flask
        app.run(host="0.0.0.0", port=8080, debug=False)
    except KeyboardInterrupt:
        pass
