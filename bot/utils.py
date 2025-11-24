# bot/utils.py
import re
import sys
from urllib.parse import urlparse, parse_qs
from typing import Optional
import traceback

def extract_post_id_from_anchor(node) -> str:
    """
    Извлекает ID поста из HTML-узла сообщения (article.message или похожего)
    Обычно берется из атрибута id или data-post-id
    """
    if not node:
        return ""
    # Попытка взять id поста из id узла вида "post-12345"
    node_id = node.get("id", "")
    m = re.search(r'post-(\d+)', node_id)
    if m:
        return m.group(1)
    # Попытка взять из data-post-id
    data_pid = node.get("data-post-id")
    if data_pid:
        return str(data_pid)
    return ""


def log_info(msg: str):
    print(f"[UTILS] {msg}", file=sys.stderr)

def log_error(msg: str):
    print(f"\033[91m[UTILS ERROR] {msg}\033[0m", file=sys.stderr)
    traceback.print_exc()

def normalize_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    url = url.replace("\r", "").replace("\n", "")
    # remove repeated slashes at end
    while url.endswith("//"):
        url = url[:-1]
    return url

def is_forum_domain(url: str, forum_base: str) -> bool:
    """
    Проверяет, начинается ли url с указанного базового домена.
    """
    try:
        url = normalize_url(url).lower()
        base = forum_base.lower().rstrip("/")
        return url.startswith(base)
    except Exception as e:
        log_error(f"is_forum_domain failed: {e}")
        return False

def detect_type(url: str) -> str:
    """
    Возвращает 'thread' | 'forum' | 'unknown'
    """
    if not url:
        return "unknown"
    u = url.lower()
    if "/threads/" in u or "threads=" in u or "/posts/" in u:
        return "thread"
    if "/forums/" in u or "forums=" in u:
        return "forum"
    return "unknown"

def extract_thread_id(url: str) -> str:
    """
    Пытается вытянуть числовой id темы/поста.
    """
    if not url:
        return ""
    try:
        # /posts/12345
        m = re.search(r'/posts/(\d+)', url)
        if m:
            return m.group(1)
        # .12345/ at end or .12345 in query
        m = re.search(r'\.(\d+)(?:/|$)', url)
        if m:
            return m.group(1)
        m = re.search(r'threads=.*?\.([0-9]+)', url)
        if m:
            return m.group(1)
        m = re.search(r'threads=([0-9]+)', url)
        if m:
            return m.group(1)
    except Exception as e:
        log_error(f"extract_thread_id error: {e}")
    return ""

def extract_forum_id(url: str) -> str:
    if not url:
        return ""
    try:
        m = re.search(r'forums/.*?\.(\d+)', url)
        if m:
            return m.group(1)
        m = re.search(r'forums=([0-9]+)', url)
        if m:
            return m.group(1)
    except Exception as e:
        log_error(f"extract_forum_id error: {e}")
    return ""

def truncate_text(s: str, limit: int = 1500) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit-3] + "..."
