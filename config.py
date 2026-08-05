SHELLY_IP = "192.168.1.83"

# EDF HP/HC tariffs (€/kWh incl. tax, 9kVA contract)
# Current values (used by code that doesn't handle history).
# For correct historical computations, use tariff_at(date_str).
TARIF_HP = 0.2142
TARIF_HC = 0.1589

# Monthly subscription incl. tax (current value)
ABONNEMENT_MENSUEL = 19.88

# Tariff history by effective date — most recent first.
# Each entry applies to any date >= "from" (until the next entry).
TARIFF_HISTORY = [
    {"from": "2026-08-01", "hp": 0.2142, "hc": 0.1589, "abo": 19.88},
    {"from": "0000-01-01", "hp": 0.2065, "hc": 0.1579, "abo": 19.56},
]


def tariff_at(date_str: str):
    """Return the tariff in effect at a given date (YYYY-MM-DD).

    Returns a dict {hp, hc, abo}.
    """
    for entry in TARIFF_HISTORY:
        if date_str >= entry["from"]:
            return entry
    return TARIFF_HISTORY[-1]

# CT clamp under-count correction (re-clamped 2026-07-12, avg -9.7% vs Linky)
CALIBRATION_FACTOR = 1.097

# Off-peak (heures creuses) windows [(start_min, end_min), ...]
# in minutes since midnight
HC_WINDOWS = [
    (14 * 60 + 26, 16 * 60 + 56),   # 14:26 – 16:56
    (23 * 60 + 56, 24 * 60 + 60),   # 23:56 – 00:00 (overnight wrap)
    (0,             5 * 60 + 26),    # 00:00 – 05:26
]

DB_PATH = "/home/openclaw/projects/energy-monitor/energy.db"
