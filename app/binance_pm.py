"""币安统一账户（Portfolio Margin）数据拉取与杠杆计算。"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

CST = timezone(timedelta(hours=8))


def curl_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    proxy: str | None = None,
    timeout: int = 25,
    retries: int = 4,
) -> Any:
    last_err = ""
    for i in range(retries):
        cmd = [
            "curl",
            "-sS",
            "-4",
            "--http1.1",
            "--max-time",
            str(timeout),
            "-w",
            "\n__HTTP__:%{http_code}",
        ]
        if proxy:
            cmd += ["-x", proxy]
        for k, v in (headers or {}).items():
            cmd += ["-H", f"{k}: {v}"]
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True)
        body = r.stdout or ""
        http = None
        if "__HTTP__:" in body:
            body, http = body.rsplit("__HTTP__:", 1)
            http = http.strip()
        if r.returncode == 0 and http not in (None, "000") and body.strip():
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                last_err = f"非 JSON 响应 http={http}: {body[:200]}"
            else:
                return data
            last_err = f"http={http} empty/invalid body"
        else:
            last_err = (r.stderr or "").strip() or f"curl={r.returncode} http={http}"
        time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"请求失败: {url} -> {last_err}")


def signed_get(
    base: str,
    path: str,
    api_key: str,
    api_secret: str,
    *,
    proxy: str | None = None,
    recv_window: int = 60000,
) -> Any:
    params = {
        "timestamp": int(time.time() * 1000),
        "recvWindow": recv_window,
    }
    qs = urllib.parse.urlencode(params)
    sig = hmac.new(api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    url = f"{base}{path}?{qs}&signature={sig}"
    return curl_json(url, headers={"X-MBX-APIKEY": api_key}, proxy=proxy)


def fetch_account_snapshot(
    api_key: str,
    api_secret: str,
    *,
    label: str,
    proxy: str | None,
    recv_window: int = 60000,
) -> dict[str, Any]:
    """拉取统一账户快照。杠杆 = Σ|名义价值| / 总权益。"""
    # api 偶发 TLS 失败时回退代理
    try:
        acct = signed_get(
            "https://api.binance.com",
            "/sapi/v1/portfolio/account",
            api_key,
            api_secret,
            proxy=None,
            recv_window=recv_window,
        )
    except Exception:
        acct = signed_get(
            "https://api.binance.com",
            "/sapi/v1/portfolio/account",
            api_key,
            api_secret,
            proxy=proxy,
            recv_window=recv_window,
        )
    if not isinstance(acct, dict) or "accountEquity" not in acct:
        raise RuntimeError(f"[{label}] portfolio/account 失败: {acct}")

    positions = signed_get(
        "https://papi.binance.com",
        "/papi/v1/um/positionRisk",
        api_key,
        api_secret,
        proxy=proxy,
        recv_window=recv_window,
    )
    if isinstance(positions, dict) and positions.get("code"):
        raise RuntimeError(f"[{label}] um/positionRisk 失败: {positions}")
    if not isinstance(positions, list):
        raise RuntimeError(f"[{label}] um/positionRisk 返回异常: {positions}")

    open_pos = [p for p in positions if abs(float(p.get("positionAmt") or 0)) > 0]
    long_notional = 0.0
    short_notional = 0.0
    upnl = 0.0
    for p in open_pos:
        n = float(p.get("notional") or 0)
        upnl += float(p.get("unRealizedProfit") or 0)
        if n >= 0:
            long_notional += abs(n)
        else:
            short_notional += abs(n)

    gross_notional = long_notional + short_notional
    equity = float(acct["accountEquity"])
    leverage = (gross_notional / equity) if equity > 0 else 0.0
    now = datetime.now(CST)

    return {
        "ts": now.isoformat(timespec="seconds"),
        "label": label,
        "equity": equity,
        "gross_notional": gross_notional,
        "long_notional": long_notional,
        "short_notional": short_notional,
        "upnl": upnl,
        "position_count": len(open_pos),
        "leverage": leverage,
        "maint_margin": float(acct.get("accountMaintMargin") or 0),
        "initial_margin": float(acct.get("accountInitialMargin") or 0),
        "uni_mmr": float(acct.get("uniMMR") or 0),
        "available": float(acct.get("totalAvailableBalance") or 0),
        "account_status": acct.get("accountStatus") or "",
        "account_type": acct.get("accountType") or "",
    }
