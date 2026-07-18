#!/usr/bin/env python3
import asyncio
import calendar
import time
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import SHELLY_IP, TARIF_HC, TARIF_HP, ALERT_THRESHOLD_W, ABONNEMENT_MENSUEL, CALIBRATION_FACTOR
from db import init_db, get_conn, insert_alert


def abonnement_jour(date_str: str) -> float:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return ABONNEMENT_MENSUEL / calendar.monthrange(d.year, d.month)[1]

app = FastAPI(title="Energy Monitor")
STATIC_DIR = Path(__file__).parent / "static"

init_db()


# ── Helpers ──────────────────────────────────────────────────────────────────

def shelly_live():
    try:
        r = requests.get(f"http://{SHELLY_IP}/rpc/EM1.GetStatus?id=0", timeout=3)
        d = r.json()
        return {
            "power_w": round(d.get("act_power", 0.0), 1),
            "voltage": round(d.get("voltage", 0.0), 1),
            "current": round(d.get("current", 0.0), 2),
            "ts": int(time.time()),
        }
    except Exception:
        return None


# ── SSE live ─────────────────────────────────────────────────────────────────

async def live_generator():
    while True:
        data = shelly_live()
        if data:
            yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(5)


@app.get("/api/live")
async def live_stream():
    return StreamingResponse(live_generator(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/api/readings")
def readings(hours: int = 24):
    since = int(time.time()) - hours * 3600
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, power_w, is_hc FROM readings WHERE ts >= ? ORDER BY ts ASC",
            (since,)
        ).fetchall()
    return [{"ts": r["ts"], "power_w": r["power_w"], "is_hc": bool(r["is_hc"])} for r in rows]


@app.get("/api/today")
def today_cost():
    date_str = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM daily_costs WHERE date=?", (date_str,)
        ).fetchone()
    abo = abonnement_jour(date_str)
    if row:
        d = dict(row)
        d["abonnement"] = round(abo, 3)
        d["total_avec_abo"] = round(d["total_cost"] + abo, 2)
        return d
    return {"date": date_str, "kwh_hc": 0, "kwh_hp": 0, "cost_hc": 0, "cost_hp": 0,
            "total_cost": 0, "abonnement": round(abo, 3), "total_avec_abo": round(abo, 3)}


@app.get("/api/history")
def history(days: int = 30):
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_costs WHERE date >= ? ORDER BY date ASC",
            (since,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/alerts")
def alerts(limit: int = 20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/alerts/{alert_id}/ack")
def ack_alert(alert_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE alerts SET ack=1 WHERE id=?", (alert_id,))
    return {"ok": True}


@app.get("/api/stats")
def stats():
    """Mois en cours : total kWh et coût avec abonnement + projection fin de mois."""
    now = datetime.now()
    month = now.strftime("%Y-%m")
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    day_of_month = now.day
    abo_mois = ABONNEMENT_MENSUEL

    with get_conn() as conn:
        row = conn.execute("""
            SELECT SUM(kwh_hc) as kwh_hc, SUM(kwh_hp) as kwh_hp,
                   SUM(total_cost) as total_cost, COUNT(*) as nb_jours
            FROM daily_costs WHERE date LIKE ?
        """, (f"{month}%",)).fetchone()

    energie = row["total_cost"] if row and row["total_cost"] else 0
    kwh_hc  = row["kwh_hc"]    if row and row["kwh_hc"]    else 0
    kwh_hp  = row["kwh_hp"]    if row and row["kwh_hp"]    else 0
    nb_jours = row["nb_jours"] if row and row["nb_jours"]  else 1

    abo_prorata = abo_mois * day_of_month / days_in_month
    total_avec_abo = round(energie + abo_prorata, 2)

    # Projection fin de mois
    avg_jour_energie = energie / nb_jours if nb_jours else 0
    projection = round(avg_jour_energie * days_in_month + abo_mois, 2)

    return {
        "kwh_hc": kwh_hc, "kwh_hp": kwh_hp,
        "total_cost": round(energie, 2),
        "abonnement_prorata": round(abo_prorata, 2),
        "total_avec_abo": total_avec_abo,
        "projection_fin_mois": projection,
    }


@app.get("/api/day/{date_str}")
def day_detail(date_str: str):
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return {"error": "format invalide, utiliser YYYY-MM-DD"}

    abo = abonnement_jour(date_str)

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM daily_costs WHERE date=?", (date_str,)).fetchone()
        hourly_rows = conn.execute("""
            SELECT
                CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER) as hour,
                AVG(power_w) as avg_w,
                MAX(is_hc) as is_hc,
                COUNT(*) as n
            FROM readings
            WHERE date(ts, 'unixepoch', 'localtime') = ?
            GROUP BY hour ORDER BY hour
        """, (date_str,)).fetchall()

    summary = dict(row) if row else {
        "date": date_str, "kwh_hc": 0, "kwh_hp": 0,
        "cost_hc": 0, "cost_hp": 0, "total_cost": 0
    }
    summary["abonnement"] = round(abo, 3)
    summary["total_avec_abo"] = round(summary["total_cost"] + abo, 2)

    hourly = []
    for r in hourly_rows:
        kwh = r["avg_w"] / 1000 * CALIBRATION_FACTOR
        hourly.append({
            "hour": r["hour"],
            "avg_w": round(r["avg_w"], 1),
            "kwh": round(kwh, 3),
            "is_hc": bool(r["is_hc"]),
        })

    return {"summary": summary, "hourly": hourly}


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html)


@app.get("/day/{date_str}", response_class=HTMLResponse)
def day_page(date_str: str):
    html = (STATIC_DIR / "day.html").read_text()
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
