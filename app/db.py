"""SQLite 存储。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    label TEXT NOT NULL,
    equity REAL NOT NULL,
    gross_notional REAL NOT NULL,
    long_notional REAL NOT NULL,
    short_notional REAL NOT NULL,
    upnl REAL NOT NULL,
    position_count INTEGER NOT NULL,
    leverage REAL NOT NULL,
    maint_margin REAL NOT NULL,
    initial_margin REAL NOT NULL,
    uni_mmr REAL NOT NULL,
    available REAL NOT NULL,
    account_status TEXT,
    account_type TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_snapshots_label_ts ON snapshots(label, ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS collect_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ok INTEGER NOT NULL,
    message TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def insert_snapshot(self, snap: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots (
                    ts, label, equity, gross_notional, long_notional, short_notional,
                    upnl, position_count, leverage, maint_margin, initial_margin,
                    uni_mmr, available, account_status, account_type
                ) VALUES (
                    :ts, :label, :equity, :gross_notional, :long_notional, :short_notional,
                    :upnl, :position_count, :leverage, :maint_margin, :initial_margin,
                    :uni_mmr, :available, :account_status, :account_type
                )
                """,
                snap,
            )
            return int(cur.lastrowid)

    def log_collect(self, ts: str, ok: bool, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO collect_logs (ts, ok, message) VALUES (?, ?, ?)",
                (ts, 1 if ok else 0, message),
            )

    def latest_by_label(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.* FROM snapshots s
                INNER JOIN (
                    SELECT label, MAX(id) AS max_id FROM snapshots GROUP BY label
                ) t ON s.id = t.max_id
                ORDER BY s.label
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def history(
        self,
        *,
        label: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        with self.connect() as conn:
            if label:
                rows = conn.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE label = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (label, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM snapshots
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def labels(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT label FROM snapshots ORDER BY label"
            ).fetchall()
            return [r["label"] for r in rows]

    def recent_logs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM collect_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
