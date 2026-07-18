#!/usr/bin/env python3
"""
Importe l'historique du Shelly EM (données 1 minute) dans SQLite.
Format CSV: ts, e_a_wh, e_ret_a, e_ret_b, e_b_wh, power_a, power_b,
            aprt_a, aprt_b, volt_a, volt_b, volt_avg, curr_a, curr_b, curr_avg
"""
import requests
import sqlite3
from datetime import datetime
import pytz

from config import SHELLY_IP, DB_PATH, CALIBRATION_FACTOR
from db import init_db, is_hc, upsert_daily_cost, get_conn

TZ = pytz.timezone("Europe/Paris")

def fetch_csv():
    # Fetch all records from the main block (ts=1783609920)
    url = f"http://{SHELLY_IP}/em1data/0/data.csv?ts=1783609920&num_recs=9999"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.text.strip().splitlines()

def parse_row(line):
    parts = line.split(",")
    if len(parts) < 13:
        return None
    return {
        "ts":      int(parts[0]),
        "power_w": float(parts[5]),   # act_power canal A
        "voltage": float(parts[9]),   # voltage A
        "current": float(parts[12]),  # current A
        "energy_wh": float(parts[1]), # delta Wh this minute
    }

def main():
    init_db()
    print("Téléchargement historique Shelly EM...")
    lines = fetch_csv()
    print(f"{len(lines)} enregistrements trouvés")

    inserted = 0
    skipped = 0

    with get_conn() as conn:
        # Récupérer les ts déjà en base pour éviter les doublons
        existing = set(r[0] for r in conn.execute("SELECT ts FROM readings").fetchall())

        rows_to_insert = []
        for line in lines:
            row = parse_row(line)
            if not row:
                continue
            if row["ts"] in existing:
                skipped += 1
                continue
            hc = is_hc(row["ts"])
            rows_to_insert.append((
                row["ts"], row["power_w"], row["energy_wh"],
                row["voltage"], row["current"], int(hc)
            ))

        conn.executemany(
            "INSERT INTO readings (ts, power_w, energy_wh, voltage, current, is_hc) VALUES (?,?,?,?,?,?)",
            rows_to_insert
        )
        inserted = len(rows_to_insert)

    print(f"Importé : {inserted} | Déjà présents : {skipped}")

    # Recalculer les coûts journaliers pour chaque jour présent
    print("Recalcul des coûts journaliers...")
    with get_conn() as conn:
        dates = conn.execute(
            "SELECT DISTINCT date(ts, 'unixepoch', 'localtime') as d FROM readings ORDER BY d"
        ).fetchall()

    interval_h = 1 / 60  # 1 minute en heures
    for (date_str,) in dates:
        midnight = datetime.strptime(date_str, "%Y-%m-%d")
        midnight_ts = int(TZ.localize(midnight).timestamp())
        next_midnight_ts = midnight_ts + 86400

        with get_conn() as conn:
            rows = conn.execute(
                "SELECT power_w, is_hc FROM readings WHERE ts >= ? AND ts < ?",
                (midnight_ts, next_midnight_ts)
            ).fetchall()

        kwh_hc = sum(r["power_w"] / 1000 * interval_h * CALIBRATION_FACTOR for r in rows if r["is_hc"])
        kwh_hp = sum(r["power_w"] / 1000 * interval_h * CALIBRATION_FACTOR for r in rows if not r["is_hc"])
        upsert_daily_cost(date_str, kwh_hc, kwh_hp)
        print(f"  {date_str}: HC={kwh_hc:.3f}kWh HP={kwh_hp:.3f}kWh")

    print("Backfill terminé.")

if __name__ == "__main__":
    main()
