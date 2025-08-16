
import os
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import yfinance as yf
import pandas as pd

app = Flask(__name__)
# Allow your production origins + localhost for testing
CORS(app, resources={r"/*": {"origins": [
    "https://stockpricepredictions.com",
    "https://www.stockpricepredictions.com",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
]}})

# ---------- Utilities ----------
def _ensure_symbol(sym: str) -> str:
    sym = (sym or "").strip().upper()
    return sym

def _last_two_trading_rows(symbol: str):
    t = yf.Ticker(symbol)
    df = t.history(period="14d", interval="1d", auto_adjust=False)
    if df is None or df.empty:
        return None, None
    df = df.dropna(subset=["Open","High","Low","Close"])
    if df.shape[0] < 1:
        return None, None
    # last valid = previous day (relative to now). Use the last index row in history.
    last = df.iloc[-1]
    # try to get an earlier one for reference if needed
    prev = df.iloc[-2] if df.shape[0] >= 2 else None
    return prev, last  # (older, latest)

def _next_trading_date(from_date: pd.Timestamp) -> datetime:
    d = from_date.to_pydatetime().date() + timedelta(days=1)
    # Simple weekday pass (Mon-Fri). Exchange holiday handling can be added if needed.
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d = d + timedelta(days=1)
    return datetime(d.year, d.month, d.day)

# ----- Simple placeholder model: predicted close = previous day's close -----
# Replace this with your trained model logic as needed.
def predict_next_close_from_prev(prev_row: pd.Series) -> float:
    return float(prev_row["Close"])

# ---------- Routes ----------
@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/predict-next", methods=["GET"])
def predict_next():
    symbol = _ensure_symbol(request.args.get("symbol", "AAPL"))
    if not symbol:
        return jsonify({"error": "symbol is required"}), 400

    prev, latest = _last_two_trading_rows(symbol)
    # We interpret "Previous Day OHLC" as the most recent completed trading day = 'latest'
    row = latest if latest is not None else prev
    if row is None:
        return jsonify({"error": "no OHLC available for symbol"}), 404

    prev_date = row.name  # pandas timestamp index
    pred_close = predict_next_close_from_prev(row)
    next_date = _next_trading_date(prev_date)

    payload = {
        "symbol": symbol,
        "previous_day": {
            "date": prev_date.strftime("%Y-%m-%d"),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": float(row.get("Volume", 0)) if pd.notna(row.get("Volume", 0)) else 0.0
        },
        "prediction": {
            "target_date": next_date.strftime("%Y-%m-%d"),
            "predicted_close": pred_close,
            "method": "previous_day_model"
        }
    }
    return jsonify(payload), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
