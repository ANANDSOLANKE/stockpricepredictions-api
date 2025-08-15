# app.py
import os, json, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf

app = Flask(__name__)
# Allow calls from your static site
CORS(app, resources={r"/*": {"origins": "*"}})

@app.get("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())}), 200

def safe_float(v, default=None):
    try:
        x = float(v)
        if x != x:  # NaN
            return default
        return x
    except Exception:
        return default

@app.get("/stock")
def stock():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "missing q"}), 400
    try:
        t = yf.Ticker(q)
        hist = t.history(period="1d", interval="1d")
        if hist is None or hist.empty:
            return jsonify({"error": f"No data found for {q}"}), 404
        row = hist.iloc[-1]
        open_ = safe_float(row.get("Open"))
        high_ = safe_float(row.get("High"))
        low_  = safe_float(row.get("Low"))
        close_= safe_float(row.get("Close"))
        if None in (open_, high_, low_, close_):
            return jsonify({"error": f"Incomplete data for {q}"}), 502
        return jsonify({
            "ticker": q,
            "open": open_,
            "high": high_,
            "low": low_,
            "close": close_,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/suggest")
def suggest():
    """
    Proxy to yfinance autocomplete source. yfinance exposes a helper via
    Ticker._get_fundamentals but we’ll use yfinance’s public endpoint indirectly
    by leveraging the search_tickers util (when available). Fallback simple guesses.
    """
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"results": []})
    try:
        # yfinance has a search function
        try:
            from yfinance import utils as yutils  # older versions may not have it
            resp = yutils.get_json(f"https://query2.finance.yahoo.com/v1/finance/search?q={q}&quotesCount=10&newsCount=0")
            quotes = resp.get("quotes", [])
            results = []
            for r in quotes:
                results.append({
                    "symbol": r.get("symbol"),
                    "shortname": r.get("shortname") or r.get("longname") or r.get("quoteType") or "",
                    "exchange": r.get("exchange") or r.get("exchDisp") or "",
                })
            return jsonify({"results": results[:10]})
        except Exception:
            pass

        # Fallback: try a few common suffixes if direct ticker works
        suffixes = [ "", ".NS", ".BO", ".NSE", ".BSE", ".L", ".DE", ".PA", ".HK", ".AX", ".TO", ".V" ]
        results = []
        base = q.upper().replace(" ", "")
        for s in suffixes:
            sym = base + s
            try:
                t = yf.Ticker(sym)
                info = t.fast_info  # light call
                if getattr(info, "last_price", None) is not None:
                    results.append({"symbol": sym, "shortname": q, "exchange": s.strip(".") or "—"})
            except Exception:
                continue
        return jsonify({"results": results[:10]})
    except Exception as e:
        return jsonify({"results": [], "error": str(e)}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
