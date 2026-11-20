#!/usr/bin/env python3
"""
V-Zones Bot — OANDA:XAUUSD → Telegram
======================================
Точный перенос логики Pine Script "V-Zones: Supply & Demand [A/V Method]".
Данные: свечи OANDA:XAUUSD с TradingView (тот же график, бесплатно, без логина).
Работает по расписанию OANDA, каждая закрытая свеча обрабатывается ровно один раз.

Запуск:
    pip install pandas numpy websocket-client
    python3 vzones_bot.py            # рабочий режим
    python3 vzones_bot.py --test     # проверка: скан истории + тестовое сообщение
"""

import http.client
import json
import logging
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import tv_feed

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv() -> None:
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ═══════════════════════════════════════════════
# КОНФИГУРАЦИЯ
# ═══════════════════════════════════════════════
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOL       = "OANDA:XAUUSD"
TF_MINUTES   = 5             # Таймфрейм: 1, 5, 15, 30, 60, 240
N_BARS       = 1000          # История для построения зон

# Параметры Pine Script (input'ы)
SWING_LEN      = 6
ATR_PERIOD     = 14
ATR_MULT       = 1.0
MAX_ZONES      = 5
V_CONFIRM_BARS = 2
RR_RATIO       = 1.0

# Какие алерты слать — только сигналы входа
ALERT_A     = False   # Точка A сформирована
ALERT_V     = True    # LONG сигнал
ALERT_SHORT = True    # SHORT сигнал
ALERT_BREAK = False   # Зона пробита
SCAN_SEC      = 60    # Скан графика каждые N секунд
HEARTBEAT_MIN = 10    # Сообщение "бот работает" каждые N минут

USER_TZ  = ZoneInfo("Asia/Yekaterinburg")   # UTC+5
NY_TZ    = ZoneInfo("America/New_York")     # время расписания OANDA

# ═══════════════════════════════════════════════
# ЛОГИ
# ═══════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(os.path.join(BASE_DIR, "vzones_bot.log"), encoding="utf-8")],
)
log = logging.getLogger("vzones")


# ═══════════════════════════════════════════════
# РАСПИСАНИЕ OANDA XAUUSD
#   Вс 18:00 NY → Пт 17:00 NY, ежедневный перерыв 17:00–18:00 NY
#   (зимой в UTC+5: Пн 04:00 → Сб 03:00, перерыв 03:00–04:00)
# ═══════════════════════════════════════════════
def market_open_at(dt: datetime) -> bool:
    ny = dt.astimezone(NY_TZ)
    wd, h = ny.weekday(), ny.hour
    if wd == 5:                 # суббота
        return False
    if wd == 6:                 # воскресенье
        return h >= 18
    if wd == 4:                 # пятница
        return h < 17
    return h != 17              # Пн–Чт, перерыв 17:00–18:00


def market_open() -> bool:
    return market_open_at(datetime.now(timezone.utc))


def next_open() -> datetime:
    t = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    for _ in range(4 * 24 * 60):
        t += timedelta(minutes=1)
        if market_open_at(t):
            return t
    return t


def fmt_user(dt: datetime) -> str:
    return dt.astimezone(USER_TZ).strftime("%d.%m %H:%M")


# ═══════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════
def _post_json_ipv4(host: str, path: str, body: bytes, timeout: int = 15) -> tuple[int, str]:
    """HTTPS POST строго по IPv4 (обходит недоступный IPv6-маршрут на macOS)."""
    addrs = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
    last_err: Optional[Exception] = None
    ctx = ssl.create_default_context()
    for _, _, _, _, sockaddr in addrs:
        sock = None
        try:
            sock = socket.create_connection(sockaddr, timeout=timeout)
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            conn = http.client.HTTPSConnection(host, 443, timeout=timeout)
            conn.sock = ssock
            conn.request("POST", path, body=body, headers={"Content-Type": "application/json", "Host": host})
            resp = conn.getresponse()
            data = resp.read().decode(errors="ignore")
            conn.close()
            return resp.status, data
        except Exception as e:
            last_err = e
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
    raise last_err or RuntimeError(f"Нет IPv4-адресов для {host}")


