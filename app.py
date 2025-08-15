# app.py
import os, time
from threading import Lock
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

@app.get("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())}), 200

# ---- simple in-memory cache ----
CACHE_TTL = 300  # 5 minutes
_cache = {"stock": {}, "indices": {"ts": 0, "data": []}}
_lock = Lock()

def _safe_float(v):
    try:
        x = float(v)
        return x if x == x else None
    except Exception:
        return None

def _fetch_ohlc(symbol):
    """Return (open, high, low, close) for the latest day or raise."""
    t = yf.Ticker(symbol)
    hist = t.history(period="1d", interval="1d")
    if hist is None or hist.empty:
        raise RuntimeError(f"No data for {symbol}")
    row = hist.iloc[-1]
    o = _safe_float(row.get("Open"))
    h = _safe_float(row.get("High"))
    l = _safe_float(row.get("Low"))
    c = _safe_float(row.get("Close"))
    if None in (o, h, l, c):
        raise RuntimeError(f"Incomplete data for {symbol}")
    return o, h, l, c

@app.get("/stock")
def stock():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "missing q"}), 400

    now = time.time()
    with _lock:
        hit = _cache["stock"].get(q)
        if hit and now - hit["ts"] < CACHE_TTL:
            return jsonify(hit["data"])

    try:
        o, h, l, c = _fetch_ohlc(q)
        data = {"ticker": q, "open": o, "high": h, "low": l, "close": c}
        with _lock:
            _cache["stock"][q] = {"ts": now, "data": data}
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 502

# ---------- batched indices endpoint (ONE call from frontend) ----------
INDICES = [
    {"name":"S&P 500",    "sym":"^GSPC"},
    {"name":"Dow Jones",  "sym":"^DJI"},
    {"name":"Nasdaq 100", "sym":"^NDX"},
    {"name":"FTSE 100",   "sym":"^FTSE"},
    {"name":"DAX",        "sym":"^GDAXI"},
    {"name":"CAC 40",     "sym":"^FCHI"},
    {"name":"Nikkei 225", "sym":"^N225"},
    {"name":"Hang Seng",  "sym":"^HSI"},
    {"name":"ASX 200",    "sym":"^AXJO"},
    {"name":"Sensex",     "sym":"^BSESN"},
    {"name":"Nifty 50",   "sym":"^NSEI"},
    {"name":"Bank Nifty", "sym":"^NSEBANK"},
]

@app.get("/indices")
def indices():
    now = time.time()
    with _lock:
        if now - _cache["indices"]["ts"] < CACHE_TTL and _cache["indices"]["data"]:
            return jsonify({"results": _cache["indices"]["data"]})

    results = []
    # fetch sequentially to keep memory low on free tier
    for it in INDICES:
        name, sym = it["name"], it["sym"]
        try:
            o, h, l, c = _fetch_ohlc(sym)
            chg = c - o
            pct = (chg / o * 100.0) if o else 0.0
            results.append({
                "name": name,
                "symbol": sym,
                "price": round(c, 2),
                "chg": round(chg, 2),
                "pct": round(pct, 2),
                "up": chg >= 0
            })
        except Exception:
            results.append({
                "name": name, "symbol": sym,
                "price": "-", "chg": 0.0, "pct": 0.0, "up": False
            })

    with _lock:
        _cache["indices"]["ts"] = now
        _cache["indices"]["data"] = results
    return jsonify({"results": results})

# ---------- search suggestions ----------
@app.get("/suggest")
def suggest():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    try:
        # Use Yahoo search API directly (fast + light)
        import requests
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=10&newsCount=0"
        resp = requests.get(url, timeout=8)
        js = resp.json()
        out = []
        for r in js.get("quotes", []):
            out.append({
                "symbol": r.get("symbol"),
                "shortname": r.get("shortname") or r.get("longname") or "",
                "exchange": r.get("exchDisp") or r.get("exchange") or "",
            })
        return jsonify({"results": out[:10]})
    except Exception as e:
        return jsonify({"results": [], "error": str(e)}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
