from flask import Flask, request, jsonify
from flask_cors import CORS
import pandas as pd, numpy as np, yfinance as yf, requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import unquote

app = Flask(__name__)
CORS(app)

SUFFIX_TZ_MAP = {".NS":"Asia/Kolkata",".BO":"Asia/Kolkata",".L":"Europe/London",".PA":"Europe/Paris",
".DE":"Europe/Berlin",".F":"Europe/Berlin",".HK":"Asia/Hong_Kong",".T":"Asia/Tokyo",".SS":"Asia/Shanghai",
".SZ":"Asia/Shanghai",".SI":"Asia/Singapore",".AX":"Australia/Sydney",".TO":"America/Toronto",".V":"America/Toronto",
".KS":"Asia/Seoul",".KQ":"Asia/Seoul"}

def guess_exchange_tz(symbol):
    for suf,tz in SUFFIX_TZ_MAP.items():
        if symbol.upper().endswith(suf.upper()): return tz
    try:
        fi = yf.Ticker(symbol).fast_info or {}
        tz = fi.get("timezone") or fi.get("exchange_timezone")
        if not tz:
            info = yf.Ticker(symbol).info or {}
            tz = info.get("exchangeTimezoneName") or info.get("timezone")
        if tz: return tz
    except: pass
    return "America/New_York"

def to_exchange_local_index(df, ex_tz):
    if df.empty: return df
    if df.index.tz is None: df = df.tz_localize("UTC")
    return df.tz_convert(ex_tz)

def next_business_day(d):
    nd = d+timedelta(days=1)
    while nd.weekday()>=5: nd+=timedelta(days=1)
    return nd

def pick_last_completed_daily_bar(df, ex_tz):
    if df.empty: return None,None
    today_ex = datetime.now(ZoneInfo(ex_tz)).date()
    df_before = df[df.index.date<today_ex]
    if not df_before.empty:
        return df_before.iloc[-1], df_before.index[-1].date()
    return df.iloc[-1], df.index[-1].date()

def normalize_symbol(raw):
    if not raw: return ""
    s=unquote(raw).strip().upper()
    if s.endswith(":1"): s=s[:-2]
    if s.startswith("^"): return s
    if ":" in s:
        ex,tk=s.split(":",1); ex,tk=ex.strip(),tk.strip()
        if tk in ("NSEI","NIFTY","NIFTY50"): return "^NSEI"
        if tk in ("BSESN","SENSEX"): return "^BSESN"
        if ex in ("NSE","NSEI"): return f"{tk}.NS"
        if ex in ("BSE","BOM"): return f"{tk}.BO"
        if ex in ("LON","LSE"): return f"{tk}.L"
        if ex in ("PAR","EPA"): return f"{tk}.PA"
        if ex in ("FRA","XETRA","ETR"): return f"{tk}.DE"
        if ex in ("HKG","HKEX"): return f"{tk}.HK"
        if ex in ("TO","TSX"): return f"{tk}.TO"
        if ex in ("ASX",): return f"{tk}.AX"
        if ex in ("NYSE","NYQ","NASDAQ","NAS"): return tk
        return tk
    if s in ("NSEI","NIFTY","NIFTY50"): return "^NSEI"
    if s in ("BSESN","SENSEX"): return "^BSESN"
    return s

def yahoo_autocomplete(query,count=12):
    try:
        r=requests.get("https://query2.finance.yahoo.com/v1/finance/search",
            params={"q":query,"lang":"en-US","region":"US","quotesCount":count,"newsCount":0},
            headers={"User-Agent":"Mozilla/5.0"},timeout=6)
        r.raise_for_status()
        quotes=r.json().get("quotes",[])
        out=[]
        for q in quotes:
            sym=q.get("symbol")
            if not sym: continue
            out.append({"symbol":sym,"shortname":q.get("shortname") or q.get("longname") or q.get("name"),
                        "exch":q.get("exchDisp") or q.get("exchange"),"type":q.get("typeDisp") or q.get("quoteType")})
        uniq=[]; seen=set()
        for x in out:
            if x["symbol"] in seen: continue
            seen.add(x["symbol"]); uniq.append(x)
        return uniq[:count]
    except: return []

@app.get("/search")
def search(): return jsonify(yahoo_autocomplete(request.args.get("q","")))

RIBBON_SYMBOLS=["^NSEI","^BSESN","^GSPC","^DJI","^IXIC","^FTSE","^GDAXI","^FCHI","^HSI","^N225","^STOXX50E",
"TCS.NS","RELIANCE.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS"]

def fetch_change_for(symbols):
    try:
        df=yf.download(symbols,period="7d",interval="1d",progress=False,threads=False)
        close=df["Close"] if isinstance(df.columns,pd.MultiIndex) else pd.DataFrame({"Close":df["Close"]})
        out={}
        for sym in symbols:
            try:
                s=close[sym].dropna().tail(2)
                last=float(s.iloc[-1]) if len(s)>=1 else None
                prev=float(s.iloc[-2]) if len(s)>=2 else None
                chg=pct=None
                if last and prev: chg=last-prev; pct=chg/prev*100
                out[sym]={"price":last,"change":chg,"changePercent":pct}
            except: out[sym]={"price":None,"change":None,"changePercent":None}
        return out
    except: return {sym:{"price":None,"change":None,"changePercent":None} for sym in symbols}

@app.get("/ribbon")
def ribbon(): return jsonify(fetch_change_for(RIBBON_SYMBOLS))

@app.get("/stock")
def stock():
    sym=normalize_symbol(request.args.get("symbol") or request.args.get("q") or "")
    if not sym: return jsonify({"error":"symbol is required"}),400
    ex_tz=guess_exchange_tz(sym)
    try: hist=yf.download(sym,period="7d",interval="1d",progress=False,threads=False)
    except Exception as e: return jsonify({"error":str(e)}),502
    if hist is None or hist.empty: return jsonify({"error":"no data"}),404
    hist=hist.rename(columns={c:c.capitalize() for c in hist.columns})
    hist=to_exchange_local_index(hist,ex_tz)
    row,last_date=pick_last_completed_daily_bar(hist,ex_tz)
    if row is None: return jsonify({"error":"no data"}),404
    pred=next_business_day(last_date)
    out={"symbol":sym,"exchange_timezone":ex_tz,
         "last_completed_session_date":last_date.isoformat(),
         "prediction_date":pred.isoformat(),
         "ohlc_used":{"open":float(row.Open),"high":float(row.High),
                      "low":float(row.Low),"close":float(row.Close),
                      "volume":float(row.Volume) if "Volume" in row else None}}
    return jsonify(out)

@app.get("/")
def root(): return jsonify({"ok":True})

if __name__=="__main__": app.run(host="0.0.0.0",port=8000,debug=True)