def send_telegram(text: str) -> bool:
    path = f"/bot{BOT_TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True}).encode()
    for attempt in range(3):
        try:
            status, body = _post_json_ipv4("api.telegram.org", path, payload, timeout=15)
            if status == 200 and json.loads(body).get("ok"):
                log.info("✅ Telegram: отправлено")
                return True
            log.error(f"❌ Telegram HTTP {status}: {body}")
            if status == 429:
                try:
                    time.sleep(json.loads(body)["parameters"]["retry_after"] + 1)
                    continue
                except Exception:
                    pass
            if status in (400, 401, 403):
                return False
        except Exception as e:
            log.error(f"❌ Telegram: {e}")
        time.sleep(3)
    return False


# ═══════════════════════════════════════════════
# ДАННЫЕ
# ═══════════════════════════════════════════════
@dataclass
class Zone:
    kind: str
    top: float
    bottom: float
    active: bool = True


@dataclass
class Signal:
    kind: str                    # LONG / SHORT / POINT_A / BREAK
    bar_time: datetime           # время закрытия свечи
    price: float
    sl: Optional[float] = None
    tp: Optional[float] = None
    zone: str = ""


def format_signal(s: Signal, test: bool = False) -> str:
    tf = f"{TF_MINUTES}m" if TF_MINUTES < 60 else f"{TF_MINUTES // 60}H"
    head = "🧪 <b>ТЕСТ</b> (последний сигнал из истории)\n\n" if test else ""
    when = fmt_user(s.bar_time) + " (UTC+5)"
    if s.kind in ("LONG", "SHORT"):
        long = s.kind == "LONG"
        return head + (
            f"{'🟢' if long else '🔴'} <b>V-Zones: {'LONG ▲' if long else 'SHORT ▼'}</b>\n\n"
            f"📌 XAUUSD · OANDA · {tf}\n"
            f"🕐 {when}\n\n"
            f"💰 <b>Вход:</b> {s.price:.2f}\n"
            f"🛑 <b>SL:</b> {s.sl:.2f}  ({abs(s.price - s.sl):.2f})\n"
            f"🎯 <b>TP:</b> {s.tp:.2f}  ({abs(s.tp - s.price):.2f})\n"
            f"📐 <b>R:R</b> 1:{RR_RATIO:g}\n\n"
            f"<i>{'Точка V у Demand зоны' if long else 'Точка A у Supply зоны'}</i>"
        )
    if s.kind == "POINT_A":
        return head + (f"🔺 <b>V-Zones: точка A (Supply)</b>\n\n"
                       f"📌 XAUUSD · OANDA · {tf}\n🕐 {when}\n📍 Пик: {s.price:.2f}")
    if s.kind == "BREAK":
        return head + (f"💥 <b>V-Zones: {s.zone} зона пробита</b>\n\n"
                       f"📌 XAUUSD · OANDA · {tf}\n🕐 {when}\n📍 Close: {s.price:.2f}")
    return f"V-Zones: {s.kind} {s.price:.2f}"


