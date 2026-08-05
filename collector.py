#!/usr/bin/env python3
"""
Collecteur Shelly EM — à lancer toutes les 15 minutes via systemd timer.
"""
import time
import requests
from datetime import datetime, timezone
import pytz
import logging

from config import SHELLY_IP, CALIBRATION_FACTOR
from db import init_db, insert_reading, upsert_daily_cost, get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Paris")


def fetch_shelly():
    url = f"http://{SHELLY_IP}/rpc/EM1.GetStatus?id=0"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    data = r.json()
    return {
        "power_w":   data.get("act_power", 0.0),
        "voltage":   data.get("voltage", 0.0),
        "current":   data.get("current", 0.0),
    }


def fetch_energy_total():
    """Retourne le total kWh du compteur (canal A)."""
    url = f"http://{SHELLY_IP}/rpc/EM1Data.GetStatus?id=0"
    r = requests.get(url, timeout=5)
    r.raise_for_status()
    data = r.json()
    return data.get("total_act_energy", 0.0)  # Wh


def recompute_today():
    """Recalcule les kWh HP/HC du jour courant à partir des lectures."""
    now = datetime.now(TZ)
    date_str = now.strftime("%Y-%m-%d")
    midnight = TZ.localize(datetime(now.year, now.month, now.day)).timestamp()

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, power_w, is_hc FROM readings WHERE ts >= ? ORDER BY ts ASC",
            (int(midnight),)
        ).fetchall()

    if len(rows) < 2:
        return

    kwh_hc = 0.0
    kwh_hp = 0.0
    for i in range(1, len(rows)):
        interval_h = (rows[i]["ts"] - rows[i-1]["ts"]) / 3600
        p = rows[i-1]["power_w"] / 1000 * interval_h * CALIBRATION_FACTOR
        if rows[i-1]["is_hc"]:
            kwh_hc += p
        else:
            kwh_hp += p
    upsert_daily_cost(date_str, kwh_hc, kwh_hp)
    log.info(f"Coût recalculé pour {date_str}: HC={kwh_hc:.3f}kWh HP={kwh_hp:.3f}kWh")


def main():
    init_db()
    ts = int(time.time())

    try:
        reading = fetch_shelly()
    except Exception as e:
        log.error(f"Erreur lecture Shelly: {e}")
        return

    try:
        energy_wh = fetch_energy_total()
    except Exception:
        energy_wh = 0.0

    hc = insert_reading(
        ts=ts,
        power_w=reading["power_w"],
        energy_wh=energy_wh,
        voltage=reading["voltage"],
        current=reading["current"],
    )

    log.info(
        f"{reading['power_w']:.0f}W  {reading['voltage']:.1f}V  "
        f"{'HC' if hc else 'HP'}  energy={energy_wh:.0f}Wh"
    )

    recompute_today()


if __name__ == "__main__":
    main()
