import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd

app = Flask(__name__)
# Allow your domains + localhost for testing
CORS(app, resources={r"/*": {"origins": [
    "https://stockpricepredictions.com",
    "https://www.stockpricepredictions.com",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
]}})

# ---------- Utilities ----------
def _ensure_symbol(sym: str) -> str:
    return (sym or "").strip().upper()

def _fetch_recent_daily(symbol: str) -> pd.DataFrame | None:
    t = yf.Ticker(symbol)
    # buffer a couple of weeks to avoid holidays
    df = t.history(period="21d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None
    cols = [c for c in ["Open","High","Low","Close","Volume"] if c in df.columns]
    df = df[cols].dropna(subset=["Close"])
    if df.empty:
        return None
    return df

def _next_trading_date(prev_ts: pd.Timestamp) -> datetime:
    d = prev_ts.to_pydatetime().date() + timedelta(days=1)
    while d.weekday() >= 5:  # Sat/Sun
        d = d + timedelta(days=1)
    return datetime(d.year, d.month, d.day)

# Replace with your trained model
def predict_next_close_from_prev(prev_row: pd.Series) -> float:
    # Baseline: carry-forward previous close
    return float(prev_row["Close"])

# ---------- Routes ----------
@app.route("/health", strict_slashes=False)
def health():
    return {"status": "ok"}, 200

@app.route("/predict-next", methods=["GET"], strict_slashes=False)
def predict_next():
    symbol = _ensure_symbol(request.args.get("symbol", "AAPL"))
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    df = _fetch_recent_daily(symbol)
    if df is None or df.empty:
        return jsonify({"error": "no OHLC available for symbol"}), 404

    last_row = df.iloc[-1]
    prev_date = df.index[-1]
    next_date = _next_trading_date(prev_date)
    pred = predict_next_close_from_prev(last_row)

    return jsonify({
        "symbol": symbol,
        "previous_day": {
            "date": prev_date.strftime("%Y-%m-%d"),
            "open": float(last_row["Open"]),
            "high": float(last_row["High"]),
            "low": float(last_row["Low"]),
            "close": float(last_row["Close"]),
            "volume": float(last_row.get("Volume", 0)) if pd.notna(last_row.get("Volume", 0)) else 0.0
        },
        "prediction": {
            "target_date": next_date.strftime("%Y-%m-%d"),
            "predicted_close": float(pred),
            "method": "previous_day_model"
        }
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)