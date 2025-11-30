# server.py
"""
Flask admin panel + status API for your ForumTracker bot.
Place next to your project root and run: python server.py
"""
import os
import threading
import time
from datetime import datetime
from flask import Flask, request, redirect, url_for, render_template_string, jsonify, flash
import traceback

# ---- try to import bot modules (your project) ----
try:
    from config import XF_USER, XF_TFA_TRUST, XF_SESSION, FORUM_BASE, POLL_INTERVAL_SEC, VK_TOKEN
except Exception:
    # fallback empty config so server still runs (but tracker will warn)
    XF_USER = XF_SESSION = XF_TFA_TRUST = FORUM_BASE = ""
    POLL_INTERVAL_SEC = 20
    VK_TOKEN = ""

# import ForumTracker and storage helpers
try:
    from bot.forum_tracker import ForumTracker
except Exception as e:
    ForumTracker = None
    print("WARN: cannot import ForumTracker:", e)

# storage helpers (used by commands panel)
try:
    from bot.storage import add_track, remove_track, list_tracks, list_all_tracks, update_last
except Exception:
    # define placeholders to avoid breaking server when storage not available
    def add_track(peer, url, typ): raise RuntimeError("storage.add_track unavailable")
    def remove_track(peer, url): raise RuntimeError("storage.remove_track unavailable")
    def list_tracks(peer): return []
    def list_all_tracks(): return []

# ---- Flask app ----
app = Flask(__name__)
app.secret_key = os.environ.get("ADMIN_SECRET", "devsecret")

# ---- create/attach tracker ----
tracker = None
tracker_lock = threading.Lock()

def ensure_tracker():
    global tracker
    if tracker is None and ForumTracker is not None:
        try:
            # create tracker with cookies from config; vk=None is fine for panel usage
            tracker = ForumTracker(XF_USER, XF_TFA_TRUST, XF_SESSION, None)
            # make sure tracker.start() not run here to avoid double-loops if main.py also runs.
            # we will start it in a background thread from this server.
        except Exception as e:
            print("Failed to init ForumTracker:", e)
            traceback.print_exc()
    return tracker

# start tracker loop in background
def background_tracker_start():
    t = ensure_tracker()
    if t:
        try:
            # start only if it has start() and not running already
            t.start()
        except Exception as e:
            print("Failed to start tracker in background:", e)

# launch tracker start in a daemon thread so server can start fast
threading.Thread(target=background_tracker_start, daemon=True).start()

# ---- simple helpers ----
def format_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

LOG_PATH = os.path.join(os.path.dirname(__file__), "bot.log")

def tail_log(lines=200):
    if not os.path.exists(LOG_PATH):
        return "Log file not found"
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception as e:
        return f"Failed reading log: {e}"

# ---- Templates (small, Bootstrap via CDN) ----
BASE_HTML = """
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>ForumTracker — Панель</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
  </head>
  <body class="bg-light">
    <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
      <div class="container-fluid">
        <a class="navbar-brand" href="/">ForumTracker</a>
        <div class="collapse navbar-collapse">
          <ul class="navbar-nav me-auto">
            <li class="nav-item"><a class="nav-link" href="/">Статус</a></li>
            <li class="nav-item"><a class="nav-link" href="/tracks">Отслеживания</a></li>
            <li class="nav-item"><a class="nav-link" href="/send">Отправить ответ</a></li>
            <li class="nav-item"><a class="nav-link" href="/cookies">Cookies</a></li>
            <li class="nav-item"><a class="nav-link" href="/logs">Логи</a></li>
          </ul>
        </div>
        <span class="navbar-text text-white">FORUM: {{ forum_base }}</span>
      </div>
    </nav>
    <main class="container my-4">
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class="mb-3">
            {% for m in messages %}
              <div class="alert alert-info">{{ m }}</div>
            {% endfor %}
          </div>
        {% endif %}
      {% endwith %}
      {{ body|safe }}
    </main>
  </body>
</html>
"""

# ---- Routes ----

@app.route("/")
def index():
    t = ensure_tracker()
    ok = bool(t)
    status_html = f"""
    <h3>Статус сервера</h3>
    <ul>
      <li>Время: {format_time()}</li>
      <li>ForumTracker loaded: {'Да' if ok else 'Нет'}</li>
      <li>FORUM_BASE: {FORUM_BASE or '&lt;не настроено&gt;'}</li>
      <li>POLL_INTERVAL_SEC: {POLL_INTERVAL_SEC}</li>
    </ul>
    <hr/>
    <h5>Короткая статистика треков</h5>
    """
    try:
        rows = list_all_tracks()
        status_html += f"<p>Всего подписок в БД: {len(rows)}</p>"
    except Exception:
        status_html += "<p>Не удалось получить список треков (storage unavailable)</p>"

    body = status_html
    return render_template_string(BASE_HTML, body=body, forum_base=FORUM_BASE)

