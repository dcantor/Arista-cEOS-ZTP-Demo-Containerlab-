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
