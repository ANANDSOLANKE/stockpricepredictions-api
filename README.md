# stockpricepredictions API (Render)
Endpoints:
- GET / -> { ok: true }
- GET /suggest?q=QUERY
- GET /trending?region=IN
- GET /stock?q=QUERY

Run locally:
  pip install -r requirements.txt
  python app.py
Production (Render):
  Build: pip install -r requirements.txt
  Start: gunicorn app:app
