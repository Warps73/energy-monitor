#!/usr/bin/env python3
import asyncio
import calendar
import os
import time
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import SHELLY_IP, TARIF_HC, TARIF_HP, ALERT_THRESHOLD_W, ABONNEMENT_MENSUEL, CALIBRATION_FACTOR, tariff_at
from db import init_db, get_conn, insert_alert


# ── .env.local loader (aucune dép externe) ──────────────────────────────────
def _load_env_local():
    p = Path(__file__).parent / ".env.local"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env_local()


def abonnement_jour(date_str: str) -> float:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return ABONNEMENT_MENSUEL / calendar.monthrange(d.year, d.month)[1]

app = FastAPI(title="Energy Monitor")
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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
    out = []
    for r in rows:
        d = dict(r)
        y, mo, _ = d["date"].split("-")
        days_in_month = calendar.monthrange(int(y), int(mo))[1]
        d["abo_day"] = round(tariff_at(d["date"])["abo"] / days_in_month, 4)
        out.append(d)
    return out


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


# ── Monta EV (public-api.monta.com/api/v1) ───────────────────────────────────

MONTA_BASE = "https://public-api.monta.com/api/v1"
_MONTA_TOKEN = {"accessToken": None, "expiresAt": 0.0}
_MONTA_SUMMARY = {}  # {month: {"data": ..., "expiresAt": ...}}
MONTA_REIMB_RATE = 0.20
MONTA_HC_WINDOWS_PARIS = [("14:26", "16:56"), ("23:56", "05:26")]


def _monta_token():
    now = time.time()
    if _MONTA_TOKEN["accessToken"] and _MONTA_TOKEN["expiresAt"] > now + 120:
        return _MONTA_TOKEN["accessToken"]
    cid = os.environ.get("MONTA_CLIENT_ID")
    csec = os.environ.get("MONTA_CLIENT_SECRET")
    if not cid or not csec:
        raise RuntimeError("MONTA_CLIENT_ID / MONTA_CLIENT_SECRET manquants (.env.local)")
    r = requests.post(
        f"{MONTA_BASE}/auth/token",
        json={"clientId": cid, "clientSecret": csec},
        timeout=10,
    )
    r.raise_for_status()
    d = r.json()
    _MONTA_TOKEN["accessToken"] = d["accessToken"]
    exp = datetime.fromisoformat(d["accessTokenExpirationDate"].replace("Z", "+00:00"))
    _MONTA_TOKEN["expiresAt"] = exp.timestamp()
    return _MONTA_TOKEN["accessToken"]


def _monta_get(path, params=None):
    r = requests.get(
        f"{MONTA_BASE}{path}",
        headers={"Authorization": f"Bearer {_monta_token()}"},
        params=params or {},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _monta_hc_ratio(start_utc, stop_utc):
    """Ratio HC au prorata du temps [plug-in, plug-out] — approximation."""
    if not start_utc or not stop_utc:
        return None
    a = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone()
    b = datetime.fromisoformat(stop_utc.replace("Z", "+00:00")).astimezone()
    if b <= a:
        return None
    total = (b - a).total_seconds()
    hc = 0.0
    cur = a
    step = timedelta(minutes=1)
    while cur < b:
        nxt = min(cur + step, b)
        hm = cur.strftime("%H:%M")
        for x, y in MONTA_HC_WINDOWS_PARIS:
            in_hc = (x <= hm < y) if x < y else (hm >= x or hm < y)
            if in_hc:
                hc += (nxt - cur).total_seconds()
                break
        cur = nxt
    return hc / total


CAR_CHARGING_MIN_W = 3000
CAR_BASELINE_W = 250


def _shelly_car_split(start_utc, stop_utc):
    """Répartition HC/HP réelle depuis Shelly EM (readings).

    Détecte les intervalles où power > CAR_CHARGING_MIN_W = voiture en charge.
    Retourne {hc_ratio, shelly_kwh_hc, shelly_kwh_hp, samples} ou None si pas de données.
    """
    if not start_utc or not stop_utc:
        return None
    a = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    b = datetime.fromisoformat(stop_utc.replace("Z", "+00:00"))
    if b <= a:
        return None
    t_start = int(a.timestamp())
    t_stop = int(b.timestamp())

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, power_w, is_hc FROM readings "
            "WHERE ts >= ? AND ts <= ? ORDER BY ts ASC",
            (t_start - 900, t_stop + 900)
        ).fetchall()
    if len(rows) < 2:
        return None

    kwh_hc = 0.0
    kwh_hp = 0.0
    samples_used = 0
    for i in range(1, len(rows)):
        prev, cur = rows[i-1], rows[i]
        if prev["power_w"] < CAR_CHARGING_MIN_W:
            continue
        t0 = max(prev["ts"], t_start)
        t1 = min(cur["ts"], t_stop)
        if t1 <= t0:
            continue
        dt_h = (t1 - t0) / 3600
        power_car = max(prev["power_w"] - CAR_BASELINE_W, 0)
        kwh = power_car / 1000 * dt_h * CALIBRATION_FACTOR
        if prev["is_hc"]:
            kwh_hc += kwh
        else:
            kwh_hp += kwh
        samples_used += 1

    total = kwh_hc + kwh_hp
    if total <= 0.01:
        return None
    return {
        "hc_ratio": kwh_hc / total,
        "shelly_kwh_hc": kwh_hc,
        "shelly_kwh_hp": kwh_hp,
        "samples": samples_used,
    }


