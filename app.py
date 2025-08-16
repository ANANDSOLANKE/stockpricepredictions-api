from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+

app = Flask(__name__)
CORS(app)

# --- Exchange timezone helpers ------------------------------------------------

# Heuristic mapping by ticker suffix. Adjust/extend as you need.
SUFFIX_TZ_MAP = {
    ".NS": "Asia/Kolkata",   # NSE India
    ".BO": "Asia/Kolkata",   # BSE India
    ".L":  "Europe/London",  # LSE
    ".PA": "Europe/Paris",   # Euronext Paris
    ".DE": "Europe/Berlin",  # Xetra (alt suffixes like .F also exist)
    ".F":  "Europe/Berlin",
    ".HK": "Asia/Hong_Kong", # HKEX
    ".T":  "Asia/Tokyo",     # TSE
    ".SS": "Asia/Shanghai",  # Shanghai
    ".SZ": "Asia/Shanghai",  # Shenzhen
    ".SI": "Asia/Singapore", # SGX
    ".AX": "Australia/Sydney", # ASX
    ".TO": "America/Toronto",  # TSX
    ".V":  "America/Toronto",  # TSXV
    ".KS": "Asia/Seoul",     # KRX
    ".KQ": "Asia/Seoul",     # KOSDAQ
}

def guess_exchange_tz(symbol: str) -> str:
    # 1) Try by suffix
    for suf, tz in SUFFIX_TZ_MAP.items():
        if symbol.upper().endswith(suf.upper()):
            return tz
    # 2) Try yfinance metadata (best effort; may be slow/None sometimes)
    try:
        info = yf.Ticker(symbol).fast_info  # faster than .info
        tz = info.get("timezone")
        if tz:
            return tz
    except Exception:
        pass
    # 3) Fallback to US market tz
    return "America/New_York"

def to_exchange_local_index(df: pd.DataFrame, exchange_tz: str) -> pd.DataFrame:
    """Ensure index is timezone-aware and converted to exchange tz."""
    if df.empty:
        return df
    idx = df.index
    if idx.tz is None:
        # yfinance daily sometimes returns naive index; assume UTC then convert
        df = df.tz_localize("UTC")
    # Convert to exchange timezone
    df = df.tz_convert(exchange_tz)
    return df

def next_business_day(d: datetime.date) -> datetime.date:
    """Next weekday (Mon–Fri). Ignores local holidays by requirement."""
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:  # 5=Sat, 6=Sun
        nd += timedelta(days=1)
    return nd

def pick_last_completed_daily_bar(df: pd.DataFrame, exchange_tz: str):
    """
    From daily OHLC df indexed by datetime (already in exchange tz), pick last completed bar:
    - Take rows with index.date < 'today' in exchange tz.
    - If present, choose the last of those.
    - Else, if no such rows but df has at least one row, choose the most recent one.
    Returns (row_series, last_completed_date).
    """
    if df.empty:
        return None, None

    now_ex = datetime.now(ZoneInfo(exchange_tz))
    today_ex_date = now_ex.date()

    # Rows strictly before today in exchange tz
    mask_before_today = df.index.date < today_ex_date
    df_before = df[mask_before_today]

    if not df_before.empty:
        # Normal case: use yesterday’s bar (or last bar before today, e.g., Fri if today is Mon)
        row = df_before.iloc[-1]
        last_date = df_before.index[-1].date()
        return row, last_date
    else:
        # Edge: no row strictly before today (thin symbols / first day visible)
        # Fall back to most recent available bar
        row = df.iloc[-1]
        last_date = df.index[-1].date()
        return row, last_date

# --- API ----------------------------------------------------------------------

@app.route("/stock", methods=["GET"])
def stock():
    symbol = (request.args.get("symbol") or "").strip()
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    ex_tz = guess_exchange_tz(symbol)

    # Fetch a small recent window of daily bars (2–3 usable days). 5d gives buffer for weekends.
    try:
        hist = yf.download(
            tickers=symbol,
            period="7d",            # small lookback is enough
            interval="1d",
            auto_adjust=False,
            progress=False
        )
    except Exception as e:
        return jsonify({"error": f"fetch failed: {str(e)}"}), 502

    if hist is None or hist.empty:
        return jsonify({"error": "no data"}), 404

    # Standardize column names (yfinance can return lowercase)
    hist = hist.rename(columns={c: c.capitalize() for c in hist.columns})
    # Keep only OHLCV if present
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in hist.columns]
    hist = hist[cols]

    # Convert index to exchange timezone
    hist = to_exchange_local_index(hist, ex_tz)

    # Pick last completed session’s bar
    row, last_completed_date = pick_last_completed_daily_bar(hist, ex_tz)
    if row is None:
        return jsonify({"error": "no data"}), 404

    # Compute prediction date = next business day (ignoring local holidays)
    prediction_date = next_business_day(last_completed_date)

    # Build response
    out = {
        "symbol": symbol,
        "exchange_timezone": ex_tz,
        "last_completed_session_date": last_completed_date.isoformat(),  # YYYY-MM-DD in exchange tz
        "prediction_date": prediction_date.isoformat(),                  # YYYY-MM-DD (next weekday)
        "ohlc_used": {
            "open": float(row.get("Open", np.nan)),
            "high": float(row.get("High", np.nan)),
            "low":  float(row.get("Low", np.nan)),
            "close": float(row.get("Close", np.nan)),
            "volume": float(row.get("Volume", np.nan)) if "Volume" in row else None
        }
    }
    return jsonify(out), 200

# Health check (optional)
@app.route("/")
def root():
    return jsonify({"ok": True})

if __name__ == "__main__":
    # For local testing only. In production use gunicorn.
    app.run(host="0.0.0.0", port=8000, debug=True)
