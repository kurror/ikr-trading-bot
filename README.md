# IKR Trading Bot

AI-driven options trading system for Interactive Brokers. Runs headlessly on Oracle Cloud free tier, sends AI-analysed trade approval requests to your Android phone, and waits for your tap before submitting any order.

> **Supports both paper and live trading.** The system is designed to run against IBKR's simulated (paper) environment during development and validation, and against a live account once the strategy has been proven. A configurable budget cap (`MAX_PREMIUM_BUDGET`) limits total open premium exposure in both modes. Only buy-side options positions are taken — risk is always defined and capped at premium paid.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Infrastructure](#infrastructure)
- [Services](#services)
- [Configuration](#configuration)
- [Usage](#usage)
- [Development](#development)
- [Known Issues](#known-issues)
- [Roadmap](#roadmap)

---

## Features

- **Headless IB Gateway** via Docker (paper or live account, auto-login via IBC)
- **Long call strategy** — finds ATM/slightly OTM call with ~30 DTE daily at market open
- **yfinance option chain** — free option data (strikes, expiries, bid/ask mid-price); no IB data subscription needed
- **Yahoo Finance price fallback** — spot price from Yahoo if IB delayed data fails
- **Multi-source sentiment** via Tor (bypasses OCI IP block):
  - WSB Due Diligence posts (`flair:DD`)
  - r/options recent posts
  - WSB general search
  - Google News RSS fallback
- **Groq** `llama-3.3-70b` structured analysis (SENTIMENT / RISK / CATALYST / VERDICT)
- **HTML trade report** — hosted on webhook server, shows full analysis with clickable source links
- **ntfy push notification** to Android with three action buttons:
  - `View Report` — opens full HTML report in browser
  - `Approve` — submits the order (paper or live)
  - `Reject` — skips the iteration
- First decision wins — duplicate taps are silently ignored server-side
- Hard budget cap (`MAX_PREMIUM_BUDGET = $2,000` total open premium)
- No naked exposure — bot only ever buys calls (defined risk = premium paid)

---

## Architecture

```
Android (Termux / ntfy app)
  │
  ├── SSH tunnel ──────────────► OCI instance-main (158.180.57.245)
  │                                   │
  └── ntfy push ◄────────────────────┤
                                      ├── IB Gateway  :4004 (localhost only)
                                      ├── Lumibot     (Docker)
                                      ├── Webhook     :8080
                                      ├── ntfy        :7777
                                      └── Tor         :9050 (SOCKS5)
```

**Trade flow:**

```
Market open (09:30 ET)
  └─ Lumibot on_trading_iteration()
      └─ _spot_price()           IB delayed → Yahoo Finance fallback
      └─ _pick_call()            yfinance option chain → nearest expiry ≥ 30d, first OTM strike
      └─ budget + position check
      └─ get_market_sentiment()
          ├─ WSB DD posts via Tor      (renews circuit on 429, retries)
          ├─ r/options posts via Tor
          ├─ WSB general via Tor
          └─ Google News RSS fallback
      └─ groq_analyse()          4-section structured report
      └─ POST /report/{order_id} store HTML report on webhook
      └─ ntfy notification → phone
          └─ [View Report]  [Approve]  [Reject]
              └─ first tap wins, second tap silently ignored
                  └─ Lumibot submits or skips order
```

---

## Prerequisites

- Oracle Cloud account (free tier)
- Interactive Brokers account (paper for development, live for production)
- Android phone with [ntfy app](https://ntfy.sh)
- [Groq API key](https://console.groq.com) (free tier)
- Termux (Android) with SSH key access to OCI

---

## Infrastructure

### Compute Instances

| Name | Shape | vCPU | RAM | IP | Status |
|---|---|---|---|---|---|
| `instance-main` | VM.Standard.E2.1.Micro | 1 | 1 GB | `158.180.57.245` | Running |
| `instance-arm-trading` | VM.Standard.A1.Flex | 4 (ARM) | 24 GB | pending | OCI capacity retry loop |

**ARM retry loop** (runs from Termux every 5 min via cron, self-removes on success):

```bash
tail -20 /root/projects/ikr/retry_arm.log
```

### Firewall

OCI has two independent firewall layers — both must allow a port:

1. **OCI Security List** (VCN-level)
2. **iptables** on the instance (`iptables-persistent`)

> Insert new ACCEPT rules *before* the REJECT rule:
> ```bash
> sudo iptables -I INPUT 5 -p tcp --dport <PORT> -j ACCEPT
> sudo netfilter-persistent save
> ```

Open ports: `22` (SSH), `7777` (ntfy), `8080` (webhook).
IB Gateway ports are bound to `127.0.0.1` only — never exposed externally.

---

## Services

All services run on `instance-main`.

### IB Gateway

| | |
|---|---|
| Image | `ghcr.io/gnzsnz/ib-gateway:stable` |
| Mode | Paper trading |
| API port | `127.0.0.1:4004` (via socat) |
| Credentials | `~/ib-gateway/.env` (chmod 600) |

```bash
cd ~/ib-gateway && docker compose ps
docker logs ib-gateway-ib-gateway-1 --tail 30
```

### Lumibot

| | |
|---|---|
| Image | `lumibot-app:latest` (Python 3.11, built locally) |
| Deps | `lumibot`, `yfinance` |
| Strategy | `~/lumibot/strategy.py` — `LongCallStrategy` |

```bash
docker logs lumibot-test --tail 50
```

**Rebuild after editing `strategy.py`:**

```bash
cd ~/lumibot
docker build -t lumibot-app .
docker rm -f lumibot-test
docker run -d --name lumibot-test \
  --network host \
  -v /home/ubuntu/trading-bot:/home/ubuntu/trading-bot \
  --env-file ~/trading-bot/.env \
  lumibot-app
```

**Broker config keys** (Lumibot 4.x):

```python
INTERACTIVE_BROKERS_CONFIG = {
    'IP': '127.0.0.1',
    'SOCKET_PORT': 4004,
    'CLIENT_ID': '10',
}
```

**Market data note:** IB paper accounts have no market data subscription by default (error 10089/10167). The strategy handles this by:
1. Calling `self.broker.ib.reqMarketDataType(3)` to request delayed data
2. Falling back to Yahoo Finance for spot price if IB returns NaN
3. Using yfinance for the full option chain (strikes, expiries, bid/ask)

### ntfy (Push Notifications)

| | |
|---|---|
| Port | `7777` |
| Topic | `trading-alerts` |
| Credentials | `NTFY_USER` / `NTFY_PASSWORD` (env vars) |

**Android app setup:**

1. Install [ntfy](https://ntfy.sh) from Play Store or F-Droid
2. Settings → Manage accounts → Add `http://158.180.57.245:7777` with username `trading`
3. Subscribe to topic `trading-alerts`
4. Android → Settings → Apps → ntfy → Battery → **Unrestricted**

### Webhook Server

| | |
|---|---|
| Framework | FastAPI + uvicorn |
| Port | `8080` |
| Persistence | `nohup` only — **dies on reboot** |

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/approve/{order_id}` | Approve — first call wins, subsequent calls ignored |
| `POST` | `/reject/{order_id}` | Reject — first call wins |
| `GET` | `/decision/{order_id}` | Poll for decision |
| `DELETE` | `/decision/{order_id}` | Clear after reading |
| `POST` | `/report/{order_id}` | Store trade report JSON |
| `GET` | `/report/{order_id}` | View HTML trade report |

**Restart:**

```bash
cd ~/trading-bot && source .env && \
  nohup python3 -m uvicorn webhook:app --host 0.0.0.0 --port 8080 &
```

### Trade Notifier (`trade_notifier.py`)

Sentiment sources, tried in order:

| Source | Method | Notes |
|---|---|---|
| WSB DD posts | Tor SOCKS5 | `flair:DD` filter, monthly lookback |
| r/options posts | Tor SOCKS5 | Weekly lookback |
| WSB general | Tor SOCKS5 | Weekly lookback |
| Google News RSS | Direct | Always works from OCI; used as fallback |

On HTTP 429 (Tor exit rate-limited): requests a new circuit via stem control port and retries once.

Groq prompt produces 4 labelled lines: `SENTIMENT`, `RISK`, `CATALYST`, `VERDICT`.
Notification body shows only the `VERDICT` line; full report is one tap away.

### Tor

| | |
|---|---|
| SOCKS5 proxy | `127.0.0.1:9050` |
| Control port | `127.0.0.1:9051` (cookie auth) |
| Auth cookie | `/var/run/tor/control.authcookie` |

```bash
sudo systemctl status tor@default
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

---

## Configuration

### Strategy Parameters

Edit `~/lumibot/strategy.py`, then rebuild:

```python
MAX_PREMIUM_BUDGET = 2000       # max total USD in open option premiums
TARGET_DTE         = 30         # target days to expiration (e.g. 245 for Jan '27 LEAPS)
SYMBOL             = 'UBER'     # underlying ticker
DIRECTION          = 'bearish'  # 'bullish' → buy calls, 'bearish' → buy puts
```

### Secrets

| Secret | Env var | Notes |
|---|---|---|
| IB username | `TWS_USERID` | Set in `~/ib-gateway/.env` |
| IB password | `TWS_PASSWORD` | Set in `~/ib-gateway/.env` |
| Groq API key | `GROQ_API_KEY` | Set in `~/trading-bot/.env` |
| ntfy username | `NTFY_USER` | Set in `~/trading-bot/.env` |
| ntfy password | `NTFY_PASSWORD` | Set in `~/trading-bot/.env` |

Copy `.env.example` files and fill in real values. Never commit `.env` files (covered by `.gitignore`).

> **TODO:** Migrate to OCI Vault + Instance Principal.

---

## Usage

### SSH into instance

```bash
ssh -i /root/.ssh/oci_instance_key ubuntu@158.180.57.245
```

### Check all services

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
ps aux | grep uvicorn
sudo systemctl status tor@default --no-pager
```

### Send a test notification manually

```bash
cd ~/trading-bot && source .env && python3 -c "
from trade_notifier import request_approval
request_approval('AAPL', 'BUY CALL', 1, 710.00)
"
```

### View paper account positions

```bash
python3 -c "
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 4004, clientId=99)
print(ib.positions())
ib.disconnect()
"
```

---

## Development

### Project layout

```
ikr/
├─ lumibot/            Dockerfile + strategy.py (Python 3.11, runs in Docker)
├─ trading-bot/        trade_notifier.py + webhook.py (FastAPI service)
├─ tests/              pytest suite for the webhook server
├─ ib-gateway/         docker-compose.yml for IB Gateway container
├─ ntfy/               docker-compose.yml for ntfy push notification server
├─ pyproject.toml      project metadata + pytest config + dev dependencies
└─ Makefile            convenience targets: install · test · lint · clean
```

### Running tests locally

Requires Python 3.11+.

```bash
# First time: create venv and install dev dependencies
make install

# Run the full test suite
make test

# Check syntax of all Python files
make lint
```

Or without Make:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Tests use `pytest` with `FastAPI`'s `TestClient` (backed by `httpx`). Each test runs in isolation — an `autouse` fixture in `conftest.py` redirects the JSON storage files to a temporary directory.

### Adding tests

Tests live in `tests/test_webhook.py`. Import the app via the `trading_bot` symlink:

```python
from fastapi.testclient import TestClient
import trading_bot.webhook as wh

client = TestClient(wh.app)
```

---

## Known Issues

| Issue | Workaround |
|---|---|
| Webhook not persistent across reboots | Manually restart; needs systemd unit |
| Lumibot not in docker-compose | `docker restart lumibot-test`; needs compose file |
| ARM instance still pending (OCI capacity) | Cron retry loop running every 5 min |
| IB delayed data returns NaN for spot price | Yahoo Finance fallback handles this |
| IB options tick data unavailable (no subscription) | yfinance used for all option pricing |

---

## Roadmap

- [ ] Migrate secrets to OCI Vault + Instance Principal
- [ ] Add systemd unit for webhook server (persistence across reboots)
- [ ] Add docker-compose for Lumibot with restart policy
- [ ] Migrate Lumibot to ARM instance once provisioned
- [ ] Add test suite (unit tests for notifier/webhook, integration test for full pipeline)
- [ ] Replace AAPL-only strategy with multi-ticker scanning
- [ ] Add position sizing beyond single-contract orders