# ═══════════════════════════════════════════════
# ДВИЖОК — побарный перенос Pine Script
# ═══════════════════════════════════════════════
class VZonesEngine:
    def __init__(self):
        self.supply: list[Zone] = []
        self.demand: list[Zone] = []
        self.last_bar: Optional[pd.Timestamp] = None

    @staticmethod
    def atr(df: pd.DataFrame) -> pd.Series:
        # ta.atr = RMA(True Range)
        pc = df["Close"].shift(1)
        tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(),
                        (df["Low"] - pc).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1 / ATR_PERIOD, adjust=False).mean()

    @staticmethod
    def _pivot(arr: np.ndarray, j: int, L: int, high: bool) -> float:
        """ta.pivothigh/pivotlow(L, L), подтверждённый на баре j (пивот на j-L)."""
        c = j - L
        v = arr[c]
        left, right = arr[c - L:c], arr[c + 1:j + 1]
        if high:
            ok = v > left.max() and v >= right.max()
        else:
            ok = v < left.min() and v <= right.min()
        return v if ok else np.nan

    def _add(self, zones: list, kind: str, p: float, w: float):
        zones.append(Zone(kind, p + w, p - w))
        while len(zones) > MAX_ZONES:
            zones.pop(0)

    def process_bar(self, df: pd.DataFrame, atr: pd.Series, j: int) -> list[Signal]:
        L = SWING_LEN
        if j < 2 * L + V_CONFIRM_BARS + 1 or j < ATR_PERIOD:
            return []
        o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
        close_time = (df.index[j] + pd.Timedelta(minutes=TF_MINUTES)).to_pydatetime()
        w = atr.iloc[j] * ATR_MULT
        sigs: list[Signal] = []

        ph = self._pivot(h, j, L, True)
        pl = self._pivot(l, j, L, False)

        if not np.isnan(ph):
            self._add(self.supply, "Supply", ph, w)
        if not np.isnan(pl):
            self._add(self.demand, "Demand", pl, w)

        # Пробой зон (одно сообщение на тип зоны за свечу)
        for zones, name, broken in ((self.supply, "Supply", lambda z: c[j] > z.top),
                                    (self.demand, "Demand", lambda z: c[j] < z.bottom)):
            hit = [z for z in zones if z.active and broken(z)]
            for z in hit:
                z.active = False
            if hit:
                sigs.append(Signal("BREAK", close_time, c[j], zone=name))

        # Точка A
        point_a = not np.isnan(ph) and any(
            z.active and z.bottom <= ph <= z.top + w for z in self.supply)

        # Точка V (+ подтверждение ростом закрытий)
        point_v = False
        if not np.isnan(pl) and any(z.active and z.bottom - w <= pl <= z.top for z in self.demand):
            point_v = all(c[j - k] >= c[j - k - 1] for k in range(1, V_CONFIRM_BARS + 1))

        if point_v and c[j] > o[j]:
            sl = l[j] - w * 1.2
            sigs.append(Signal("LONG", close_time, c[j], sl, c[j] + (c[j] - sl) * RR_RATIO))

        if point_a and c[j] < o[j]:
            sl = ph + w * 1.5
            sigs.append(Signal("SHORT", close_time, c[j], sl, c[j] - (sl - c[j]) * RR_RATIO))
        elif point_a:
            sigs.append(Signal("POINT_A", close_time, ph))

        return sigs

    def update(self, df: pd.DataFrame) -> list[Signal]:
        """Обрабатывает только новые закрытые бары."""
        atr = self.atr(df)
        out = []
        for j in range(len(df)):
            t = df.index[j]
            if self.last_bar is not None and t <= self.last_bar:
                continue
            out.extend(self.process_bar(df, atr, j))
            self.last_bar = t
        return out

    def status(self) -> str:
        return (f"Supply {sum(z.active for z in self.supply)} | "
                f"Demand {sum(z.active for z in self.demand)}")


def wanted(s: Signal) -> bool:
    return {"LONG": ALERT_V, "SHORT": ALERT_SHORT,
            "POINT_A": ALERT_A, "BREAK": ALERT_BREAK}.get(s.kind, False)


def fetch_closed() -> Optional[pd.DataFrame]:
    """Свечи OANDA:XAUUSD, только закрытые (формирующаяся отбрасывается)."""
    try:
        df = tv_feed.get_bars(SYMBOL, str(TF_MINUTES), N_BARS)
    except Exception as e:
        log.error(f"❌ Данные: {e}")
        return None
    now = pd.Timestamp.now(tz="UTC")
    df = df[df.index + pd.Timedelta(minutes=TF_MINUTES) <= now]
    log.info(f"📊 {SYMBOL}: {len(df)} закрытых баров, последний close {df['Close'].iloc[-1]:.2f} "
             f"({fmt_user(df.index[-1].to_pydatetime() + timedelta(minutes=TF_MINUTES))})")
    return df


