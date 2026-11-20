"""
Минимальный клиент данных TradingView (без логина) — свечи OANDA:XAUUSD.
Те же котировки, что на графике TradingView "XAUUSD OANDA".
"""
import json
import random
import re
import string

import pandas as pd
from websocket import create_connection

WS_URL = "wss://data.tradingview.com/socket.io/websocket"
HEADERS = {"Origin": "https://data.tradingview.com"}


def _sid(prefix: str) -> str:
    return prefix + "".join(random.choices(string.ascii_lowercase, k=12))


def _frame(func: str, params: list) -> str:
    msg = json.dumps({"m": func, "p": params}, separators=(",", ":"))
    return f"~m~{len(msg)}~m~{msg}"


def get_bars(symbol: str = "OANDA:XAUUSD", interval: str = "15",
             n_bars: int = 500, timeout: int = 20) -> pd.DataFrame:
    """
    interval: "1","5","15","30","60","240","1D"
    Возвращает DataFrame [Open, High, Low, Close, Volume], индекс — время открытия бара (UTC).
    """
    ws = create_connection(WS_URL, header=HEADERS, timeout=timeout)
    cs = _sid("cs_")
    try:
        ws.send(_frame("set_auth_token", ["unauthorized_user_token"]))
        ws.send(_frame("chart_create_session", [cs, ""]))
        ws.send(_frame("resolve_symbol", [cs, "sym_1",
                 '={"symbol":"' + symbol + '","adjustment":"splits"}']))
        ws.send(_frame("create_series", [cs, "s1", "s1", "sym_1", interval, n_bars]))

        bars = {}
        while True:
            raw = ws.recv()
            # Heartbeat — нужно отвечать тем же
            for hb in re.findall(r"~m~\d+~m~(~h~\d+)", raw):
                ws.send(f"~m~{len(hb)}~m~{hb}")
            for part in re.split(r"~m~\d+~m~", raw):
                if not part.startswith("{"):
                    continue
                try:
                    msg = json.loads(part)
                except json.JSONDecodeError:
                    continue
                m = msg.get("m")
                if m in ("timescale_update", "du"):
                    series = msg["p"][1].get("s1", {}).get("s", [])
                    for b in series:
                        v = b["v"]
                        bars[v[0]] = v[:6] if len(v) >= 6 else v[:5] + [0]
                elif m in ("symbol_error", "series_error", "critical_error"):
                    raise RuntimeError(f"TradingView: {msg}")
            if '"series_completed"' in raw:
                break
    finally:
        ws.close()

    if not bars:
        raise RuntimeError("TradingView: нет данных")

    df = pd.DataFrame(sorted(bars.values()),
                      columns=["time", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    return df.drop(columns="time")


if __name__ == "__main__":
    d = get_bars()
    print(d.tail(5))
    print(len(d), "баров")
