import os
import re
from urllib.parse import urlparse, parse_qs

def normalize_url(url: str) -> str:
    url = url.strip()

    if not url.startswith("http"):
        url = "https://" + url

    # НЕ трогаем ссылки вида *.12345/
    # потому что двойной слеш ломает detect_type
    if "threads" in url or "forums" in url:
        return url

    # Если совсем обычная ссылка — тогда добавляем / для красоты
    if not url.endswith("/"):
        url = url + "/"

    return url

def detect_type(url: str) -> str:
    u = url.lower()

    if "/threads/" in u or "index.php?threads=" in u:
        return "thread"

    if "/forums/" in u or "index.php?forums=" in u:
        return "forum"

    return "unknown"

def extract_thread_id(url: str) -> str:
    # handle URLs like .../threads/slug.12345/ or index.php?threads=slug.12345/
    m = re.search(r'\.([0-9]{3,})/?$', url)
    if m:
        return m.group(1)
    m2 = re.search(r'threads=.*?\.([0-9]{3,})', url)
    if m2:
        return m2.group(1)
    return ""

def extract_forum_id(url: str) -> str:
    m = re.search(r'forums/.*?\.([0-9]{1,6})', url)
    if m:
        return m.group(1)
    m2 = re.search(r'forums=([0-9]{1,6})', url)
    if m2:
        return m2.group(1)
    return ""

