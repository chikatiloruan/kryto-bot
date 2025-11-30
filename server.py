import os
import sqlite3
import time
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for, session,
    flash
)

# -----------------------------
#   Load config
# -----------------------------
try:
    from config import ADMIN_USER, ADMIN_PASS, DEBUG_PASS, FORUM_BASE
except:
    ADMIN_USER = "admin"
    ADMIN_PASS = "admin"
    DEBUG_PASS = "debug"
    FORUM_BASE = "https://forum.matrp.ru"

# -----------------------------
#   Flask init
# -----------------------------
app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = "panel-secret-key"

DB_FILE = "panel.db"


# -----------------------------
#   DB helpers
# -----------------------------
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            ts_iso TEXT,
            ip TEXT,
            path TEXT,
            ua TEXT,
            user TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER,
            ts_iso TEXT,
            actor TEXT,
            action TEXT,
            details TEXT
        )
    """)

    conn.commit()
    conn.close()


# -----------------------------
#   Logging
# -----------------------------
def log_visit(ip, path, ua, user):
    conn = db()
    c = conn.cursor()
    ts = int(time.time())
    c.execute(
        "INSERT INTO visits (ts, ts_iso, ip, path, ua, user) VALUES (? ,?, ?, ?, ?, ?)",
        (ts, datetime.utcfromtimestamp(ts).isoformat(), ip, path, ua, user or "")
    )
    conn.commit()
    conn.close()


def log_action(actor, action, details=""):
    conn = db()
    c = conn.cursor()
    ts = int(time.time())
    c.execute(
        "INSERT INTO actions (ts, ts_iso, actor, action, details) VALUES (?, ?, ?, ?, ?)",
        (ts, datetime.utcfromtimestamp(ts).isoformat(), actor or "", action, details)
    )
    conn.commit()
    conn.close()


# -----------------------------
#   AUTH DECORATORS
# -----------------------------
def requires_auth(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if "user" not in session:
            return redirect("/login")
        return f(*a, **kw)
    return wrapper


def requires_debug(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if session.get("debug") is not True:
            return redirect("/debug-login")
        return f(*a, **kw)
    return wrapper


# -----------------------------
#   BEFORE REQUEST LOGGING
# -----------------------------
@app.before_request
def before():
    ip = request.headers.get("X-Real-IP") or request.remote_addr
    ua = request.headers.get("User-Agent", "")[:300]
    path = request.path
    user = session.get("user")

    if not path.startswith("/static"):
        try:
            log_visit(ip, path, ua, user)
        except:
            pass


# -----------------------------
#   LOGIN
# -----------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        if u == ADMIN_USER and p == ADMIN_PASS:
            session["user"] = u
            log_action(u, "login")
            return redirect("/")
        flash("Неверный логин или пароль", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    user = session.get("user")
    log_action(user, "logout")
    session.clear()
    return redirect("/login")


# -----------------------------
#   DEBUG LOGIN
# -----------------------------
@app.route("/debug-login", methods=["GET", "POST"])
def debug_login():
    if request.method == "POST":
        p = request.form.get("password")
        if p == DEBUG_PASS:
            session["debug"] = True
            log_action(session.get("user"), "debug_login")
            return redirect("/debug")
        flash("Неверный пароль debug", "danger")
    return render_template("debug_login.html")


# -----------------------------
#   DASHBOARD
# -----------------------------
@app.route("/")
@requires_auth
def index():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 20")
    actions = c.fetchall()
    c.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 40")
    visits = c.fetchall()
    conn.close()

    return render_template("dashboard.html", actions=actions, visits=visits)


# -----------------------------
#   DEBUG PANEL
# -----------------------------
@app.route("/debug")
@requires_debug
def debug():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 200")
    actions = c.fetchall()
    c.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 200")
    visits = c.fetchall()
    conn.close()

    return render_template("debug.html", actions=actions, visits=visits)


# -----------------------------
#   LOGS — ACTIONS
# -----------------------------
@app.route("/logs/actions")
@requires_auth
def logs_actions():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM actions ORDER BY ts DESC LIMIT 1000")
    rows = c.fetchall()
    conn.close()
    return render_template("logs_actions.html", logs=rows)


# -----------------------------
#   LOGS — VISITS
# -----------------------------
@app.route("/logs/visits")
@requires_auth
def logs_visits():
    conn = db()
    c = conn.cursor()
    c.execute("SELECT * FROM visits ORDER BY ts DESC LIMIT 1000")
    rows = c.fetchall()
    conn.close()
    return render_template("logs_visits.html", logs=rows)


# -----------------------------
#   RUN
# -----------------------------
if __name__ == "__main__":
    init_db()
    print("WEB-Панель запущена → http://0.0.0.0:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
