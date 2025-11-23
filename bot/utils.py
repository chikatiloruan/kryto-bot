# bot/utils.py
import re
from urllib.parse import urlparse, parse_qs

def normalize_url(url: str) -> str:
    """
    Нормализует URL: добавляет https:// если нужно, не ломает query-параметры.
    Не добавляет лишних двойных слешей.
    """
    if not url:
        return url
    url = url.strip()
    # add scheme if missing
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # Trim trailing whitespace/newlines
    url = url.replace("\n", "").replace("\r", "")

    # If url already ends with slash or contains query param, keep as is
    # but ensure no double slash at end
    if url.endswith("//"):
        url = url[:-1]
    return url

def detect_type(url: str) -> str:
    """
    Определяет тип: 'thread' или 'forum' или 'unknown'.
    Обрабатывает форматы:
      - /threads/slug.12345/
      - index.php?threads=slug.12345
      - /forums/... .1234/
      - index.php?forums=1234
    """
    if not url:
        return "unknown"
    u = url.lower()
    if "/threads/" in u or "index.php?threads=" in u or "/posts/" in u:
        return "thread"
    if "/forums/" in u or "index.php?forums=" in u:
        return "forum"
    return "unknown"

def extract_thread_id(url: str) -> str:
    """
    Пытается извлечь числовой id темы/поста из URL:
      - ...slug.1234567/
      - index.php?threads=slug.1234567
      - /posts/28237102/  (возможно для пост-ссылок)
    Возвращает строку с id или пустую строку.
    """
    if not url:
        return ""
    # Try post style /posts/28237102/
    m = re.search(r'/posts/(\d+)', url)
    if m:
        return m.group(1)

    # Try .1234567/ at end
    m = re.search(r'\.(\d+)(?:/|$)', url)
    if m:
        return m.group(1)

    # Try query param threads=... .123
    m = re.search(r'threads=.*?\.([0-9]+)', url)
    if m:
        return m.group(1)

    # Try index.php?threads=12345
    m = re.search(r'threads=([0-9]+)', url)
    if m:
        return m.group(1)

    return ""

def extract_post_id_from_anchor(node) -> str:
    """
    Попытка извлечь id поста из атрибутов узла (если есть).
    Полезно в случае article[data-message-id] или id="post-..."
    """
    if not node:
        return ""
    v = node.get("data-message-id") or node.get("data-content") or node.get("id")
    if v:
        m = re.search(r'(\d+)', str(v))
        if m:
            return m.group(1)
    return ""
