import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, jsonify, request
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app)

# ---- Helpers ----

# Quick map by Yahoo suffix → local exchange tz (fallback to NY).
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

def guess_exchange_tz(ticker: str) -> str:
    """Infer the local exchange timezone from the ticker suffix."""
    for suf, tz in SUFFIX_TZ.items():
        if ticker.upper().endswith(suf.upper()):
            return tz
    return DEFAULT_TZ

def next_weekday(d: datetime) -> datetime:
    """Return next weekday (Mon–Fri), ignoring holidays."""
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:  # 5=Sat, 6=Sun
        nd += timedelta(days=1)
    return nd

def choose_completed_bar(df: pd.DataFrame, tzname: str) -> pd.Series:
    """
    Given daily OHLC df (index is tz-aware or naive UTC), choose the last completed bar
    strictly before 'today' in the exchange timezone.
    """
    if df is None or df.empty:
        return None

    # Ensure datetime index is timezone-aware in exchange tz
    if df.index.tz is None:
        # yfinance daily index is usually naive → treat as UTC then convert
        df = df.tz_localize("UTC")
    df = df.tz_convert(ZoneInfo(tzname))

    # 'today' in exchange timezone (date only)
    now_tz = datetime.now(ZoneInfo(tzname))
    today_date = now_tz.date()

    # Separate rows into (strictly) before today vs today/after
    df_before_today = df[df.index.date < today_date]

    if not df_before_today.empty:
        # If there ARE rows before today → use the last one (yesterday or earlier)
        return df_before_today.iloc[-1]
    else:
        # No rows strictly before today; market likely closed and last bar is “today” (from prior session)
        # In that case we use the most recent available row.
        return df.iloc[-1]

def build_payload(ticker: str, row: pd.Series, tzname: str, rows_count: int):
    # row.name is the timestamp in exchange tz
    used_dt = row.name  # tz-aware
    used_session_date = used_dt.date()

    # prediction date = next business day (Mon-Fri), same timezone (ignore holidays)
    pred_date = next_weekday(datetime(used_session_date.year, used_session_date.month, used_session_date.day, tzinfo=ZoneInfo(tzname))).date()

    o = float(row["Open"])
    h = float(row["High"])
    l = float(row["Low"])
    c = float(row["Close"])

    return {
        "ticker": ticker,
        "exchange_timezone": tzname,
        "used_session_date": used_session_date.isoformat(),   # date of last completed session (local tz)
        "prediction_date": pred_date.isoformat(),             # next weekday (ignores holidays)
        "ohlc": {"open": o, "high": h, "low": l, "close": c},
        "source_rows": rows_count,
        "note": "Bar chosen as last completed session strictly before 'today' in exchange timezone."
    }

# ---- Routes ----

@app.get("/health")
def health():
    return {"status": "ok"}, 200

@app.get("/stock")
def stock():
    """
    Query: /stock?q=RELIANCE.NS
    Downloads up to ~4 recent calendar days of daily data and picks the last completed session
    (strictly before 'today' in the exchange timezone). If 'today' exists, we still use yesterday's bar.
    If market closed (no 'today' bar), we use the most recent row.
    """
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query param 'q'"}), 400

    tzname = guess_exchange_tz(q)

    # Period: '5d' gives enough history to safely pick yesterday vs weekend across timezones.
    try:
        df = yf.download(q, period="5d", interval="1d", auto_adjust=False, progress=False)
    except Exception as e:
        return jsonify({"error": f"yfinance download failed: {e}"}), 502

    if df is None or df.empty:
        return jsonify({"error": "no data"}), 404

    # Clean columns if yfinance returns a multi-index (happens with adj columns or corporate actions)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    # Drop rows that have all-NaN OHLC
    keep = df[["Open", "High", "Low", "Close"]].dropna(how="all")
    if keep.empty:
        return jsonify({"error": "no valid OHLC"}), 404

    chosen = choose_completed_bar(keep, tzname)
    if chosen is None:
        return jsonify({"error": "could not choose bar"}), 404

    payload = build_payload(q, chosen, tzname, rows_count=int(keep.shape[0]))
    return jsonify(payload), 200


if __name__ == "__main__":
    # Render will run via Procfile, but this lets you test locally.
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
