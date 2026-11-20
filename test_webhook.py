#!/usr/bin/env python3
"""
Тестирует локальный webhook-сервер — имитирует запрос от TradingView.

Запуск (сначала запусти webhook_server.py):
    python test_webhook.py
"""

import json
from urllib.request import Request, urlopen

WEBHOOK_URL = "http://localhost:5000"

# Имитируем JSON, который отправляет TradingView
test_signals = [
    {
        "name": "LONG Signal",
        "data": {
            "signal": "LONG",
            "price": 2650.50,
            "sl": 2635.20,
            "tp": 2681.10,
            "rr": 2.0,
            "time": "2026-11-20 05:00",
            "tf": "15",
            "sym": "OANDA:XAUUSD"
        }
    },
    {
        "name": "SHORT Signal",
        "data": {
            "signal": "SHORT",
            "price": 2680.00,
            "sl": 2698.50,
            "tp": 2643.00,
            "rr": 2.0,
            "time": "2026-11-20 06:30",
            "tf": "15",
            "sym": "OANDA:XAUUSD"
        }
    },
    {
        "name": "Zone Break",
        "data": {
            "signal": "ZONE_BREAK",
            "type": "Supply",
            "price": 2695.00,
            "time": "2026-11-20 07:15",
            "tf": "15",
            "sym": "OANDA:XAUUSD"
        }
    },
    {
        "name": "Point A",
        "data": {
            "signal": "POINT_A",
            "type": "Supply",
            "price": 2690.00,
            "time": "2026-11-20 08:00",
            "tf": "15",
            "sym": "OANDA:XAUUSD"
        }
    },
]

print("🧪 Тестирование webhook-сервера...")
print(f"   URL: {WEBHOOK_URL}\n")

for test in test_signals:
    print(f"📤 Отправка: {test['name']}...")
    payload = json.dumps(test["data"]).encode("utf-8")
    req = Request(WEBHOOK_URL, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            status = "✅" if result.get("status") == "ok" else "❌"
            print(f"   {status} Ответ: {result}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    print()

print("🏁 Тестирование завершено!")
