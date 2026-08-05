# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Home energy monitor: a Shelly EM Gen3 (CT clamp, on the local network at `SHELLY_IP` in `config.py`) feeds a SQLite database, exposed through a FastAPI dashboard. Code is in English; user-facing text (dashboard UI, Telegram recap) is in French — keep it that way.

## Commands

No tests or linter. Everything runs through the project venv:

```bash
venv/bin/python3 server.py                          # dev server on 0.0.0.0:8000
venv/bin/python3 collector.py                       # one Shelly reading (one-shot)
venv/bin/python3 cloud_sync.py --days 7 [--notify]  # re-sync days from Shelly Cloud
```

In production, three mechanisms already run on this machine:
- `energy-server.service` (systemd) — the FastAPI server, `Restart=always`. After changing `server.py`: `sudo systemctl restart energy-server`; logs via `journalctl -u energy-server`.
- `energy-collector.timer` (systemd) — runs `collector.py` every 15 min.
- cron at 6:00 — `cloud_sync.py --days 2 --notify` (Telegram recap), output in `sync.log`.

## Architecture

Two write paths feed `energy.db` (tables `readings` and `daily_costs`):

1. **`collector.py`** (every 15 min): reads instantaneous power from the Shelly over local RPC, inserts a point into `readings`, then re-estimates today's HP/HC kWh by integrating the points and upserts into `daily_costs`. This is an **intraday estimate**.
2. **`cloud_sync.py`** (daily): fetches aggregated daily consumption from the Shelly Cloud API and **overwrites** `daily_costs` with those values, which are authoritative. The Shelly Cloud API key is retrieved via a `subprocess` call (see `get_api_key`).

`server.py` only reads the database and proxies live data: SSE `/api/live` (polls the Shelly every 5 s), REST API (`/api/today`, `/api/history`, `/api/stats`, `/api/day/{date}`), and the **Monta** integration (EV charge points) with an OAuth token and an in-memory cache warmed at startup. The frontend (`static/`) is vanilla JS + vendored Chart.js, as a PWA (manifest + icons); pages `index.html`, `day.html`, `monta.html`.

## Things to know

- **Tariffs**: for any cost computation on a past date, use `tariff_at(date_str)` (`config.py`), which reads `TARIFF_HISTORY`. `TARIF_HP`/`TARIF_HC`/`ABONNEMENT_MENSUEL` are the *current* values only — never use them for historical data.
- **API JSON keys are French** (`abonnement`, `total_avec_abo`, `abonnement_prorata`, …) and are a contract with the frontend — do not rename them.
- **`CALIBRATION_FACTOR`** corrects a CT clamp under-count (measured against the Linky meter); it applies to the integration of `readings`, not to Shelly Cloud data.
- **Off-peak hours (heures creuses)**: non-standard windows defined in `HC_WINDOWS` (14:26–16:56 and 23:56–05:26, Europe/Paris). They are duplicated in the Monta code in `server.py` (`MONTA_HC_WINDOWS_PARIS`) — keep both in sync.
- **Secrets**: `MONTA_CLIENT_ID`/`MONTA_CLIENT_SECRET` live in `.env.local` (not versioned), loaded by a homegrown loader in `server.py`. The Telegram bot token is read from a file outside the repo (path in `cloud_sync.py`).
- `DB_PATH` is an absolute path in `config.py` — the scripts assume they run on this machine.
- `backfill.py` is a one-shot import script for the Shelly CSV history; not used routinely.
