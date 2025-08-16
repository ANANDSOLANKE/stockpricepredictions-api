from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote

app = Flask(__name__)
CORS(app)

# ---------- Ticker suffix -> timezone (heuristic) ----------
SUFFIX_TZ_MAP = {
    ".NS": "Asia/Kolkata",   # NSE India
    ".BO": "Asia/Kolkata",   # BSE India
    ".L":  "Europe/London",  # LSE
    ".PA": "Europe/Paris",   # Euronext Paris
    ".DE": "Europe/Berlin",  # Xetra
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
    for suf, tz in SUFFIX_TZ_MAP.items():
        if symbol.upper().endswith(suf.upper()):
            return tz
    try:
        tkr = yf.Ticker(symbol)
        fi = getattr(tkr, "fast_info", {}) or {}
        tz = fi.get("timezone") or fi.get("exchange_timezone")
        if not tz:
            info = tkr.info or {}
            tz = info.get("exchangeTimezoneName") or info.get("timezone")
        if tz:
            return tz
    except Exception:
        pass
    return "America/New_York"

def to_exchange_local_index(df: pd.DataFrame, exchange_tz: str) -> pd.DataFrame:
    if df.empty:
        return df
    if df.index.tz is None:
        df = df.tz_localize("UTC")
    return df.tz_convert(exchange_tz)

def next_business_day(d: datetime.date) -> datetime.date:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:
        nd += timedelta(days=1)
    return nd

def pick_last_completed_daily_bar(df: pd.DataFrame, exchange_tz: str):
    if df.empty:
        return None, None
    now_ex = datetime.now(ZoneInfo(exchange_tz))
    today_ex_date = now_ex.date()
    df_before = df[df.index.date < today_ex_date]
    if not df_before.empty:
        row = df_before.iloc[-1]
        last_date = df_before.index[-1].date()
        return row, last_date
    # Fallback (thin symbols)
    row = df.iloc[-1]
    last_date = df.index[-1].date()
    return row, last_date

def normalize_symbol(raw: str) -> str:
    """
    Accepts Google-style and Yahoo-style symbols and returns a Yahoo Finance symbol.
    """
    if not raw:
        return ""
    s = unquote(raw).strip().upper()
    if s.endswith(":1"):
        s = s[:-2]
    if s.startswith("^"):
        return s
    if ":" in s:
        ex, tk = s.split(":", 1)
        ex, tk = ex.strip(), tk.strip()
        if tk in ("NSEI","NIFTY","NIFTY50"):
            return "^NSEI"
        if tk in ("BSESN","SENSEX"):
            return "^BSESN"
        if ex in ("NSE","NSEI"): return f"{tk}.NS"
        if ex in ("BSE","BOM"):  return f"{tk}.BO"
        if ex in ("LON","LSE"):  return f"{tk}.L"
        if ex in ("PAR","EPA"):  return f"{tk}.PA"
        if ex in ("FRA","XETRA","ETR"): return f"{tk}.DE"
        if ex in ("HKG","HKEX"): return f"{tk}.HK"
        if ex in ("TO","TSX"):  return f"{tk}.TO"
        if ex in ("ASX",):      return f"{tk}.AX"
        # US exchanges use plain ticker on Yahoo
        if ex in ("NYSE","NYQ","NASDAQ","NAS"): return tk
        return tk
    if s in ("NSEI","NIFTY","NIFTY50"): return "^NSEI"
    if s in ("BSESN","SENSEX"):         return "^BSESN"
    return s

# ---------- SEARCH: Yahoo-style autocomplete ----------
def yahoo_autocomplete(query: str, count: int = 12):
    """
    Calls Yahoo Finance search and returns a compact list.
    """
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {"q": query, "lang": "en-US", "region": "US", "quotesCount": count, "newsCount": 0}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=6)
        r.raise_for_status()
        data = r.json() or {}
        quotes = data.get("quotes", []) or []
        out = []
        for q in quotes:
            # Filter out non-tradables if you like (keep indices, equities, ETFs, futures)
            sym = q.get("symbol")
            if not sym:
                continue
            out.append({
                "symbol": sym,                          # Yahoo-ready
                "shortname": q.get("shortname") or q.get("longname") or q.get("name"),
                "exch": q.get("exchDisp") or q.get("exchange"),
                "type": q.get("typeDisp") or q.get("quoteType"),
                "score": q.get("score"),
            })
        # Basic uniqueness by symbol
        seen = set()
        uniq = []
        for x in out:
            if x["symbol"] in seen: continue
            seen.add(x["symbol"])
            uniq.append(x)
        return uniq[:count]
    except Exception:
        return []

