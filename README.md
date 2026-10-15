# 币安统一账户杠杆监控（FastAPI）

本地定时采集币安 Portfolio Margin 账户名义杠杆，写入 SQLite，并通过网页查看。

**杠杆口径**：`账户杠杆 = Σ|持仓名义价值| / 统一账户总权益`

## 启动

```bash
cd /root/crypto-scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json   # 编辑 API Key / 代理 / 间隔
python3 -m app.main
```

服务监听 `0.0.0.0:8000`（可远程访问）。本机打开 http://127.0.0.1:8000 ，局域网用 `http://<服务器IP>:8000`。

## Docker

```bash
# 构建
docker build -t pm-leverage:latest .

# 运行（挂载配置与数据；宿主机代理用 host.docker.internal）
docker run -d --name pm-leverage --restart unless-stopped \
  -p 8000:8000 \
  -v "$(pwd)/config.json:/app/config.json:ro" \
  -v "$(pwd)/data:/app/data" \
  --add-host=host.docker.internal:host-gateway \
  pm-leverage:latest
```

容器内代理可把 `binance.proxy` 设为 `http://host.docker.internal:7890`。

## 配置

`config.json`：

| 字段 | 说明 |
|------|------|
| `accounts[]` | 账户 `label` / `api_key` / `api_secret` |
| `binance.proxy` | 访问 `papi.binance.com` 时可填 HTTP 代理 |
| `server.interval_minutes` | 采集间隔（分钟） |
| `server.port` | HTTP 端口，默认 8000 |

数据文件：`data/pm_leverage.db`

## API

- `GET /` 监控页
- `GET /api/latest` 各账户最新快照
- `GET /api/history?label=&limit=` 历史
- `POST /api/collect` 立即采集一次
- `GET /api/health` 运行状态