def _monta_compute_summary(month=None):
    now_local = datetime.now().astimezone()
    tz = now_local.tzinfo
    if month:
        y, m = [int(x) for x in month.split("-")]
    else:
        y, m = now_local.year, now_local.month
    start_month = datetime(y, m, 1, tzinfo=tz)
    if m == 12:
        end_month = datetime(y + 1, 1, 1, tzinfo=tz)
    else:
        end_month = datetime(y, m + 1, 1, tzinfo=tz)
    is_current_month = (y == now_local.year and m == now_local.month)

    sessions = []
    per_page = 50
    page = 0
    while page < 20:
        r = _monta_get("/charges", {"perPage": per_page, "page": page})
        data = r.get("data", [])
        if not data:
            break
        stop = False
        for s in data:
            if not s.get("startedAt"):
                continue
            sd = datetime.fromisoformat(s["startedAt"].replace("Z", "+00:00")).astimezone()
            if sd >= end_month:
                continue
            if sd < start_month:
                stop = True
                continue
            sessions.append(s)
        if stop or len(data) < per_page:
            break
        page += 1

    total_kwh = sum(s.get("consumedKwh") or 0 for s in sessions)
    total_price = sum(s.get("price") or 0 for s in sessions)
    total_cost = sum(s.get("cost") or 0 for s in sessions)

    def _session_split(s):
        """Retourne (hc_ratio, source) — 'shelly' si dispo, sinon 'prorata'."""
        shelly = _shelly_car_split(s.get("startedAt"), s.get("stoppedAt"))
        if shelly and shelly["samples"] >= 3:
            return shelly["hc_ratio"], "shelly"
        return _monta_hc_ratio(s.get("startedAt"), s.get("stoppedAt")), "prorata"

    def _session_date(s):
        iso = s.get("startedAt")
        if not iso:
            return f"{y:04d}-{m:02d}-01"
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")

    hc_kwh, hp_kwh = 0.0, 0.0
    cost_est = 0.0
    cost_best = 0.0  # tout HC au tarif d'époque
    cost_worst = 0.0  # tout HP au tarif d'époque
    session_splits = {}
    for s in sessions:
        ratio, source = _session_split(s)
        session_splits[s["id"]] = (ratio, source)
        k = s.get("consumedKwh") or 0
        t = tariff_at(_session_date(s))
        if ratio is None:
            hp_kwh += k
            cost_est += k * t["hp"]
        else:
            hc_kwh += k * ratio
            hp_kwh += k * (1 - ratio)
            cost_est += k * ratio * t["hc"] + k * (1 - ratio) * t["hp"]
        cost_best += k * t["hc"]
        cost_worst += k * t["hp"]
    n_shelly = sum(1 for r, src in session_splits.values() if src == "shelly")

    # Dernière session : la plus récente du mois affiché. Fallback API uniquement
    # pour le mois en cours (sinon on afficherait une session hors-mois).
    last = None
    if sessions:
        last_s = sessions[0]
    elif is_current_month:
        r = _monta_get("/charges", {"perPage": 1})
        last_s = (r.get("data") or [None])[0]
    else:
        last_s = None
    if last_s:
        last = {
            "startedAt": last_s.get("startedAt"),
            "stoppedAt": last_s.get("stoppedAt"),
            "kwh": round(last_s.get("consumedKwh") or 0, 3),
            "price": round(last_s.get("price") or 0, 2),
            "state": last_s.get("state"),
            "humanReadableId": last_s.get("humanReadableId"),
        }

    sessions_light = []
    for s in sessions:
        ratio, source = session_splits.get(s["id"], (None, "prorata"))
        k = s.get("consumedKwh") or 0
        p = s.get("price") or 0
        t = tariff_at(_session_date(s))
        cst = (k * ratio * t["hc"] + k * (1 - ratio) * t["hp"]) if ratio is not None else k * t["hp"]
        sessions_light.append({
            "startedAt": s.get("startedAt"),
            "stoppedAt": s.get("stoppedAt"),
            "kwh": round(k, 3),
            "price": round(p, 2),
            "cost": round(s.get("cost") or 0, 2),
            "state": s.get("state"),
            "humanReadableId": s.get("humanReadableId"),
            "hc_ratio": round(ratio, 3) if ratio is not None else None,
            "hc_source": source,
            "cost_edf_estimated": round(cst, 2),
            "gain_estimated": round(p - cst, 2),
        })

    hc_sources = {s["hc_source"] for s in sessions_light}
    if not hc_sources or hc_sources == {"shelly"}:
        hc_source_global = "shelly"
    elif hc_sources == {"prorata"}:
        hc_source_global = "prorata"
    else:
        hc_source_global = "mixed"

    return {
        "month": f"{y:04d}-{m:02d}",
        "is_current_month": is_current_month,
        "sessions_count": len(sessions),
        "total_kwh": round(total_kwh, 3),
        "reimbursement": round(total_price, 2),
        "reimbursement_rate": MONTA_REIMB_RATE,
        "monta_cost_gross": round(total_cost, 2),
        "hc_kwh": round(hc_kwh, 3),
        "hp_kwh": round(hp_kwh, 3),
        "cost_edf_estimated": round(cost_est, 2),
        "gain_estimated": round(total_price - cost_est, 2),
        "cost_edf_best_case_all_hc": round(cost_best, 2),
        "cost_edf_worst_case_all_hp": round(cost_worst, 2),
        "gain_best_case": round(total_price - cost_best, 2),
        "gain_worst_case": round(total_price - cost_worst, 2),
        "last_session": last,
        "sessions": sessions_light,
        "hc_source": hc_source_global,
        "generated_at": int(time.time()),
    }


