# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Moniteur d'énergie domestique : un Shelly EM Gen3 (pince CT sur le réseau local, `SHELLY_IP` dans `config.py`) alimente une base SQLite, exposée par un dashboard FastAPI. Code et UI en français.

## Commandes

Pas de tests ni de linter. Tout passe par le venv du projet :

```bash
venv/bin/python3 server.py                          # serveur dev sur 0.0.0.0:8000
venv/bin/python3 collector.py                       # un relevé Shelly (one-shot)
venv/bin/python3 cloud_sync.py --days 7 [--notify]  # re-sync des jours depuis Shelly Cloud
```

En production, trois mécanismes tournent déjà sur cette machine :
- `energy-server.service` (systemd) — le serveur FastAPI, `Restart=always`. Après modif de `server.py` : `sudo systemctl restart energy-server` ; logs via `journalctl -u energy-server`.
- `energy-collector.timer` (systemd) — lance `collector.py` toutes les 15 min.
- cron à 6h00 — `cloud_sync.py --days 2 --notify` (récap Telegram), sortie dans `sync.log`.

## Architecture

Deux chemins d'écriture alimentent `energy.db` (tables `readings` et `daily_costs`) :

1. **`collector.py`** (toutes les 15 min) : lit la puissance instantanée du Shelly en RPC local, insère un point dans `readings`, puis ré-estime les kWh HP/HC du jour courant par intégration des points et upsert dans `daily_costs`. C'est une **estimation intraday**.
2. **`cloud_sync.py`** (quotidien) : récupère les consommations journalières agrégées depuis l'API Shelly Cloud et **écrase** `daily_costs` avec ces valeurs, qui font autorité. La clé API Shelly Cloud est récupérée via un `subprocess` (voir `get_api_key`).

`server.py` ne fait que lire la base + proxifier du live : SSE `/api/live` (poll du Shelly toutes les 5 s), API REST (`/api/today`, `/api/history`, `/api/stats`, `/api/day/{date}`), et intégration **Monta** (bornes de recharge VE) avec token OAuth et cache mémoire préchauffé au démarrage. Le frontend (`static/`) est du JS vanilla + Chart.js vendorisé, en PWA (manifest + icônes) ; pages `index.html`, `day.html`, `monta.html`.

## Points à connaître

- **Tarifs** : pour tout calcul de coût sur une date passée, utiliser `tariff_at(date_str)` (`config.py`), qui lit `TARIFF_HISTORY`. `TARIF_HP`/`TARIF_HC`/`ABONNEMENT_MENSUEL` sont les valeurs *actuelles* seulement — ne pas les utiliser pour de l'historique.
- **`CALIBRATION_FACTOR`** corrige un sous-comptage de la pince CT (mesuré vs Linky) ; il est appliqué à l'intégration des `readings`, pas aux données Shelly Cloud.
- **Heures creuses** : plages non standard définies dans `HC_WINDOWS` (14h26–16h56 et 23h56–5h26, Europe/Paris). Elles sont dupliquées en dur côté Monta dans `server.py` (`MONTA_HC_WINDOWS_PARIS`) — garder les deux synchronisées.
- **Secrets** : `MONTA_CLIENT_ID`/`MONTA_CLIENT_SECRET` vivent dans `.env.local` (non versionné), chargé par un loader maison dans `server.py`. Le token du bot Telegram est lu depuis un fichier hors repo (chemin dans `cloud_sync.py`).
- `DB_PATH` est un chemin absolu dans `config.py` — les scripts supposent qu'ils tournent sur cette machine.
- `backfill.py` est un script one-shot d'import de l'historique CSV du Shelly ; ne sert plus en routine.
