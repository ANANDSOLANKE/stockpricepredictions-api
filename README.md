# stockpricepredictions-api (Render-ready)

## Endpoints
- `GET /health` → `{ "status": "ok" }`
- `GET /predict-next?symbol=RELIANCE.NS`
  - Returns:
    - `previous_day.date` (yyyy-mm-dd)
    - `prediction.target_date` (next trading day)
    - OHLC + predicted close (baseline = previous close)

## Deploy (Render)
1. Create a Python Web Service.
2. Upload these files (`app.py`, `Procfile`, `requirements.txt`).
3. Build command: `pip install -r requirements.txt`
4. Start command from `Procfile` is already `web: gunicorn app:app`.
5. Open `/health` to verify, then `/predict-next?symbol=RELIANCE.NS`.

## Notes
- CORS allows your domains and localhost.
- Replace `predict_next_close_from_prev(...)` with your model.