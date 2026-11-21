"""
Макро- и новостной контекст для сделок по золоту (XAUUSD).

Проверяет 6 факторов перед сигналом:
  1. DXY (TVC:DXY) — индекс доллара укрепляется или слабеет
  2. US10Y (TVC:US10Y) — доходность 10-летних облигаций США растёт или падает
  3. CPI / NFP / инфляция — отчёты сегодня
  4. ФРС (Fed / FOMC) — выступления и решения по ставке
  5. Геополитика и новостной фон по золоту (Google News RSS)
  6. Окно волатильности — ближайшие важные USD-новости (ForexFactory)
"""

import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import tv_feed

log = logging.getLogger("vzones")

USER_TZ = ZoneInfo("Asia/Yekaterinburg")  # UTC+5
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_URL = (
    "https://news.google.com/rss/search?"
    "q=(gold+OR+XAUUSD)+(Fed+OR+dollar+OR+geopolitics+OR+war+OR+inflation)+when:1d"
    "&hl=en-US&gl=US&ceid=US:en"
)

MACRO_KEYWORDS_HIGH = (
    "cpi", "non-farm", "nonfarm", "nfp", "payroll", "unemployment",
    "pce", "ppi", "gdp", "inflation", "retail sales",
)
FED_KEYWORDS = (
    "fed", "fomc", "powell", "warsh", "waller", "cook", "williams",
    "bostic", "kashkari", "rate", "monetary", "minutes",
)
GEO_RE = re.compile(
    r"\b(war|wars|strike|strikes|missile|missiles|conflict|iran|israel|gaza|"
    r"ukraine|russia|china|tariff|tariffs|sanction|sanctions|safe[- ]haven|"
    r"geopolit\w*|attack|attacks|middle east|tension|tensions)\b",
    re.IGNORECASE,
)


@dataclass
class QuoteTrend:
    symbol: str
    price: float
    delta_1h: float
    pct_1h: float
    direction: str  # "up", "down", "flat"


@dataclass
class CalEvent:
    title: str
    when: datetime
    impact: str     # High / Medium / Low
    forecast: str = ""
    previous: str = ""


@dataclass
class MacroContext:
    fetched_at: datetime
    dxy: Optional[QuoteTrend] = None
    us10y: Optional[QuoteTrend] = None
    cpi_nfp_today: list[CalEvent] = field(default_factory=list)
    fed_events: list[CalEvent] = field(default_factory=list)
    near_events: list[CalEvent] = field(default_factory=list)
    next_major: Optional[CalEvent] = None
    geo_headlines: list[str] = field(default_factory=list)
    top_headlines: list[str] = field(default_factory=list)


def _fetch_json(url: str, timeout: int = 8):
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def _fetch_text(url: str, timeout: int = 8) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="ignore")


