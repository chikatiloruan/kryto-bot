# server.py
import threading
import time
import html
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, jsonify, send_from_directory
)
from flask.helpers import make_response
from functools import wraps
import os
from config import (
    WEB_LOGIN, WEB_PASSWORD, DEBUG_PASSWORD, FLASK_SECRET,
    WEB_PORT, ALLOW_REMOTE
)
# импортируем твой трекер
from bot.forum_tracker import ForumTracker, stay_online_loop

# -----------------------
# Flask app
# -----------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = FLASK_SECRET or "changeme"

# Глобальный трекер (запустится ниже)
TRACKER = None

# Декораторы
def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if not session.get("auth"):
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return inner

def debug_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if not session.get("debug"):
            return redirect(url_for("debug_login", next=request.path))
        return f(*a, **kw)
    return inner

# -----------------------
# ROUTES
# -----------------------
@app.route("/static/<path:p>")
def static_files(p):
    return send_from_directory("static", p)

@app.route("/", methods=["GET"])
@login_required
def index():
    # показываем краткую панель: треки, кнопки, форма добавления track
    rows = []
    try:
        # list_tracks не импортируем — но трекер хранит tracks в БД, проще показать все из storage
        from bot.storage import list_all_tracks
        rows = list_all_tracks()
    except Exception:
        rows = []
    return render_template("dashboard.html", rows=rows)

# login for panel
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        if u == WEB_LOGIN and p == WEB_PASSWORD:
            session["auth"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        return render_template("login.html", error="Неверный логин/пароль")
    return render_template("login.html")

# logout
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# debug-login (separate password)
@app.route("/debug-login", methods=["GET", "POST"])
@login_required
def debug_login():
    if request.method == "POST":
        p = request.form.get("debug_password", "")
        if p == DEBUG_PASSWORD:
            session["debug"] = True
            return redirect(url_for("debug"))
        return render_template("debug_login.html", error="Неверный debug-пароль")
    return render_template("debug_login.html")

# debug dashboard (requires debug)
@app.route("/debug")
@login_required
@debug_required
def debug():
    # Возвращаем логи/состояние трекера/cookies (скрытые по умолчанию в обычной панеле)
    cookies = {}
    try:
        cookies = TRACKER.session.cookies.get_dict()
    except Exception:
        cookies = {}
    # краткая инфа о трекере
    info = {
        "interval": getattr(TRACKER, "interval", None),
        "running": getattr(TRACKER, "_running", None),
    }
    return render_template("debug.html", cookies=cookies, info=info)

# AJAX лог (fetch logs) — мы сохраняем последние 200 линий в TRACKER.logs (если есть)
@app.route("/api/logs")
@login_required
@debug_required
def api_logs():
    logs = []
    try:
        logs = getattr(TRACKER, "recent_logs", [])[-500:]
    except Exception:
        logs = []
    return jsonify({"logs": logs})

# API: добавить отслеживание (ручной peer_id)
@app.route("/api/add_track", methods=["POST"])
@login_required
def api_add_track():
    url = request.form.get("url", "")
    peer_id = request.form.get("peer_id", "")
    if not url or not peer_id:
        return make_response("Missing fields", 400)
    try:
        # используем storage.add_track
        from bot.storage import add_track
        from bot.utils import normalize_url, detect_type
        url = normalize_url(url)
        add_track(int(peer_id), url, detect_type(url))
        return redirect(url_for("index"))
    except Exception as e:
        return make_response(f"Error: {e}", 500)

# API: удалить трек
@app.route("/api/remove_track", methods=["POST"])
@login_required
def api_remove_track():
    url = request.form.get("url", "")
    peer_id = request.form.get("peer_id", "")
    if not url or not peer_id:
        return make_response("Missing fields", 400)
    try:
        from bot.storage import remove_track
        remove_track(int(peer_id), url)
        return redirect(url_for("index"))
    except Exception as e:
        return make_response(f"Error: {e}", 500)

# API: скрыть/показать cookies (debug only)
@app.route("/api/cookies/reveal", methods=["POST"])
@login_required
@debug_required
def api_cookies_reveal():
    cookies = {}
    try:
        cookies = TRACKER.session.cookies.get_dict()
    except Exception:
        cookies = {}
    return jsonify(cookies)

# Shutdown (debug only)
@app.route("/api/shutdown", methods=["POST"])
@login_required
@debug_required
def api_shutdown():
    func = request.environ.get('werkzeug.server.shutdown')
    if func:
        func()
    return "Shutting down..."

# -----------------------
# Start tracker in background when server starts
# -----------------------
def start_tracker_background():
    global TRACKER
    # create ForumTracker(vk) — we don't have VK here, pass None
    try:
        TRACKER = ForumTracker(None)
    except Exception as e:
        # if tracker expects cookies args signature, try fallback with globals
        try:
            from config import XF_USER, XF_TFA_TRUST, XF_SESSION
            TRACKER = ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, None)
        except Exception:
            TRACKER = ForumTracker(None)
    # attach simple recent_logs list to track logs
    if not hasattr(TRACKER, "recent_logs"):
        TRACKER.recent_logs = []

    # monkey-patch Tracker debug log appender if possible
    def append_log(line):
        try:
            TRACKER.recent_logs.append(line)
            if len(TRACKER.recent_logs) > 1000:
                TRACKER.recent_logs.pop(0)
        except Exception:
            pass

    # start tracker thread if not running
    try:
        TRACKER.start()
    except Exception:
        pass

    # start online pinger in separate thread (stay_online_loop exists)
    try:
        threading.Thread(target=stay_online_loop, daemon=True).start()
    except Exception:
        pass

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    # start tracker before flask
    start_tracker_background()
    host = "0.0.0.0" if ALLOW_REMOTE else "127.0.0.1"
    app.run(host=host, port=WEB_PORT, debug=False)