def sleep_to_next_scan():
    """Спим до следующей минуты + 5 сек (скан каждую минуту)."""
    now = time.time()
    step = SCAN_SEC
    nxt = (int(now) // step + 1) * step + 5
    time.sleep(max(nxt - now, 5))


# ═══════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════
def run_test():
    df = fetch_closed()
    if df is None:
        sys.exit(1)
    eng = VZonesEngine()
    sigs = [s for s in eng.update(df) if wanted(s)]
    print(f"\nЗоны: {eng.status()}\nСигналов в истории ({len(df)} баров): {len(sigs)}")
    for s in sigs[-15:]:
        print(f"  {fmt_user(s.bar_time)}  {s.kind:8s} {s.price:.2f}"
              + (f"  SL {s.sl:.2f} TP {s.tp:.2f}" if s.sl else ""))
    entries = [s for s in sigs if s.kind in ("LONG", "SHORT")]
    if entries:
        ok = send_telegram(format_signal(entries[-1], test=True))
        print("\nTelegram:", "OK" if ok else "ОШИБКА")


def main():
    log.info(f"V-Zones Bot | {SYMBOL} {TF_MINUTES}m | скан каждые {SCAN_SEC}с | chat {CHAT_ID}")
    eng = VZonesEngine()
    initialized = False
    started = datetime.now(timezone.utc)
    scans = 0
    errors = 0
    last_price = None
    last_sent: Optional[Signal] = None
    pending_signal: Optional[Signal] = None
    last_hb_slot = None

    while True:
        is_open = market_open()
        try:
            df = fetch_closed() if (is_open or not initialized) else None
            if df is not None and len(df) >= 50:
                scans += 1
                last_price = df["Close"].iloc[-1]
                sigs = [s for s in eng.update(df) if wanted(s)]

                if not initialized:
                    # Первый проход: строим зоны по истории, старые сигналы НЕ отправляем
                    initialized = True
                    if sigs:
                        s = sigs[-1]
                        log.info(f"История: последний сигнал {s.kind} {s.price:.2f} "
                                 f"({fmt_user(s.bar_time)}) — не отправлен")
                elif sigs:
                    # Только крайний (самый свежий) сигнал
                    pending_signal = sigs[-1]

                if pending_signal is not None:
                    age = (datetime.now(timezone.utc) - pending_signal.bar_time).total_seconds()
                    if age <= TF_MINUTES * 60 * 2:
                        log.info(f"📤 {pending_signal.kind} {pending_signal.price:.2f}")
                        if send_telegram(format_signal(pending_signal)):
                            last_sent = pending_signal
                            pending_signal = None
                    else:
                        log.info(f"Пропущен устаревший сигнал {pending_signal.kind} ({int(age // 60)} мин)")
                        pending_signal = None

                log.info(f"✓ скан #{scans} | {eng.status()} | новых сигналов: {len(sigs)}")
            elif is_open:
                errors += 1
        except KeyboardInterrupt:
            break
        except Exception as e:
            errors += 1
            log.exception(f"Ошибка скана: {e}")

        # ── Отчёт о работе раз в HEARTBEAT_MIN минут ──
        now = datetime.now(timezone.utc)
        slot = int(now.timestamp()) // (HEARTBEAT_MIN * 60)
        if slot != last_hb_slot:
            up = int((now - started).total_seconds() // 60)
            mkt = "🟢 открыт, сканирую" if is_open else f"🔴 закрыт, откроется {fmt_user(next_open())}"
            txt = (f"🤖 <b>V-Zones: бот работает</b>\n\n"
                   f"🕐 {fmt_user(now)} (UTC+5) · аптайм {up // 60}ч {up % 60}м\n"
                   f"📌 XAUUSD · OANDA · {TF_MINUTES}m · скан каждую минуту\n"
                   f"📈 Рынок: {mkt}\n")
            if last_price is not None:
                txt += f"💰 Цена: {last_price:.2f}\n"
            txt += f"🔍 Сканов: {scans} · 🗂 Зоны: {eng.status()}\n"
            txt += (f"📍 Последний сигнал: {last_sent.kind} {last_sent.price:.2f} ({fmt_user(last_sent.bar_time)})"
                    if last_sent else "📍 Новых сигналов пока нет")
            if errors:
                txt += f"\n⚠️ Ошибок загрузки: {errors}"
            if send_telegram(txt):
                last_hb_slot = slot

        sleep_to_next_scan()


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        main()
