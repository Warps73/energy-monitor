import sqlite3
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS readings (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,  -- unix timestamp
                power_w   REAL NOT NULL,
                energy_wh REAL,
                voltage   REAL,
                current   REAL,
                is_hc     INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_readings_ts ON readings(ts);

            CREATE TABLE IF NOT EXISTS daily_costs (
                date       TEXT PRIMARY KEY,  -- YYYY-MM-DD
                kwh_hc     REAL NOT NULL DEFAULT 0,
                kwh_hp     REAL NOT NULL DEFAULT 0,
                cost_hc    REAL NOT NULL DEFAULT 0,
                cost_hp    REAL NOT NULL DEFAULT 0,
                total_cost REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        INTEGER NOT NULL,
                type      TEXT NOT NULL,
                value_w   REAL,
                message   TEXT,
                ack       INTEGER NOT NULL DEFAULT 0
            );
        """)


def is_hc(ts_seconds: int) -> bool:
    """Return True if timestamp falls in heures creuses."""
    from datetime import datetime, timezone
    import pytz
    tz = pytz.timezone("Europe/Paris")
    dt = datetime.fromtimestamp(ts_seconds, tz=tz)
    minutes = dt.hour * 60 + dt.minute
    from config import HC_WINDOWS
    for start, end in HC_WINDOWS:
        if start <= minutes < end:
            return True
    return False


def insert_reading(ts: int, power_w: float, energy_wh: float, voltage: float, current: float):
    hc = is_hc(ts)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO readings (ts, power_w, energy_wh, voltage, current, is_hc) VALUES (?,?,?,?,?,?)",
            (ts, power_w, energy_wh, voltage, current, int(hc))
        )
    return hc


def upsert_daily_cost(date_str: str, kwh_hc: float, kwh_hp: float):
    from config import TARIF_HC, TARIF_HP
    cost_hc = kwh_hc * TARIF_HC
    cost_hp = kwh_hp * TARIF_HP
    total = cost_hc + cost_hp
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO daily_costs (date, kwh_hc, kwh_hp, cost_hc, cost_hp, total_cost)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                kwh_hc=excluded.kwh_hc, kwh_hp=excluded.kwh_hp,
                cost_hc=excluded.cost_hc, cost_hp=excluded.cost_hp,
                total_cost=excluded.total_cost
        """, (date_str, kwh_hc, kwh_hp, cost_hc, cost_hp, total))


def insert_alert(ts: int, type_: str, value_w: float, message: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO alerts (ts, type, value_w, message) VALUES (?,?,?,?)",
            (ts, type_, value_w, message)
        )
