"""Database storage for tracked flights and every status check.

Works with two databases through one small wrapper:
  Postgres (Neon) when DATABASE_URL is set   used in the cloud
  SQLite (a local file) otherwise            handy for quick local testing

The SQL is written once. The only dialect differences handled here are the
placeholder style (? vs %s) and the auto increment syntax in the schema.

Tables:
  tracked_flights  one row per flight, including WHEN it should next be checked
  snapshots        one row per status check, a full history of every change
"""

import json
import sqlite3
from datetime import datetime, timezone

from app.config import settings

COLUMNS = """
    flight_iata    TEXT NOT NULL,
    flight_date    TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    notify         INTEGER NOT NULL DEFAULT 1,
    error_count    INTEGER NOT NULL DEFAULT 0,
    next_check_at  TEXT,
    created_at     TEXT NOT NULL
"""

SNAPSHOT_COLUMNS = """
    checked_at     TEXT NOT NULL,
    status         TEXT,
    dep_delay      INTEGER,
    dep_gate       TEXT,
    dep_terminal   TEXT,
    dep_scheduled  TEXT,
    dep_estimated  TEXT,
    arr_gate       TEXT,
    arr_estimated  TEXT,
    raw_json       TEXT
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_tracked_due ON tracked_flights (active, next_check_at)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_tracked ON snapshots (tracked_id, id)",
]

SCHEMA = {
    "sqlite": [
        f"CREATE TABLE IF NOT EXISTS tracked_flights (id INTEGER PRIMARY KEY AUTOINCREMENT, {COLUMNS})",
        f"CREATE TABLE IF NOT EXISTS snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        f"tracked_id INTEGER NOT NULL REFERENCES tracked_flights(id), {SNAPSHOT_COLUMNS})",
    ],
    "postgres": [
        f"CREATE TABLE IF NOT EXISTS tracked_flights (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, {COLUMNS})",
        f"CREATE TABLE IF NOT EXISTS snapshots (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
        f"tracked_id BIGINT NOT NULL REFERENCES tracked_flights(id), {SNAPSHOT_COLUMNS})",
    ],
}

SNAPSHOT_FIELDS = (
    "status", "dep_delay", "dep_gate", "dep_terminal", "dep_scheduled",
    "dep_estimated", "arr_gate", "arr_estimated",
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Timestamps are stored as ISO text in UTC, so they sort and compare correctly as text."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, url: str | None = None, sqlite_path: str | None = None):
        url = settings.database_url if url is None else url
        if url:
            import psycopg
            from psycopg.rows import dict_row

            self.kind = "postgres"
            self.conn = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        else:
            self.kind = "sqlite"
            self.conn = sqlite3.connect(sqlite_path or settings.db_path)
            self.conn.row_factory = sqlite3.Row
        for stmt in SCHEMA[self.kind] + INDEXES:
            self.conn.execute(stmt)
        self._migrate_old_sqlite()
        self._commit()

    def _commit(self):
        if self.kind == "sqlite":
            self.conn.commit()

    def _migrate_old_sqlite(self):
        """Add columns that older local databases (from the first tracker version) are missing."""
        if self.kind != "sqlite":
            return
        existing = {r[1] for r in self.conn.execute("PRAGMA table_info(tracked_flights)")}
        for col, ddl in (("notify", "INTEGER NOT NULL DEFAULT 1"),
                         ("error_count", "INTEGER NOT NULL DEFAULT 0"),
                         ("next_check_at", "TEXT")):
            if col not in existing:
                self.conn.execute(f"ALTER TABLE tracked_flights ADD COLUMN {col} {ddl}")

    def run(self, sql: str, params: tuple = (), fetch: str | None = None):
        """Run one statement. fetch: None, 'one' (dict or None), or 'all' (list of dicts)."""
        if self.kind == "postgres":
            sql = sql.replace("?", "%s")
        cur = self.conn.execute(sql, params)
        result = None
        if fetch == "one":
            row = cur.fetchone()
            result = dict(row) if row is not None else None
        elif fetch == "all":
            result = [dict(r) for r in cur.fetchall()]
        self._commit()
        return result

    def close(self):
        self.conn.close()


def connect(url: str | None = None, sqlite_path: str | None = None) -> DB:
    return DB(url, sqlite_path)


# ---------- tracked flights ----------

def add_tracked_flight(db: DB, flight_iata: str, flight_date: str | None = None, notify: bool = True,
                       now: datetime | None = None) -> int:
    now = now or utc_now()
    row = db.run(
        "INSERT INTO tracked_flights (flight_iata, flight_date, notify, next_check_at, created_at) "
        "VALUES (?, ?, ?, ?, ?) RETURNING id",
        (flight_iata, flight_date, int(notify), iso(now), iso(now)),
        fetch="one",
    )
    return row["id"]


def get_tracked(db: DB, tracked_id: int) -> dict | None:
    return db.run("SELECT * FROM tracked_flights WHERE id = ?", (tracked_id,), fetch="one")


def active_flights(db: DB) -> list[dict]:
    return db.run("SELECT * FROM tracked_flights WHERE active = 1 ORDER BY next_check_at", fetch="all")


def due_flights(db: DB, now: datetime) -> list[dict]:
    return db.run(
        "SELECT * FROM tracked_flights WHERE active = 1 AND next_check_at <= ? ORDER BY next_check_at",
        (iso(now),), fetch="all",
    )


def next_due_time(db: DB) -> datetime | None:
    row = db.run("SELECT MIN(next_check_at) AS t FROM tracked_flights WHERE active = 1", fetch="one")
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None


def set_next_check(db: DB, tracked_id: int, when: datetime) -> None:
    db.run("UPDATE tracked_flights SET next_check_at = ? WHERE id = ?", (iso(when), tracked_id))


def set_flight_date(db: DB, tracked_id: int, flight_date: str) -> None:
    db.run("UPDATE tracked_flights SET flight_date = ? WHERE id = ?", (flight_date, tracked_id))


def record_error(db: DB, tracked_id: int) -> int:
    row = db.run(
        "UPDATE tracked_flights SET error_count = error_count + 1 WHERE id = ? RETURNING error_count",
        (tracked_id,), fetch="one",
    )
    return row["error_count"]


def reset_errors(db: DB, tracked_id: int) -> None:
    db.run("UPDATE tracked_flights SET error_count = 0 WHERE id = ?", (tracked_id,))


def deactivate(db: DB, tracked_id: int) -> None:
    db.run("UPDATE tracked_flights SET active = 0 WHERE id = ?", (tracked_id,))


def stop_flight(db: DB, flight_iata: str) -> int:
    rows = db.run(
        "UPDATE tracked_flights SET active = 0 WHERE flight_iata = ? AND active = 1 RETURNING id",
        (flight_iata,), fetch="all",
    )
    return len(rows)


# ---------- snapshots ----------

def save_snapshot(db: DB, tracked_id: int, snap: dict, raw: dict) -> None:
    cols = ", ".join(SNAPSHOT_FIELDS)
    marks = ", ".join("?" for _ in SNAPSHOT_FIELDS)
    db.run(
        f"INSERT INTO snapshots (tracked_id, checked_at, {cols}, raw_json) VALUES (?, ?, {marks}, ?)",
        (tracked_id, iso(utc_now()), *[snap.get(f) for f in SNAPSHOT_FIELDS], json.dumps(raw)),
    )


def latest_snapshot(db: DB, tracked_id: int) -> dict | None:
    row = db.run(
        "SELECT * FROM snapshots WHERE tracked_id = ? ORDER BY id DESC LIMIT 1", (tracked_id,), fetch="one"
    )
    return {f: row[f] for f in SNAPSHOT_FIELDS} if row else None


def count_snapshots(db: DB, tracked_id: int) -> int:
    row = db.run("SELECT COUNT(*) AS n FROM snapshots WHERE tracked_id = ?", (tracked_id,), fetch="one")
    return row["n"]
