import os
from datetime import datetime, timedelta, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd
import pytz

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": [
    "https://stockpricepredictions.com",
    "https://www.stockpricepredictions.com",
    "https://stockpricepredictions-api.onrender.com",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
]}})

# ---------- Venue rules: timezone, hours, workweek ----------
# Python weekday: Mon=0 ... Sun=6
MON_FRI = {0,1,2,3,4}
SUN_THU = {6,0,1,2,3}  # e.g., Saudi (Fri/Sat weekend)

EXCHANGES = {
    # India
    ".NS": {"tz": "Asia/Kolkata", "start": (9,15),  "end": (15,30), "open_days": MON_FRI, "venue": "NSE"},
    ".BO": {"tz": "Asia/Kolkata", "start": (9,15),  "end": (15,30), "open_days": MON_FRI, "venue": "BSE"},

    # US
    ".NY": {"tz": "US/Eastern",   "start": (9,30),  "end": (16,0),  "open_days": MON_FRI, "venue": "NYSE"},
    ".O":  {"tz": "US/Eastern",   "start": (9,30),  "end": (16,0),  "open_days": MON_FRI, "venue": "NASDAQ"},
    # default fallback for others → US/Eastern below

    # UK
    ".L":  {"tz": "Europe/London","start": (8,0),   "end": (16,30), "open_days": MON_FRI, "venue": "LSE"},

    # Hong Kong
    ".HK": {"tz": "Asia/Hong_Kong","start": (9,30), "end": (16,0),  "open_days": MON_FRI, "venue": "HKEX"},

    # Japan
    ".T":  {"tz": "Asia/Tokyo",   "start": (9,0),   "end": (15,0),  "open_days": MON_FRI, "venue": "TSE"},

    # China (no lunch breaks modeled)
    ".SS": {"tz": "Asia/Shanghai","start": (9,30),  "end": (15,0),  "open_days": MON_FRI, "venue": "SSE"},
    ".SZ": {"tz": "Asia/Shanghai","start": (9,30),  "end": (15,0),  "open_days": MON_FRI, "venue": "SZSE"},

    # Canada
    ".TO": {"tz": "America/Toronto","start": (9,30),"end": (16,0),  "open_days": MON_FRI, "venue": "TSX"},

    # Australia
    ".AX": {"tz": "Australia/Sydney","start": (10,0),"end": (16,0), "open_days": MON_FRI, "venue": "ASX"},

    # New Zealand
    ".NZ": {"tz": "Pacific/Auckland","start": (10,0),"end": (16,45),"open_days": MON_FRI, "venue": "NZX"},

    # Brazil
    ".SA": {"tz": "America/Sao_Paulo","start": (10,0),"end": (17,30),"open_days": MON_FRI, "venue": "B3"},

    # Germany
    ".F":  {"tz": "Europe/Berlin","start": (8,0),   "end": (20,0),  "open_days": MON_FRI, "venue": "Xetra"},

    # France
    ".PA": {"tz": "Europe/Paris", "start": (9,0),   "end": (17,30), "open_days": MON_FRI, "venue": "Euronext Paris"},

    # Italy
    ".MI": {"tz": "Europe/Rome",  "start": (9,0),   "end": (17,30), "open_days": MON_FRI, "venue": "Borsa Italiana"},

    # Switzerland (Yahoo often .SW or .VX)
    ".SW": {"tz": "Europe/Zurich","start": (9,0),   "end": (17,30), "open_days": MON_FRI, "venue": "SIX"},
    ".VX": {"tz": "Europe/Zurich","start": (9,0),   "end": (17,30), "open_days": MON_FRI, "venue": "SIX"},

    # Korea
    ".KS": {"tz": "Asia/Seoul",   "start": (9,0),   "end": (15,30), "open_days": MON_FRI, "venue": "KRX"},
    ".KQ": {"tz": "Asia/Seoul",   "start": (9,0),   "end": (15,30), "open_days": MON_FRI, "venue": "KOSDAQ"},

    # Taiwan
    ".TW": {"tz": "Asia/Taipei",  "start": (9,0),   "end": (13,30), "open_days": MON_FRI, "venue": "TWSE"},

    # Singapore
    ".SI": {"tz": "Asia/Singapore","start": (9,0),  "end": (17,0),  "open_days": MON_FRI, "venue": "SGX"},

    # Thailand
    ".BK": {"tz": "Asia/Bangkok", "start": (10,0),  "end": (16,30), "open_days": MON_FRI, "venue": "SET"},

    # Saudi Arabia (Sun-Thu)
    ".SR": {"tz": "Asia/Riyadh",  "start": (10,0),  "end": (15,0),  "open_days": SUN_THU, "venue": "Tadawul"},
}

