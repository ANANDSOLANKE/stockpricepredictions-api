import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import yfinance as yf
import pandas as pd

app = Flask(__name__)
# Allow only your domains; add more if needed
CORS(app, resources={r"/*": {"origins": [
    "https://stockpricepredictions.com",
    "https://www.stockpricepredictions.com"
]}})

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def yahoo_search(query: str, quotes=10):
    """Use Yahoo Finance public search API to find best tickers for a query."""
    url = "https://query2.finance.yahoo.com/v1/finance/search"
    params = {"q": query, "quotesCount": quotes, "newsCount": 0, "enableFuzzyQuery": True}
    try:
        r = requests.get(url, params=params, headers=YF_HEADERS, timeout=8)
        r.raise_for_status()
        data = r.json()
        return data.get("quotes", [])
    except Exception as e:
        return []

def yahoo_trending(region="IN"):
    """Yahoo trending tickers by region (e.g., IN, US)."""
    url = f"https://query1.finance.yahoo.com/v1/finance/trending/{region}"
    try:
        r = requests.get(url, headers=YF_HEADERS, timeout=8)
        r.raise_for_status()
        data = r.json()
        quotes = (data.get("finance", {}).get("result", [{}])[0].get("quotes", [])) or []
        return quotes
    except Exception:
        return []

def resolve_symbol(q: str):
    """Return best-guess ticker symbol for a free-text query."""
    q = (q or "").strip()
    if not q:
        return None, "empty"
    # If user already typed a plausible ticker (has dot suffix or uppercase), try as-is first
    if any(ch.isalpha() for ch in q):
        sym_try = q.upper().replace(" ", "")
        quotes = yahoo_search(sym_try, quotes=1)
        if quotes:
            return quotes[0].get("symbol"), None
    # Fallback: search and return the first equity-like quote
    quotes = yahoo_search(q, quotes=8)
    for it in quotes:
        if it.get("symbol"):
            return it["symbol"], None
    return None, "not_found"

def get_latest_ohlc(symbol: str):
    """Fetch latest daily OHLC using yfinance; return last non-NaN row."""
    t = yf.Ticker(symbol)
    # Fetch last 5 days to avoid holidays/NaN
    df = t.history(period="7d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None
    # Take last row with valid Close
    df = df.dropna(subset=["Close"])
    if df.empty:
        return None
    row = df.iloc[-1]
    return {
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": float(row["Close"]),
    }

@app.get("/")
def root():
    return jsonify({"ok": True, "service": "stockpricepredictions-api"})

@app.get("/suggest")
def suggest():
    q = request.args.get("q", "").strip()
    quotes = yahoo_search(q, quotes=10) if q else []
    results = []
    for it in quotes:
        results.append({
            "symbol": it.get("symbol"),
            "shortname": it.get("shortname") or it.get("longname"),
            "exchange": it.get("exchDisp") or it.get("exchange")
        })
    return jsonify({"query": q, "results": results})

@app.get("/trending")
def trending():
    region = request.args.get("region", "IN").upper()
    quotes = yahoo_trending(region=region)
    results = [{"symbol": q.get("symbol"), "shortname": q.get("shortName")} for q in quotes if q.get("symbol")]
    return jsonify({"region": region, "results": results})

@app.get("/stock")
def stock():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "missing query"}), 400
    symbol, err = resolve_symbol(q)
    if not symbol:
        return jsonify({"error": f"symbol not found for query: {q}"}), 404
    ohlc = get_latest_ohlc(symbol)
    if not ohlc:
        return jsonify({"error": f"no price data for {symbol}"}), 404
    # Yantra-style signal
    o, h, l, c = ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"]
    o9, h9, l9, c9 = o % 9, h % 9, l % 9, c % 9
    layer1 = (o9 + c9) % 9
    layer2 = (h9 - l9 + 9) % 9
    bindu = int((layer1 * layer2) % 9)
    signal = 1 if bindu >= 5 else 0
    return jsonify({
        "query": q,
        "ticker": symbol,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "bindu": bindu,
        "signal": signal
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