def _monta_cache_get_or_compute(key: str, is_current: bool):
    now = time.time()
    cached = _MONTA_SUMMARY.get(key)
    if cached and cached["expiresAt"] > now:
        return {**cached["data"], "cached": True}
    d = _monta_compute_summary(month=key)
    ttl = 300 if is_current else 3600
    _MONTA_SUMMARY[key] = {"data": d, "expiresAt": now + ttl}
    return {**d, "cached": False}


@app.get("/api/monta/summary")
def monta_summary(month: str | None = None):
    now_local = datetime.now().astimezone()
    if month:
        try:
            y, m = [int(x) for x in month.split("-")]
            datetime(y, m, 1)
        except Exception:
            return {"error": f"month invalide: {month} (attendu YYYY-MM)"}
        key = f"{y:04d}-{m:02d}"
    else:
        key = now_local.strftime("%Y-%m")
    is_current = (key == now_local.strftime("%Y-%m"))
    try:
        return _monta_cache_get_or_compute(key, is_current)
    except Exception as e:
        return {"error": str(e)}


@app.on_event("startup")
def _warmup_monta_cache():
    """Précharge le mois courant en arrière-plan pour que le premier hit
    utilisateur soit instantané (au lieu d'attendre le paging Monta + rate limit)."""
    import threading

    def _warm():
        try:
            now_local = datetime.now().astimezone()
            key = now_local.strftime("%Y-%m")
            _monta_cache_get_or_compute(key, is_current=True)
        except Exception:
            pass

    threading.Thread(target=_warm, daemon=True).start()


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html)


@app.get("/day/{date_str}", response_class=HTMLResponse)
def day_page(date_str: str):
    html = (STATIC_DIR / "day.html").read_text()
    return HTMLResponse(html)


@app.get("/monta", response_class=HTMLResponse)
def monta_page():
    html = (STATIC_DIR / "monta.html").read_text()
    return HTMLResponse(html)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