class MacroMonitor:
    """Кэширует и обновляет макро-данные без задержки 1-минутного цикла."""

    def __init__(self):
        self._quotes_ts = 0.0
        self._dxy: Optional[QuoteTrend] = None
        self._us10y: Optional[QuoteTrend] = None

        self._cal_ts = 0.0
        self._cal_events: list[CalEvent] = []

        self._news_ts = 0.0
        self._geo_headlines: list[str] = []
        self._top_headlines: list[str] = []

    def _fetch_trend(self, symbol: str, flat_pct: float) -> Optional[QuoteTrend]:
        try:
            df = tv_feed.get_bars(symbol, "5", 30, timeout=10)
            if df is None or len(df) < 2:
                return None
            last = float(df["Close"].iloc[-1])
            ref_idx = max(0, len(df) - 13)  # ~1 час (12 баров по 5м)
            ref = float(df["Close"].iloc[ref_idx])
            delta = last - ref
            pct = (delta / ref * 100.0) if ref else 0.0
            if pct >= flat_pct:
                direction = "up"
            elif pct <= -flat_pct:
                direction = "down"
            else:
                direction = "flat"
            return QuoteTrend(symbol, last, delta, pct, direction)
        except Exception as e:
            log.warning(f"⚠️ Макро ({symbol}): {e}")
            return None

    def _refresh_quotes(self, force: bool = False):
        now = time.time()
        if not force and now - self._quotes_ts < 120:
            return
        dxy = self._fetch_trend("TVC:DXY", flat_pct=0.03)
        us10y = self._fetch_trend("TVC:US10Y", flat_pct=0.10)
        if dxy is not None:
            self._dxy = dxy
        if us10y is not None:
            self._us10y = us10y
        self._quotes_ts = now

    def _refresh_calendar(self, force: bool = False):
        now = time.time()
        if not force and now - self._cal_ts < 1800 and self._cal_events:
            return
        try:
            raw = _fetch_json(FF_URL, timeout=8)
            events: list[CalEvent] = []
            for item in raw:
                if item.get("country") != "USD":
                    continue
                ds = item.get("date", "")
                if not ds:
                    continue
                try:
                    dt = datetime.fromisoformat(ds).astimezone(timezone.utc)
                except ValueError:
                    continue
                events.append(CalEvent(
                    title=str(item.get("title", "")).strip(),
                    when=dt,
                    impact=str(item.get("impact", "Low")),
                    forecast=str(item.get("forecast", "") or ""),
                    previous=str(item.get("previous", "") or ""),
                ))
            events.sort(key=lambda x: x.when)
            self._cal_events = events
            self._cal_ts = now
        except Exception as e:
            log.warning(f"⚠️ Календарь: {e}")

    def _refresh_news(self, force: bool = False):
        now = time.time()
        if not force and now - self._news_ts < 600 and self._top_headlines:
            return
        try:
            xml_text = _fetch_text(NEWS_URL, timeout=8)
            root = ET.fromstring(xml_text)
            top: list[str] = []
            geo: list[str] = []
            for item in root.findall(".//item")[:10]:
                title = (item.findtext("title") or "").strip()
                if not title:
                    continue
                # Убираем хвост " - Source" для компактности, если длинный
                clean = title.rsplit(" - ", 1)[0].strip()
                top.append(clean)
                if GEO_RE.search(clean):
                    geo.append(clean)
            self._top_headlines = top[:3]
            self._geo_headlines = geo[:2]
            self._news_ts = now
        except Exception as e:
            log.warning(f"⚠️ Новости RSS: {e}")

    def get_context(self, force_quotes: bool = False) -> MacroContext:
        self._refresh_quotes(force=force_quotes)
        self._refresh_calendar(force=False)
        self._refresh_news(force=False)

        now_utc = datetime.now(timezone.utc)
        today_user = now_utc.astimezone(USER_TZ).date()

        cpi_nfp_today: list[CalEvent] = []
        fed_events: list[CalEvent] = []
        near_events: list[CalEvent] = []
        next_major: Optional[CalEvent] = None

        for ev in self._cal_events:
            low = ev.title.lower()
            mins = (ev.when - now_utc).total_seconds() / 60.0
            ev_date = ev.when.astimezone(USER_TZ).date()

            if ev_date == today_user and any(k in low for k in MACRO_KEYWORDS_HIGH):
                cpi_nfp_today.append(ev)

            if -120 <= mins <= 24 * 60 and any(k in low for k in FED_KEYWORDS):
                fed_events.append(ev)

            if ev.impact in ("High", "Medium") and -20 <= mins <= 90:
                near_events.append(ev)

            if next_major is None and ev.impact in ("High", "Medium") and mins >= -15:
                next_major = ev

        return MacroContext(
            fetched_at=now_utc,
            dxy=self._dxy,
            us10y=self._us10y,
            cpi_nfp_today=cpi_nfp_today,
            fed_events=fed_events,
            near_events=near_events,
            next_major=next_major,
            geo_headlines=list(self._geo_headlines),
            top_headlines=list(self._top_headlines),
        )


def _fmt_time(dt: datetime) -> str:
    return dt.astimezone(USER_TZ).strftime("%H:%M")


def _fmt_rel(dt: datetime, now: datetime) -> str:
    mins = int(round((dt - now).total_seconds() / 60.0))
    if abs(mins) < 1:
        return "сейчас"
    if mins > 0:
        h, m = divmod(mins, 60)
        return f"через {h}ч {m}м" if h else f"через {m}м"
    mins = abs(mins)
    h, m = divmod(mins, 60)
    return f"{h}ч {m}м назад" if h else f"{m}м назад"


def _trend_verdict(trend_dir: str, direction: str) -> tuple[str, int]:
    """Возвращает (текст вердикта, балл: +1 за, -1 против, 0 нейтрально) для DXY/US10Y."""
    is_long = direction == "LONG"
    if trend_dir == "flat":
        return f"➖ нейтрально для {direction}", 0
    favorable = (is_long and trend_dir == "down") or (not is_long and trend_dir == "up")
    if favorable:
        return f"✅ ЗА {direction}", 1
    return f"⚠️ ПРОТИВ {direction}", -1