def _venue_info(symbol: str):
    s = symbol.upper()
    for suf, info in EXCHANGES.items():
        if s.endswith(suf):
            tz = pytz.timezone(info["tz"])
            start = time(*info["start"])
            end = time(*info["end"])
            open_days = info["open_days"]
            return info["venue"], tz, start, end, open_days
    # default → US/Eastern Mon–Fri 09:30–16:00
    return "US", pytz.timezone("US/Eastern"), time(9,30), time(16,0), MON_FRI

def _is_market_open_now(symbol: str):
    venue, tz, start, end, open_days = _venue_info(symbol)
    now = datetime.now(tz)
    return venue, tz, start, end, open_days, (now.weekday() in open_days and start <= now.time() <= end)

def _fetch_recent_daily(symbol: str):
    t = yf.Ticker(symbol)
    df = t.history(period="21d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None
    cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
    df = df[cols].dropna(subset=["Close"])
    return df if not df.empty else None

def _previous_completed_daily_row(df: pd.DataFrame, symbol: str):
    """Pick previous *completed* daily bar (never an in-progress session)."""
    if df is None or df.empty:
        return None, None
    venue, tz, start, end, open_days, open_now = _is_market_open_now(symbol)
    local_today = datetime.now(tz).date()
    last_idx = df.index[-1].date()

    # If last bar is stamped 'today' in venue tz and we are BEFORE end → step back one.
    if last_idx == local_today and not (datetime.now(tz).time() >= end):
        if len(df) >= 2:
            idx = df.index[-2]; row = df.iloc[-2]
        else:
            idx = df.index[-1]; row = df.iloc[-1]
    else:
        idx = df.index[-1]; row = df.iloc[-1]
    return idx, row

def _next_trading_date(from_idx: pd.Timestamp, symbol: str):
    venue, tz, start, end, open_days, _ = _is_market_open_now(symbol)
    d = from_idx.to_pydatetime().date() + timedelta(days=1)
    while d.weekday() not in open_days:
        d = d + timedelta(days=1)
    return datetime(d.year, d.month, d.day)

def predict_next_close_from_prev(prev_row: pd.Series) -> float:
    # Replace with your model if available
    return float(prev_row["Close"])

@app.route("/health", strict_slashes=False)
def health():
    return {"status": "ok"}, 200

@app.route("/predict-next", methods=["GET"], strict_slashes=False)
def predict_next():
    symbol = (request.args.get("symbol") or "RELIANCE.NS").strip().upper()
    df = _fetch_recent_daily(symbol)
    if df is None:
        return jsonify({"error": "no OHLC available for symbol"}), 404

    idx, prev_row = _previous_completed_daily_row(df, symbol)
    if idx is None or prev_row is None:
        return jsonify({"error": "insufficient data"}), 404

    target_date = _next_trading_date(idx, symbol)
    venue, tz, start, end, open_days, open_now = _is_market_open_now(symbol)

    payload = {
        "symbol": symbol,
        "previous_day": {
            "date": idx.strftime("%Y-%m-%d"),
            "open": float(prev_row["Open"]),
            "high": float(prev_row["High"]),
            "low": float(prev_row["Low"]),
            "close": float(prev_row["Close"]),
            "volume": float(prev_row.get("Volume", 0)) if pd.notna(prev_row.get("Volume", 0)) else 0.0
        },
        "prediction": {
            "target_date": target_date.strftime("%Y-%m-%d"),
            "predicted_close": float(predict_next_close_from_prev(prev_row)),
            "method": "previous_day_model"
        },
        "market_meta": {
            "venue": venue,
            "market_open_now": bool(open_now),
            "market_status": "Open" if open_now else "Closed",
            "local_tz": str(tz),
            "hours_local": {
                "start": f"{start.hour:02d}:{start.minute:02d}",
                "end": f"{end.hour:02d}:{end.minute:02d}",
                "open_days": sorted(list(open_days))
            },
            "evaluated_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z"
        }
    }
    return jsonify(payload), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
