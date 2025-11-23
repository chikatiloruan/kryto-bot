# bot/deepseek_ai.py
import os
import requests

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/v1/chat")

def ask_ai(prompt: str) -> str:
    if not DEEPSEEK_API_KEY:
        return "AI not configured."
    try:
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512
        }
        headers = {
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        r = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        # adapt to provider format
        if isinstance(data, dict) and data.get("choices"):
            return data["choices"][0].get("message", {}).get("content", "") or str(data)
        return str(data)
    except Exception as e:
        return f"AI error: {e}"
