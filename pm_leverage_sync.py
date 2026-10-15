#!/usr/bin/env python3
"""计算币安统一账户（Portfolio Margin）名义杠杆，并追加同步到腾讯文档表格。

杠杆定义：账户杠杆 ≈ Σ|持仓名义价值| / 统一账户总权益
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import hmac
import hashlib
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
CST = timezone(timedelta(hours=8))

HEADER = [
    "时间",
    "账户标签",
    "总权益",
    "名义价值合计",
    "多仓名义",
    "空仓名义",
    "未实现盈亏",
    "仓位数",
    "账户杠杆",
    "维持保证金",
    "初始保证金",
    "uniMMR",
    "可用余额",
]


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"缺少配置文件: {path}（可参考 config.example.json）")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


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
                if isinstance(data, dict) and data.get("code") not in (None, 0, "0"):
                    # 业务错误也直接返回，便于上层处理
                    return data
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
    recv_window: int,
) -> dict[str, Any]:
    # 账户级权益走 api.binance.com（更稳）
    acct = signed_get(
        "https://api.binance.com",
        "/sapi/v1/portfolio/account",
        api_key,
        api_secret,
        proxy=None,
        recv_window=recv_window,
    )
    if not isinstance(acct, dict) or "accountEquity" not in acct:
        raise RuntimeError(f"[{label}] portfolio/account 失败: {acct}")

    # 持仓走 papi（统一账户）；需要时可走代理
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

    return {
        "time": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
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
        "account_status": acct.get("accountStatus"),
        "account_type": acct.get("accountType"),
    }


def snapshot_to_row(s: dict[str, Any]) -> list[str]:
    return [
        s["time"],
        s["label"],
        f'{s["equity"]:.4f}',
        f'{s["gross_notional"]:.4f}',
        f'{s["long_notional"]:.4f}',
        f'{s["short_notional"]:.4f}',
        f'{s["upnl"]:.4f}',
        str(s["position_count"]),
        f'{s["leverage"]:.4f}',
        f'{s["maint_margin"]:.4f}',
        f'{s["initial_margin"]:.4f}',
        f'{s["uni_mmr"]:.6f}',
        f'{s["available"]:.4f}',
    ]


def rows_to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().rstrip("\n")


def ensure_mcporter_token(token: str) -> None:
    """确保 mcporter 四个腾讯文档服务使用最新 Token。"""
    services = [
        ("tencent-docs", "https://docs.qq.com/openapi/mcp"),
        ("slide-mcp", "https://docs.qq.com/api/v6/slide/mcp"),
        ("doc-mcp", "https://docs.qq.com/api/v6/doc/mcp"),
        ("sheet-mcp", "https://docs.qq.com/api/v6/sheet/mcp"),
    ]
    for name, url in services:
        subprocess.run(
            [
                "mcporter",
                "config",
                "add",
                name,
                url,
                "--header",
                f"Authorization={token}",
                "--transport",
                "http",
                "--scope",
                "home",
            ],
            check=True,
            capture_output=True,
            text=True,
        )


def mcporter_call(server: str, tool: str, args: dict[str, Any]) -> Any:
    cmd = [
        "mcporter",
        "call",
        server,
        tool,
        "--args",
        json.dumps(args, ensure_ascii=False),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"mcporter {server}.{tool} 失败:\n{r.stderr or r.stdout}")
    out = (r.stdout or "").strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def find_next_row(file_id: str, sheet_id: str) -> int:
    """扫描 A 列，返回下一个可写行（0-based）。"""
    data = mcporter_call(
        "sheet-mcp",
        "get_cell_data",
        {
            "file_id": file_id,
            "sheet_id": sheet_id,
            "start_row": 0,
            "start_col": 0,
            "end_row": 500,
            "end_col": 0,
        },
    )
    max_row = -1
    for cell in data.get("cells") or []:
        val = (cell.get("string_value") or "").strip()
        if val or abs(float(cell.get("number_value") or 0)) > 0:
            max_row = max(max_row, int(cell.get("row") or 0))
    return max_row + 1


def sync_to_tencent(
    snapshots: list[dict[str, Any]],
    td: dict[str, Any],
) -> int:
    token = td.get("token") or os.environ.get("TENCENT_DOCS_TOKEN")
    if not token:
        raise SystemExit("缺少腾讯文档 token（config.tencent_docs.token 或环境变量 TENCENT_DOCS_TOKEN）")

    file_id = td["file_id"]
    sheet_id = td["sheet_id"]
    ensure_mcporter_token(token)

    next_row = find_next_row(file_id, sheet_id)
    if next_row == 0:
        rows = [HEADER] + [snapshot_to_row(s) for s in snapshots]
        write_row = 0
    else:
        rows = [snapshot_to_row(s) for s in snapshots]
        write_row = next_row

    csv_data = rows_to_csv(rows)
    mcporter_call(
        "sheet-mcp",
        "set_range_value_by_csv",
        {
            "file_id": file_id,
            "sheet_id": sheet_id,
            "csv_data": csv_data,
            "start_col": 0,
            "start_row": write_row,
        },
    )
    return write_row


def print_snapshot(s: dict[str, Any]) -> None:
    print(
        f'[{s["label"]}] 权益={s["equity"]:.2f}  '
        f'名义={s["gross_notional"]:.2f}  '
        f'杠杆={s["leverage"]:.4f}x  '
        f'仓位={s["position_count"]}  '
        f'浮盈={s["upnl"]:.2f}  '
        f'uniMMR={s["uni_mmr"]:.4f}'
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="币安统一账户杠杆计算并同步腾讯文档")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--no-sync", action="store_true", help="只计算，不同步腾讯文档")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    binance_cfg = cfg.get("binance") or {}
    proxy = binance_cfg.get("proxy") or None
    recv_window = int(binance_cfg.get("recv_window") or 60000)
    accounts = cfg.get("accounts") or []
    if not accounts:
        raise SystemExit("config.accounts 为空")

    snapshots: list[dict[str, Any]] = []
    for acc in accounts:
        snap = fetch_account_snapshot(
            acc["api_key"],
            acc["api_secret"],
            label=acc.get("label") or "default",
            proxy=proxy,
            recv_window=recv_window,
        )
        snapshots.append(snap)
        if not args.json:
            print_snapshot(snap)

    if args.json:
        print(json.dumps(snapshots, ensure_ascii=False, indent=2))

    if not args.no_sync:
        td = cfg.get("tencent_docs") or {}
        start_row = sync_to_tencent(snapshots, td)
        if not args.json:
            print(
                f"已同步到腾讯文档: {td.get('file_url') or td.get('file_id')} "
                f"(起始行 {start_row}, sheet_id={td.get('sheet_id')})"
            )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
