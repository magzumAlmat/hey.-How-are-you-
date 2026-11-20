#!/usr/bin/env python3
"""
V-Zones TradingView → Telegram Webhook Server
===============================================
Принимает webhook от TradingView и пересылает сигналы в Telegram.

Запуск:
    python webhook_server.py

Переменные окружения (опционально — можно оставить значения по умолчанию):
    TELEGRAM_BOT_TOKEN  — токен бота
    TELEGRAM_CHAT_ID    — ID чата
    WEBHOOK_PORT        — порт сервера (по умолчанию 5000)
    WEBHOOK_SECRET      — секретный ключ (опционально, для безопасности)
"""

import json
import logging
import os
import sys
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError

def _load_dotenv() -> None:
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ─────────────────────────────────────────────
# КОНФИГУРАЦИЯ — ИЗ .env ИЛИ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ─────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
PORT      = int(os.environ.get("WEBHOOK_PORT", "5000"))
SECRET    = os.environ.get("WEBHOOK_SECRET", "")  # Опционально

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

# ─────────────────────────────────────────────
# ЛОГИРОВАНИЕ
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("vzones_webhook.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("vzones")

# ─────────────────────────────────────────────
# EMOJI И ФОРМАТИРОВАНИЕ
# ─────────────────────────────────────────────
SIGNAL_EMOJI = {
    "LONG":       "🟢",
    "SHORT":      "🔴",
    "POINT_A":    "🔺",
    "POINT_V":    "🔻",   # не используется напрямую, но на всякий случай
    "ZONE_BREAK": "💥",
}

def format_telegram_message(data: dict) -> str:
    """Форматирует JSON от TradingView в красивое Telegram-сообщение (HTML)."""
    signal = data.get("signal", "UNKNOWN")
    emoji  = SIGNAL_EMOJI.get(signal, "📊")
    sym    = data.get("sym", "N/A")
    tf     = data.get("tf", "")
    price  = data.get("price", "—")
    time_  = data.get("time", "")

    lines = []

    if signal in ("LONG", "SHORT"):
        direction = "LONG ▲" if signal == "LONG" else "SHORT ▼"
        sl = data.get("sl", "—")
        tp = data.get("tp", "—")
        rr = data.get("rr", "—")

        lines = [
            f"{emoji} <b>V-Zones: СИГНАЛ {direction}</b>",
            "",
            f"📌 <b>Инструмент:</b> {sym}",
            f"⏱ <b>Таймфрейм:</b> {tf}",
            f"🕐 <b>Время:</b> {time_}",
            "",
            f"💰 <b>Цена входа:</b> {price}",
            f"🛑 <b>Stop Loss:</b> {sl}",
            f"🎯 <b>Take Profit:</b> {tp}",
            f"📐 <b>R:R =</b> 1:{rr}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "🤖 <i>V-Zones A/V Method</i>",
        ]

    elif signal == "POINT_A":
        lines = [
            f"{emoji} <b>V-Zones: Точка A (Supply)</b>",
            "",
            f"📌 {sym} | {tf}",
            f"🕐 {time_}",
            f"📍 Цена: {price}",
            "",
            "<i>Формируется зона сопротивления. Ожидай SHORT сигнал.</i>",
        ]

    elif signal == "ZONE_BREAK":
        zone_type = data.get("type", "?")
        lines = [
            f"{emoji} <b>V-Zones: {zone_type} зона ПРОБИТА</b>",
            "",
            f"📌 {sym} | {tf}",
            f"🕐 {time_}",
            f"📍 Цена пробоя: {price}",
            "",
            f"<i>Зона {zone_type} больше не активна.</i>",
        ]

    else:
        # Fallback — просто выводим JSON как текст
        lines = [
            f"📊 <b>V-Zones Alert</b>",
            "",
            f"<pre>{json.dumps(data, indent=2, ensure_ascii=False)}</pre>",
        ]

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    """Отправляет сообщение в Telegram через Bot API."""
    payload = json.dumps({
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = Request(TELEGRAM_API, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("ok"):
                log.info("✅ Telegram: сообщение отправлено")
                return True
            else:
                log.error(f"❌ Telegram API ошибка: {result}")
                return False
    except URLError as e:
        log.error(f"❌ Telegram: сетевая ошибка — {e}")
        return False
    except Exception as e:
        log.error(f"❌ Telegram: неизвестная ошибка — {e}")
        return False


# ─────────────────────────────────────────────
# HTTP СЕРВЕР (принимает вебхуки)
# ─────────────────────────────────────────────
class WebhookHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        # Проверка секрета (если задан)
        if SECRET:
            auth = self.headers.get("X-Webhook-Secret", "")
            if auth != SECRET:
                self.send_response(403)
                self.end_headers()
                self.wfile.write(b"Forbidden")
                log.warning("⚠️ Запрос с неверным секретом отклонён")
                return

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8").strip()

        log.info(f"📥 Получен webhook: {body[:500]}")

        # Пробуем распарсить JSON
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Если пришёл простой текст — оборачиваем
            data = {"signal": "RAW", "message": body}

        # Формируем сообщение
        message = format_telegram_message(data)

        # Отправляем в Telegram
        ok = send_telegram(message)

        # Отвечаем TradingView
        self.send_response(200 if ok else 500)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = json.dumps({"status": "ok" if ok else "error"})
        self.wfile.write(resp.encode())

    def do_GET(self):
        """Health check endpoint."""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        resp = json.dumps({
            "service": "V-Zones Webhook → Telegram",
            "status":  "running",
            "time":    datetime.utcnow().isoformat() + "Z",
        })
        self.wfile.write(resp.encode())

    def log_message(self, format, *args):
        """Перенаправляем HTTP-логи в наш логгер."""
        log.info(f"HTTP: {args[0]} {args[1]} {args[2]}")


def main():
    log.info("=" * 50)
    log.info("V-Zones Webhook → Telegram Server")
    log.info(f"  Bot:     @MACDALERTTTBOT")
    log.info(f"  Chat ID: {CHAT_ID}")
    log.info(f"  Port:    {PORT}")
    log.info(f"  Secret:  {'ВКЛЮЧЁН' if SECRET else 'ОТКЛЮЧЁН'}")
    log.info("=" * 50)

    # Тестовое сообщение при запуске
    send_telegram(
        "🚀 <b>V-Zones Webhook Server запущен!</b>\n\n"
        f"🕐 {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        "📡 Ожидаю сигналы от TradingView..."
    )

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    log.info(f"🌐 Сервер слушает на http://0.0.0.0:{PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("🛑 Сервер остановлен")
        server.server_close()


if __name__ == "__main__":
    main()
