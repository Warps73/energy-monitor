#!/usr/bin/env python3
"""
Sync les données journalières depuis la Shelly Cloud API vers daily_costs.
Utilise les compteurs Wh internes (précis) plutôt que les mesures 15min locales.
"""
import subprocess
import requests
import sys
from datetime import datetime, timedelta
import pytz

from config import CALIBRATION_FACTOR, HC_WINDOWS, tariff_at
from db import init_db, upsert_daily_cost

SERVER_URL = "https://shelly-66-eu.shelly.cloud"
DEVICE_ID  = "d885ac0ae7b4"
EMAIL      = "titi_du_73@hotmail.fr"
TZ         = pytz.timezone("Europe/Paris")

# Notification Telegram directe (récap autonome, sans passer par l'IA)
TG_TOKEN_FILE = "/home/openclaw/.openclaw/secrets/telegram-default.token"
TG_CHAT_ID    = "8622835280"


def get_api_key() -> str:
    result = subprocess.run(
        ["secret-tool", "lookup", "service", "shelly-apikey", "username", EMAIL],
        capture_output=True, text=True
    )
    key = result.stdout.strip()
    if not key:
        sys.exit("Clé API Shelly introuvable — secret-tool lookup a échoué.")
    return key


def hc_fraction(hour: int) -> float:
    """Fraction de l'heure 'hour' (0-23) qui tombe en heures creuses."""
    start_min = hour * 60
    end_min   = start_min + 60
    hc_min = 0.0
    for (ws, we) in HC_WINDOWS:
        overlap_start = max(start_min, ws)
        overlap_end   = min(end_min, we)
        if overlap_end > overlap_start:
            hc_min += overlap_end - overlap_start
    return hc_min / 60.0


def fetch_day(api_key: str, date_str: str) -> list:
    """Retourne la liste des Wh horaires pour une journée (heure locale Paris)."""
    params = {
        "auth_key":   api_key,
        "date_range": "day",
        "date_from":  date_str,
        "device_id":  DEVICE_ID,
    }
    r = requests.get(f"{SERVER_URL}/v2/statistics/power-consumption/overall",
                     params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    history = data.get("history", [])
    return [h for h in history if h.get("consumption") is not None]


def sync_day(api_key: str, date_str: str) -> tuple:
    rows = fetch_day(api_key, date_str)
    if not rows:
        return 0.0, 0.0

    kwh_hc = 0.0
    kwh_hp = 0.0
    for h in rows:
        # datetime format: "2026-07-13 14:00:00"
        hour = int(h["datetime"][11:13])
        wh   = h["consumption"] * CALIBRATION_FACTOR
        frac = hc_fraction(hour)
        kwh_hc += wh * frac / 1000
        kwh_hp += wh * (1 - frac) / 1000

    return kwh_hc, kwh_hp


def _fr(x: float, dec: int = 3) -> str:
    """Formate un nombre à la française (virgule décimale)."""
    return f"{x:.{dec}f}".replace(".", ",")


def build_recap(results: list) -> str:
    """Construit le récap texte déterministe (pas d'IA)."""
    lines = ["🔋 Sync énergie (Shelly Cloud)", ""]
    for date_str, kwh_hc, kwh_hp, total, cost in results:
        d = f"{date_str[8:10]}/{date_str[5:7]}"
        lines.append(
            f"• {d} : {_fr(total)} kWh "
            f"(HC {_fr(kwh_hc)} · HP {_fr(kwh_hp)}) → {_fr(cost, 2)} €"
        )
    lines.append("")
    lines.append(f"✅ {len(results)} jours mis à jour dans le dashboard.")
    return "\n".join(lines)


def send_telegram(text: str) -> None:
    """Poste le récap directement via l'API bot Telegram (0 token IA)."""
    try:
        with open(TG_TOKEN_FILE) as f:
            token = f.read().strip()
    except OSError as e:
        print(f"Notif Telegram ignorée — token illisible : {e}")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text,
                  "disable_notification": True},
            timeout=15,
        )
        r.raise_for_status()
        print("Récap Telegram envoyé.")
    except Exception as e:
        print(f"Échec envoi Telegram : {e}")


def main(days: int = 30, notify: bool = False):
    init_db()
    api_key = get_api_key()

    today = datetime.now(TZ).date()
    results = []

    for i in range(days, -1, -1):
        date = today - timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        try:
            kwh_hc, kwh_hp = sync_day(api_key, date_str)
            upsert_daily_cost(date_str, kwh_hc, kwh_hp)
            total = kwh_hc + kwh_hp
            t = tariff_at(date_str)
            cost  = kwh_hc * t["hc"] + kwh_hp * t["hp"]
            print(f"{date_str}: HC={kwh_hc:.3f} HP={kwh_hp:.3f} total={total:.3f} kWh → {cost:.2f}€")
            results.append((date_str, kwh_hc, kwh_hp, total, cost))
        except Exception as e:
            print(f"{date_str}: ERREUR — {e}")

    print(f"\nSync terminé : {len(results)} jours mis à jour.")

    if notify and results:
        send_telegram(build_recap(results))


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30, help="Nb jours à syncer (défaut: 30)")
    p.add_argument("--notify", action="store_true",
                   help="Poste un récap sur Telegram en fin de sync")
    args = p.parse_args()
    main(args.days, args.notify)
