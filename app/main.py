"""FastAPI 入口：定时采集 + SQLite + 网页监控。"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.collector import Collector
from app.db import Database

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "pm_leverage.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("pm_leverage")

templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"缺少配置: {CONFIG_PATH}")
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


cfg = load_config()
binance_cfg = cfg.get("binance") or {}
server_cfg = cfg.get("server") or {}
interval_minutes = int(server_cfg.get("interval_minutes") or cfg.get("interval_minutes") or 5)
host = server_cfg.get("host") or "0.0.0.0"
port = int(server_cfg.get("port") or 8000)

db = Database(DB_PATH)
collector = Collector(
    db,
    cfg.get("accounts") or [],
    proxy=binance_cfg.get("proxy") or None,
    recv_window=int(binance_cfg.get("recv_window") or 60000),
)
scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    log.info("启动采集：每 %s 分钟一次", interval_minutes)
    # 启动立即采一次
    try:
        collector.collect_once()
    except Exception:
        log.exception("首次采集失败")
    scheduler.add_job(
        collector.collect_once,
        "interval",
        minutes=interval_minutes,
        id="pm_collect",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)
    log.info("调度器已停止")


app = FastAPI(title="币安统一账户杠杆监控", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    latest = db.latest_by_label()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "latest": latest,
            "interval_minutes": interval_minutes,
            "last_run": collector.last_run,
            "last_error": collector.last_error,
            "labels": db.labels() or [a.get("label") for a in (cfg.get("accounts") or [])],
        },
    )


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "interval_minutes": interval_minutes,
        "last_run": collector.last_run,
        "last_error": collector.last_error,
        "collecting": collector.running,
    }


@app.get("/api/latest")
async def api_latest():
    return db.latest_by_label()


@app.get("/api/history")
async def api_history(
    label: str | None = None,
    limit: int = Query(300, ge=1, le=5000),
):
    rows = db.history(label=label, limit=limit)
    rows.reverse()  # 时间正序，方便画图
    return rows


@app.get("/api/logs")
async def api_logs(limit: int = Query(20, ge=1, le=200)):
    return db.recent_logs(limit)


@app.post("/api/collect")
async def api_collect():
    try:
        snaps = collector.collect_once()
        return {"ok": True, "count": len(snaps), "snapshots": snaps, "error": collector.last_error}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def run():
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    run()
