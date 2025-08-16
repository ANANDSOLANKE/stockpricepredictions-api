import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

SUFFIX_TZ = {
    ".NS": "Asia/Kolkata", ".BO": "Asia/Kolkata",
    ".L": "Europe/London",
    ".TO": "America/Toronto", ".V": "America/Toronto",
    ".HK": "Asia/Hong_Kong",
    ".T": "Asia/Tokyo",
    ".SS": "Asia/Shanghai", ".SZ": "Asia/Shanghai",
    ".KS": "Asia/Seoul",
    ".TW": "Asia/Taipei",
    ".SI": "Asia/Singapore",
    ".AX": "Australia/Sydney",
    ".NZ": "Pacific/Auckland"
}
DEFAULT_TZ = "America/New_York"

INDEX_ALIASES = {
    "NIFTY": "^NSEI", "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "NIFTYBANK": "^NSEBANK",
    "SENSEX": "^BSESN",
    "DOW": "^DJI", "DJI": "^DJI",
    "SPX": "^GSPC", "S&P500": "^GSPC",
    "NASDAQ": "^IXIC"
}

def guess_exchange_tz(ticker: str) -> str:
    t = ticker.upper()
    for suf, tz in SUFFIX_TZ.items():
        if t.endswith(suf.upper()):
            return tz
    if t.startswith("^"):
        if t.startswith("^NSE") or t.startswith("^BSE"):
            return "Asia/Kolkata"
    return DEFAULT_TZ

def next_weekday(d: datetime) -> datetime:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd

def choose_completed_bar(df: pd.DataFrame, tzname: str) -> pd.Series | None:
    if df is None or df.empty:
        return None
    if df.index.tz is None:
        df = df.tz_localize("UTC")
    df = df.tz_convert(ZoneInfo(tzname))

    now_tz = datetime.now(ZoneInfo(tzname))
    today_date = now_tz.date()

    df_before_today = df[df.index.date < today_date]
    if not df_before_today.empty:
        return df_before_today.iloc[-1]
    return df.iloc[-1]

def build_payload(ticker: str, row: pd.Series, tzname: str, rows_count: int):
    used_dt = row.name
    used_session_date = used_dt.date()
    pred_date = next_weekday(datetime(
        used_session_date.year, used_session_date.month, used_session_date.day,
        tzinfo=ZoneInfo(tzname)
    )).date()
    o = float(row["Open"]); h = float(row["High"]); l = float(row["Low"]); c = float(row["Close"])
    return {
        "ticker": ticker,
        "exchange_timezone": tzname,
        "used_session_date": used_session_date.isoformat(),
        "prediction_date": pred_date.isoformat(),
        "ohlc": {"open": o, "high": h, "low": l, "close": c},
        "source_rows": int(rows_count),
        "note": "Bar = last completed session strictly before 'today' in exchange tz."
    }

def _dl_once(sym: str, period: str):
    return yf.download(sym, period=period, interval="1d",
                       auto_adjust=False, progress=False, threads=False)

def smart_download(q: str):
    tried = []
    cand = INDEX_ALIASES.get(q.upper(), q)
    cands = [cand]
    if ("." not in q) and (not q.startswith("^")):
        cands.append(q + ".NS")  # handy default for India

    for sym in cands:
        for period in ("5d", "10d"):
            tried.append(f"{sym}@{period}")
            try:
                df = _dl_once(sym, period)
            except Exception:
                df = None
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0] for c in df.columns]
                keep = df[["Open","High","Low","Close"]].dropna(how="all")
                if not keep.empty:
                    return sym, keep, tried
    return None, None, tried

@app.get("/health")
def health():
    return {"status": "ok"}, 200

@app.get("/stock")
def stock():
    q = (request.args.get("q") or "").strip()
    debug = request.args.get("debug") == "1"
    if not q:
        return jsonify({"error": "missing query param 'q'"}), 400

    sym, df, tried = smart_download(q)
    if df is None:
        payload = {"error": "no data"}
        if debug:
            payload["tried"] = tried
        return jsonify(payload), 404

    tzname = guess_exchange_tz(sym or q)
    row = choose_completed_bar(df, tzname)
    if row is None:
        payload = {"error": "could not choose bar"}
        if debug:
            payload["tried"] = tried
            payload["rows"] = int(df.shape[0]) if df is not None else 0
        return jsonify(payload), 404

    out = build_payload(sym or q, row, tzname, rows_count=df.shape[0])
    if debug:
        out["tried"] = tried
    return jsonify(out), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
