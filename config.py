SHELLY_IP = "192.168.1.83"

# Tarifs EDF HP/HC (€/kWh TTC, contrat 9kVA)
# Valeurs actuelles (utilisées par du code qui ne sait pas historiser).
# Pour un calcul historique correct, utiliser tariff_at(date_str).
TARIF_HP = 0.2142
TARIF_HC = 0.1589

# Abonnement mensuel TTC (valeur actuelle)
ABONNEMENT_MENSUEL = 19.88

# Historique des tarifs par date d'effet — la plus récente en tête.
# Chaque entrée s'applique à toute date >= "from" (jusqu'à la prochaine entrée).
TARIFF_HISTORY = [
    {"from": "2026-08-01", "hp": 0.2142, "hc": 0.1589, "abo": 19.88},
    {"from": "0000-01-01", "hp": 0.2065, "hc": 0.1579, "abo": 19.56},
]


def tariff_at(date_str: str):
    """Retourne le tarif en vigueur à une date donnée (YYYY-MM-DD).

    Retourne un dict {hp, hc, abo}.
    """
    for entry in TARIFF_HISTORY:
        if date_str >= entry["from"]:
            return entry
    return TARIFF_HISTORY[-1]

# Correction sous-comptage pince CT (reclipage 12/07/2026, moy -9.7% vs Linky)
CALIBRATION_FACTOR = 1.097

# Plages heures creuses [(heure_debut_min, heure_fin_min), ...]
# en minutes depuis minuit
HC_WINDOWS = [
    (14 * 60 + 26, 16 * 60 + 56),   # 14h26 – 16h56
    (23 * 60 + 56, 24 * 60 + 60),   # 23h56 – 00h00 (wrap nuit)
    (0,             5 * 60 + 26),    # 00h00 – 05h26
]

# Seuil alerte puissance (W)
ALERT_THRESHOLD_W = 7000

DB_PATH = "/home/openclaw/projects/energy-monitor/energy.db"