@app.get("/search")
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    # If user typed Google-style prefix, normalize to help match
    norm_hint = normalize_symbol(q)
    results = yahoo_autocomplete(q, count=12)
    # If normalization yields a different symbol, bubble it to top if present
    if norm_hint and any(r["symbol"] == norm_hint for r in results):
        results.sort(key=lambda r: 0 if r["symbol"] == norm_hint else 1)
    return jsonify(results)

# ---------- RIBBON: world snapshot ----------
RIBBON_SYMBOLS = [
    "^NSEI", "^BSESN", "^GSPC", "^DJI", "^IXIC", "^FTSE", "^GDAXI", "^FCHI", "^HSI", "^N225", "^STOXX50E",
    "TCS.NS", "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"
]

def fetch_change_for(symbols):
    """
    For a list of symbols, fetch last two daily closes and compute pct change.
    """
    if not symbols:
        return {}
    try:
        df = yf.download(symbols, period="7d", interval="1d", auto_adjust=False, progress=False, threads=False)
        # yfinance returns different shapes for one vs many; normalize
        if isinstance(df.columns, pd.MultiIndex):
            close = df["Close"]
        else:
            close = pd.DataFrame({"Close": df["Close"]})
        out = {}
        for sym in (symbols if isinstance(symbols, list) else [symbols]):
            try:
                s = close[sym].dropna().tail(2)
                last = float(s.iloc[-1]) if len(s) >= 1 else None
                prev = float(s.iloc[-2]) if len(s) >= 2 else None
                pct = None
                chg = None
                if last is not None and prev is not None and prev != 0:
                    chg = last - prev
                    pct = (chg / prev) * 100.0
                out[sym] = {"price": last, "change": chg, "changePercent": pct}
            except Exception:
                out[sym] = {"price": None, "change": None, "changePercent": None}
        return out
    except Exception:
        return {sym: {"price": None, "change": None, "changePercent": None} for sym in symbols}

@app.get("/ribbon")
def ribbon():
    data = fetch_change_for(RIBBON_SYMBOLS)
    # Also add display names
    enriched = []
    for sym in RIBBON_SYMBOLS:
        try:
            info = yf.Ticker(sym).fast_info
            name = getattr(yf.Ticker(sym), "info", {}) or {}
            short = name.get("shortName") or name.get("longName") or sym
        except Exception:
            short = sym
        enriched.append({
            "symbol": sym,
            "name": short,
            "price": data.get(sym, {}).get("price"),
            "change": data.get(sym, {}).get("change"),
            "changePercent": data.get(sym, {}).get("changePercent"),
        })
    return jsonify(enriched)

# ---------- CORE /stock endpoint (your improved logic) ----------
@app.get("/stock")
def stock():
    raw = (request.args.get("symbol") or request.args.get("q") or "").strip()
    symbol = normalize_symbol(raw)
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    ex_tz = guess_exchange_tz(symbol)
    try:
        hist = yf.download(
            tickers=symbol,
            period="7d",  # small lookback is enough
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False
        )
    except Exception as e:
        return jsonify({"error": f"fetch failed: {str(e)}"}), 502

    if hist is None or hist.empty:
        return jsonify({"error": "no data"}), 404

    hist = hist.rename(columns={c: c.capitalize() for c in hist.columns})
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in hist.columns]
    hist = hist[cols]

    hist = to_exchange_local_index(hist, ex_tz)

    row, last_completed_date = pick_last_completed_daily_bar(hist, ex_tz)
    if row is None:
        return jsonify({"error": "no data"}), 404

    prediction_date = next_business_day(last_completed_date)

    out = {
        "symbol": symbol,
        "exchange_timezone": ex_tz,
        "last_completed_session_date": last_completed_date.isoformat(),
        "prediction_date": prediction_date.isoformat(),
        "ohlc_used": {
            "open": float(row.get("Open", np.nan)),
            "high": float(row.get("High", np.nan)),
            "low":  float(row.get("Low", np.nan)),
            "close": float(row.get("Close", np.nan)),
            "volume": float(row.get("Volume", np.nan)) if "Volume" in row else None
        }
    }

    if request.args.get("debug") == "1":
        out["recent_bars"] = [
            {
                "date_ex": idx.date().isoformat(),
                "open": float(r.get("Open", np.nan)),
                "high": float(r.get("High", np.nan)),
                "low":  float(r.get("Low", np.nan)),
                "close": float(r.get("Close", np.nan)),
                "volume": float(r.get("Volume", np.nan)) if "Volume" in r else None
            }
            for idx, r in hist.tail(5).iterrows()
        ]
    return jsonify(out)

@app.get("/")
def root():
    return jsonify({"ok": True})