# tracks list + add/remove UI
@app.route("/tracks", methods=["GET", "POST"])
def tracks():
    if request.method == "POST":
        action = request.form.get("action")
        url = request.form.get("url", "").strip()
        if not url:
            flash("Пустой URL")
            return redirect(url_for("tracks"))
        try:
            url = url if url.startswith("http") else f"https://{url}"
            if action == "add":
                add_track(-1, url, "thread")  # peer -1 is generic (you can adapt)
                flash(f"Добавлено: {url}")
            elif action == "remove":
                remove_track(-1, url)
                flash(f"Удалено: {url}")
        except Exception as e:
            flash(f"Ошибка storage: {e}")
        return redirect(url_for("tracks"))

    try:
        rows = list_tracks(-1)
    except Exception:
        rows = []
    html = "<h3>Отслеживания (по пользователю -1)</h3>"
    html += """
    <form method="post" class="row g-2 mb-3">
      <div class="col-auto"><input name="url" class="form-control" placeholder="https://forum.matrp.ru/..." /></div>
      <div class="col-auto">
        <button name="action" value="add" class="btn btn-success">Добавить</button>
        <button name="action" value="remove" class="btn btn-danger">Удалить</button>
      </div>
    </form>
    """
    if rows:
        html += "<ul class='list-group'>"
        for u,t,l in rows:
            html += f"<li class='list-group-item'>{u} — {t} — last: {l}</li>"
        html += "</ul>"
    else:
        html += "<p>Нет записей.</p>"

    return render_template_string(BASE_HTML, body=html, forum_base=FORUM_BASE)

# route to send a reply via tracker.post_message
@app.route("/send", methods=["GET","POST"])
def send():
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        text = request.form.get("text", "").strip()
        if not url or not text:
            flash("URL и текст обязательны")
            return redirect(url_for("send"))
        t = ensure_tracker()
        if not t:
            flash("Tracker не инициализирован")
            return redirect(url_for("send"))
        try:
            res = t.post_message(url, text)
            if res.get("ok"):
                flash("Сообщение отправлено (" + res.get("response","ok") + ")")
            else:
                flash("Ошибка: " + str(res.get("error", "unknown")))
        except Exception as e:
            flash("Ошибка отправки: " + str(e))
        return redirect(url_for("send"))

    html = """
    <h3>Отправить ответ в тему</h3>
    <form method="post" class="mb-3">
      <div class="mb-2"><input name="url" class="form-control" placeholder="https://forum.matrp.ru/index.php?threads/..." /></div>
      <div class="mb-2"><textarea name="text" class="form-control" rows="6" placeholder="Текст ответа"></textarea></div>
      <button class="btn btn-primary">Отправить</button>
    </form>
    """
    return render_template_string(BASE_HTML, body=html, forum_base=FORUM_BASE)

# cookies status
@app.route("/cookies")
def cookies():
    t = ensure_tracker()
    if not t:
        return render_template_string(BASE_HTML, body="<p>Tracker не инициализирован</p>", forum_base=FORUM_BASE)
    try:
        r = t.check_cookies()
    except Exception as e:
        r = {"ok": False, "error": str(e)}
    html = "<h3>Проверка cookies</h3><pre>" + jsonify(r).get_data(as_text=True) + "</pre>"
    return render_template_string(BASE_HTML, body=html, forum_base=FORUM_BASE)

# logs
@app.route("/logs")
def logs():
    txt = tail_log(400)
    html = "<h3>Логи</h3><pre style='white-space:pre-wrap; background:#111; color:#ddd; padding:10px;'>" + txt + "</pre>"
    return render_template_string(BASE_HTML, body=html, forum_base=FORUM_BASE)

# simple JSON API endpoints (for automation)
@app.route("/api/status")
def api_status():
    t = ensure_tracker()
    ok = bool(t)
    return jsonify({
        "time": format_time(),
        "tracker_loaded": ok,
        "forum_base": FORUM_BASE,
    })

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    url = data.get("url")
    text = data.get("text")
    if not url or not text:
        return jsonify({"ok": False, "error": "url & text required"}), 400
    t = ensure_tracker()
    if not t:
        return jsonify({"ok": False, "error": "tracker not loaded"}), 500
    try:
        res = t.post_message(url, text)
        return jsonify(res)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# run server
if __name__ == "__main__":
    host = os.environ.get("WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("WEB_PORT", "8080"))
    print(f"Starting web panel on http://{host}:{port}  (tracker background start)")
    app.run(host=host, port=port, debug=False)
