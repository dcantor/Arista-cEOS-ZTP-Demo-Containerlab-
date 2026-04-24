import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("/data/ztp.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    host    TEXT    NOT NULL,
    event   TEXT    NOT NULL,
    ip      TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_host ON events(host);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

-- Devices the user has registered through the UI. Each row produces a
-- DHCP reservation in /dhcp-state/managed.conf (MAC -> IP -> bootfile
-- URL) so any device DHCPing in with that MAC gets a deterministic
-- mgmt IP and is pointed at /ztp/<name>.sh on the ZTP server.
CREATE TABLE IF NOT EXISTS managed_devices (
    name       TEXT PRIMARY KEY,
    mac        TEXT NOT NULL UNIQUE,
    mgmt_ip    TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def insert_event(host: str, event: str, ip: str | None) -> dict:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO events (host, event, ip) VALUES (?, ?, ?) RETURNING id, ts, host, event, ip",
            (host, event, ip),
        )
        row = cur.fetchone()
        return dict(row)


def list_events(limit: int = 200) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, ts, host, event, ip FROM events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_managed_devices() -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT name, mac, mgmt_ip, created_at FROM managed_devices ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


def insert_managed_device(name: str, mac: str, mgmt_ip: str) -> dict:
    with connect() as c:
        c.execute(
            "INSERT INTO managed_devices (name, mac, mgmt_ip) VALUES (?, ?, ?)",
            (name, mac, mgmt_ip),
        )
        row = c.execute(
            "SELECT name, mac, mgmt_ip, created_at FROM managed_devices WHERE name = ?",
            (name,),
        ).fetchone()
        return dict(row)


def delete_managed_device(name: str) -> bool:
    with connect() as c:
        cur = c.execute("DELETE FROM managed_devices WHERE name = ?", (name,))
        return cur.rowcount > 0


def host_summaries() -> list[dict]:
    with connect() as c:
        rows = c.execute(
            """
            SELECT
                host,
                MIN(ts) AS first_seen,
                MAX(ts) AS last_seen,
                COUNT(*) AS event_count,
                (SELECT event FROM events e2 WHERE e2.host = e1.host ORDER BY id DESC LIMIT 1) AS last_event
            FROM events e1
            GROUP BY host
            """
        ).fetchall()
        return [dict(r) for r in rows]