def format_macro_for_signal(direction: str, ctx: MacroContext) -> str:
    """Формирует HTML-блок с проверкой 6 макро-факторов и вердиктом по каждому пункту."""
    is_long = direction == "LONG"
    now = ctx.fetched_at
    lines = ["📋 <b>Чек-лист по золоту:</b>"]

    score = 0
    total = 0

    # 1. DXY
    if ctx.dxy:
        total += 1
        d = ctx.dxy
        arrow = "↑ укрепляется" if d.direction == "up" else "↓ слабеет" if d.direction == "down" else "→ флэт"
        v_txt, pts = _trend_verdict(d.direction, direction)
        if pts > 0:
            score += 1
        lines.append(f"1. <b>DXY:</b> {d.price:.2f} ({d.pct_1h:+.2f}% 1ч) — {arrow} → {v_txt}")
    else:
        lines.append(f"1. <b>DXY:</b> нет данных → ➖ нейтрально для {direction}")

    # 2. Доходность облигаций US10Y
    if ctx.us10y:
        total += 1
        y = ctx.us10y
        arrow = "↑ растёт" if y.direction == "up" else "↓ падает" if y.direction == "down" else "→ флэт"
        v_txt, pts = _trend_verdict(y.direction, direction)
        if pts > 0:
            score += 1
        lines.append(f"2. <b>US10Y:</b> {y.price:.3f}% ({y.delta_1h:+.3f} 1ч) — {arrow} → {v_txt}")
    else:
        lines.append(f"2. <b>US10Y:</b> нет данных → ➖ нейтрально для {direction}")

    # 3. CPI / NFP / инфляция сегодня
    if ctx.cpi_nfp_today:
        ev_txt = ", ".join(
            f"{html.escape(e.title)} ({_fmt_time(e.when)})"
            for e in ctx.cpi_nfp_today[:2]
        )
        upcoming = any((e.when - now).total_seconds() >= -1200 for e in ctx.cpi_nfp_today)
        v_txt = (f"⚠️ ОСТОРОЖНО для {direction} (риск импульса)" if upcoming
                 else f"➖ отчёт уже вышел (нейтрально для {direction})")
        lines.append(f"3. <b>CPI/NFP/инфляция:</b> {ev_txt} → {v_txt}")
    else:
        lines.append(f"3. <b>CPI/NFP сегодня:</b> нет отчётов → ✅ чисто для {direction}")

    # 4. ФРС
    if ctx.fed_events:
        ev = ctx.fed_events[0]
        upcoming = (ev.when - now).total_seconds() >= -1200
        v_txt = (f"⚠️ ОСТОРОЖНО для {direction} (возможны заявления)" if upcoming
                 else f"➖ выступление уже прошло (нейтрально для {direction})")
        lines.append(
            f"4. <b>ФРС:</b> {html.escape(ev.title)} ({_fmt_time(ev.when)}, {_fmt_rel(ev.when, now)}) → {v_txt}"
        )
    else:
        lines.append(f"4. <b>ФРС:</b> событий в 24ч нет → ✅ чисто для {direction}")

    # 5. Геополитика и новостной фон
    if ctx.geo_headlines:
        h = html.escape(ctx.geo_headlines[0][:90])
        v_txt = (f"✅ ЗА {direction} (спрос на защитное золото)" if is_long
                 else f"⚠️ ОСТОРОЖНО для {direction} (риск роста на геополитике)")
        lines.append(f"5. <b>Геополитика:</b> 🔥 {h} → {v_txt}")
    elif ctx.top_headlines:
        h = html.escape(ctx.top_headlines[0][:90])
        lines.append(f"5. <b>Фон:</b> {h} → ➖ нейтрально для {direction}")
    else:
        lines.append(f"5. <b>Геополитика:</b> спокойный фон → ✅ чисто для {direction}")

    # 6. Новости и волатильность (-20..+90 мин)
    if ctx.near_events:
        ev = ctx.near_events[0]
        rel = _fmt_rel(ev.when, now)
        lines.append(
            f"6. <b>Волатильность:</b> 🚨 [{ev.impact}] {html.escape(ev.title)} ({rel}) "
            f"→ 🚨 ОПАСНО для {direction} (всплеск волатильности)"
        )
    elif ctx.next_major:
        ev = ctx.next_major
        lines.append(
            f"6. <b>Волатильность:</b> спокойно (след.: {html.escape(ev.title)} {_fmt_time(ev.when)}) "
            f"→ ✅ безопасно для {direction}"
        )
    else:
        lines.append(f"6. <b>Волатильность:</b> важных новостей рядом нет → ✅ безопасно для {direction}")

    # Итоговый вердикт по макро
    if total > 0:
        if score == total:
            verdict = f"✅ <b>Итог по макро: ЗА {direction}</b> ({score}/{total} по DXY/US10Y)"
        elif score == 0:
            verdict = f"⚠️ <b>Итог по макро: ПРОТИВ {direction}</b> (0/{total} по DXY/US10Y)"
        else:
            verdict = f"⚖️ <b>Итог по макро: смешанный для {direction}</b> ({score}/{total} по DXY/US10Y)"
        if ctx.near_events:
            verdict += " · 🚨 близко новость!"
        lines.append("")
        lines.append(verdict)

    return "\n".join(lines)


def format_macro_compact(ctx: MacroContext) -> str:
    """Короткая строка макро-фона для 10-минутного отчёта."""
    parts = []
    if ctx.dxy:
        ar = "↑" if ctx.dxy.direction == "up" else "↓" if ctx.dxy.direction == "down" else "→"
        parts.append(f"DXY {ctx.dxy.price:.2f} ({ar})")
    if ctx.us10y:
        ar = "↑" if ctx.us10y.direction == "up" else "↓" if ctx.us10y.direction == "down" else "→"
        parts.append(f"US10Y {ctx.us10y.price:.3f}% ({ar})")
    line = "🌐 " + " · ".join(parts) if parts else ""
    if ctx.near_events:
        ev = ctx.near_events[0]
        extra = f"\n🚨 Скоро: [{ev.impact}] {html.escape(ev.title)} ({_fmt_rel(ev.when, ctx.fetched_at)})"
        line += extra
    elif ctx.fed_events:
        ev = ctx.fed_events[0]
        line += f"\n🏦 ФРС: {html.escape(ev.title)} ({_fmt_time(ev.when)} UTC+5)"
    return line
