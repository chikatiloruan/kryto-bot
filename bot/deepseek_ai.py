# bot/deepseek_ai.py
import os
import requests

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.example/v1/chat")

def ask_ai(prompt: str, max_tokens: int = 512) -> str:
    if not DEEPSEEK_API_KEY:
        return "AI not configured. Set DEEPSEEK_API_KEY env var."
    try:
        payload = {"model": "gpt-like", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        r = requests.post(DEEPSEEK_API_URL, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        # adapt to provider response
        if isinstance(data, dict):
            # attempt several common shapes
            if data.get("choices"):
                return data["choices"][0].get("message", {}).get("content", "") or str(data)
            if data.get("result"):
                return str(data.get("result"))
        return str(data)
    except Exception as e:
        return f"AI error: {e}"
