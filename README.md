
# VK Forum Tracker Bot (Railway)

Особенности:
- /track <url> — отслеживание темы/раздела (одна или множество ссылок в разных чатах)
- /untrack <url>
- /list — показать ссылки чата
- /check — принудительно проверить
- /ai <текст> — DeepSeek AI (deepseek-chat)
- Модерация в чатах: /kick /ban /mute /unmute /warn /warns /clearwarns
- Использует 3 cookie (XF_USER, XF_SESSION, XF_TFA_TRUST) — параллельные запросы

## Деплой (Railway)
1. Залей репозиторий на GitHub.
2. Подключи его в Railway и деплой.
3. В Railway → Variables добавь:
   - VK_TOKEN
   - XF_USER
   - XF_SESSION
   - XF_TFA_TRUST
   - DEEPSEEK_API_KEY
   - POLL_INTERVAL_SEC (по умолчанию 10)
   - KEEP_ALIVE_PORT (8080)
   - ADMINS (опционально)

## Примечание
- Никогда не коммить секреты в репо.
- Если нужно подогнать парсер под конкретную тему/форум — пришли URL темы.  
- Скриншоты/логи, которые ты прикладывал(а) — лежат локально: `/mnt/data/d05d3b31-7d40-477b-aae2-a9137a87da8e.png` (ссылка в чате).
