SHELLY_IP = "192.168.1.83"

# Tarifs EDF HP/HC (€/kWh TTC, contrat 9kVA vérifié juillet 2026)
TARIF_HP = 0.2065
TARIF_HC = 0.1579

# Abonnement mensuel TTC
ABONNEMENT_MENSUEL = 19.56

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
