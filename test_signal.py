#!/usr/bin/env python3
"""
Тестовый скрипт — отправляет тестовый сигнал напрямую в Telegram,
чтобы проверить что бот и chat_id работают.

Запуск:
    python test_signal.py
"""

import json
import os
from urllib.request import Request, urlopen

env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

test_message = """🟢 <b>V-Zones: СИГНАЛ LONG ▲</b>

📌 <b>Инструмент:</b> XAUUSD
⏱ <b>Таймфрейм:</b> 15m
🕐 <b>Время:</b> 2026-11-20 05:00

💰 <b>Цена входа:</b> 2650.50
🛑 <b>Stop Loss:</b> 2635.20
🎯 <b>Take Profit:</b> 2681.10
📐 <b>R:R =</b> 1:2.0

━━━━━━━━━━━━━━━━━━
🤖 <i>V-Zones A/V Method</i>
⚡ <i>Это тестовое сообщение</i>"""

payload = json.dumps({
    "chat_id":    CHAT_ID,
    "text":       test_message,
    "parse_mode": "HTML",
    "disable_web_page_preview": True,
}).encode("utf-8")

req = Request(TELEGRAM_API, data=payload, headers={"Content-Type": "application/json"})

try:
    with urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
        if result.get("ok"):
            print("✅ Тестовое сообщение успешно отправлено в Telegram!")
            print(f"   Message ID: {result['result']['message_id']}")
        else:
            print(f"❌ Ошибка Telegram API: {result}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
