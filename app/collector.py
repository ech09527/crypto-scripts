"""定时采集逻辑。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.binance_pm import fetch_account_snapshot
from app.db import Database

log = logging.getLogger("pm_leverage")
CST = timezone(timedelta(hours=8))


class Collector:
    def __init__(
        self,
        db: Database,
        accounts: list[dict[str, Any]],
        *,
        proxy: str | None,
        recv_window: int = 60000,
        on_done: Callable[[list[dict[str, Any]], str | None], None] | None = None,
    ):
        self.db = db
        self.accounts = accounts
        self.proxy = proxy
        self.recv_window = recv_window
        self.on_done = on_done
        self.last_run: str | None = None
        self.last_error: str | None = None
        self.running = False

    def collect_once(self) -> list[dict[str, Any]]:
        if self.running:
            return []
        self.running = True
        now = datetime.now(CST).isoformat(timespec="seconds")
        snapshots: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            for acc in self.accounts:
                label = acc.get("label") or "default"
                try:
                    snap = fetch_account_snapshot(
                        acc["api_key"],
                        acc["api_secret"],
                        label=label,
                        proxy=self.proxy,
                        recv_window=self.recv_window,
                    )
                    self.db.insert_snapshot(snap)
                    snapshots.append(snap)
                    log.info(
                        "[%s] leverage=%.4fx equity=%.2f notional=%.2f",
                        label,
                        snap["leverage"],
                        snap["equity"],
                        snap["gross_notional"],
                    )
                except Exception as e:
                    msg = f"[{label}] {e}"
                    errors.append(msg)
                    log.exception("采集失败 %s", label)
            if errors and not snapshots:
                self.last_error = "; ".join(errors)
                self.db.log_collect(now, False, self.last_error)
            elif errors:
                self.last_error = "; ".join(errors)
                self.db.log_collect(now, True, f"部分成功: {self.last_error}")
            else:
                self.last_error = None
                self.db.log_collect(
                    now,
                    True,
                    f"成功 {len(snapshots)} 个账户",
                )
            self.last_run = now
            if self.on_done:
                self.on_done(snapshots, self.last_error)
            return snapshots
        finally:
            self.running = False
