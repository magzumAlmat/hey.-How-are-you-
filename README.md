# hey.-How-are-you-

V-Zones (Supply & Demand A/V Method) — автономный Telegram-бот и Pine Script v6 индикатор для `OANDA:XAUUSD`.

## Файлы
- `vzones_bot.py` — автономный бот: получает свечи `OANDA:XAUUSD`, строит зоны Supply/Demand (`SWING_LEN = 6`, `TF = 5m`, `RR = 1:1`), сканирует каждую минуту и отправляет крайние `LONG`/`SHORT` сигналы + 30-минутный отчёт о работе в Telegram.
- `macro_context.py` — проверка 6 макро-факторов перед сигналом: `DXY`, `US10Y`, отчёты CPI/NFP, события ФРС, геополитика/новости (RSS) и окно волатильности (ForexFactory).
- `tv_feed.py` — клиент котировок TradingView (без логина).
- `vzones_av.pine` — индикатор TradingView (Pine Script v6).
- `webhook_server.py` — опциональный webhook-сервер для платных планов TradingView.
- `test_signal.py`, `test_webhook.py` — тестовые скрипты.

## Установка и запуск
```bash
pip3 install -r requirements.txt
cp .env.example .env   # впиши TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID
python3 vzones_bot.py --test
python3 vzones_bot.py
```
