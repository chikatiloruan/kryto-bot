# bot/utils.py
import re
import sys
from urllib.parse import urlparse, parse_qs
from typing import Optional
import traceback

import requests
from config import FORUM_BASE

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

def fetch_html(url: str) -> str | None:
    """
    Скачивает HTML страницу без авторизации.
    Используется в /tlist, /tlistall и parsing форумов.
    """
    try:
        url = normalize_url(url)
        r = requests.get(url, headers=HEADERS, timeout=10)

        if r.status_code != 200:
            log_error(f"HTTP {r.status_code} for {url}")
            return None

        return r.text

    except Exception as e:
        log_error(f"fetch_html failed: {e}")
        return None


def extract_post_id_from_article(article_html: str) -> str:
    """
    Извлекает ID поста из HTML статьи:
    - <article data-content="post-123456">
    - <article data-message-id="123456">
    - <article id="js-post-123456">
    """
    if not article_html:
        return ""

    # Ищем pattern data-message-id="12345"
    m = re.search(r'data-message-id=["\'](\d+)["\']', article_html)
    if m:
        return m.group(1)

    # Ищем data-content="post-12345"
    m = re.search(r'data-content=["\']post-(\d+)["\']', article_html)
    if m:
        return m.group(1)

    # Ищем id="js-post-12345"
    m = re.search(r'id=["\']js-post-(\d+)["\']', article_html)
    if m:
        return m.group(1)

    # fallback — ищем любые числа в article, но осторожно
    m = re.search(r'post[-_]?(\\d+)', article_html)
    if m:
        return m.group(1)

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
    Определяет тип страницы MatRP:
      thread  — тема
      forum   — раздел
      members — список пользователей
      unknown — остальное
    """
    if not url:
        return "unknown"

    u = url.lower()

    # ---- темы ----
    if "/threads/" in u:
        return "thread"
    if "index.php?threads=" in u:
        return "thread"
    if "threads=" in u:
        return "thread"
    if "/posts/" in u:
        return "thread"

    # ---- форумы (разделы) ----
    if "/forums/" in u:
        return "forum"
    if "index.php?forums=" in u:
        return "forum"
    if "forums=" in u:
        return "forum"

    # ---- участники ----
    if "/members/" in u:
        return "members"

    # по умолчанию лучше считать темой
    return "thread"


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
