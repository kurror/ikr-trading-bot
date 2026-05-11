# IKR Trading Bot

Automated paper-trading system for options on Interactive Brokers. Runs headlessly on Oracle Cloud free tier, sends AI-analysed trade approvals to your Android phone, and waits for your tap before submitting any order.

> **Paper trading only.** The bot is hard-wired to IBKR's simulated environment and enforces a $10,000 budget cap. No real money is ever at risk.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Infrastructure](#infrastructure)
- [Services](#services)
- [Configuration](#configuration)
- [Usage](#usage)
- [Known Issues](#known-issues)
- [Roadmap](#roadmap)

---

## Features

- Headless IB Gateway via Docker (paper account, auto-login via IBC)
- Lumibot strategy execution with human-approval gate before every order
- Reddit sentiment via **Tor** (bypasses OCI IP block; auto-renews circuit on 429)
- Google News RSS fallback when Tor exit is rate-limited
- **Groq** `llama-3.3-70b` analysis of each trade before notification
- **ntfy** push notification to Android with three action buttons:
  - `Reddit Source` — opens r/options search in your Reddit app
  - `Approve` — submits the paper order
  - `Reject` — skips the iteration
- Hard budget cap (`MAX_BUDGET = $10,000`) enforced in strategy code
- All secrets in `.env` files (chmod 600), no plaintext in code

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
Market open
  └─ Lumibot checks position + budget
      └─ get_market_sentiment()
          ├─ Reddit r/options via Tor  ──► (429?) renew circuit, retry
          └─ Google News RSS fallback
      └─ Groq analyses trade + context (2-line response)
      └─ ntfy notification → phone
          └─ [Reddit Source] [Approve] [Reject]
              └─ Webhook records decision
                  └─ Lumibot submits or skips
```

---

## Prerequisites

- Oracle Cloud account (free tier)
- Interactive Brokers paper account
- Android phone with [ntfy app](https://ntfy.sh)
- [Groq API key](https://console.groq.com) (free tier)
- Termux (Android) with SSH access to OCI

---

## Infrastructure

### Compute Instances

| Name | Shape | vCPU | RAM | IP | Status |
|---|---|---|---|---|---|
| `instance-main` | VM.Standard.E2.1.Micro | 1 | 1 GB | `158.180.57.245` | Running |
| `instance-arm-trading` | VM.Standard.A1.Flex | 4 (ARM) | 24 GB | pending | OCI capacity retry loop |

The ARM instance will take over Lumibot once provisioned (1 GB RAM on the micro is tight).

**ARM retry loop** (runs from Termux every 5 min via cron, self-removes on success):

```bash
tail -20 /root/projects/ikr/retry_arm.log
crontab -l | grep retry_arm
```

### Firewall

OCI has two independent firewall layers — both must allow a port:

1. **OCI Security List** (VCN-level, via Console or CLI)
2. **iptables** on the instance (persisted via `iptables-persistent`)

> **Gotcha:** Insert new ACCEPT rules *before* the REJECT rule or traffic will be dropped.
>
> ```bash
> sudo iptables -I INPUT 5 -p tcp --dport <PORT> -j ACCEPT
> sudo netfilter-persistent save
> ```

Open ports: `22` (SSH), `7777` (ntfy), `8080` (webhook).  
IB Gateway ports `4003`, `4004`, `5900` are bound to `127.0.0.1` — never exposed.

---

## Services

All services run on `instance-main`.

### IB Gateway

| | |
|---|---|
| Location | `~/ib-gateway/` |
| Image | `ghcr.io/gnzsnz/ib-gateway:stable` |
| Mode | Paper trading |
| API port | `127.0.0.1:4004` (via socat) |
| VNC | `127.0.0.1:5900` |
| Credentials | `~/ib-gateway/.env` (chmod 600) |

```bash
cd ~/ib-gateway && docker compose ps
docker logs ib-gateway-ib-gateway-1 --tail 30
```

Paper trading is confirmed three ways: `TRADING_MODE=paper` in `.env`, IBC log entry "Paper Log In clicked", and window title "Simulated Trading".

### Lumibot

| | |
|---|---|
| Location | `~/lumibot/` |
| Strategy | `~/lumibot/strategy.py` |
| Image | `lumibot-app:latest` (Python 3.11, built locally) |
| Container | `lumibot-test` |

```bash
docker logs lumibot-test --tail 50
docker restart lumibot-test
```

**Rebuild after editing strategy.py:**

```bash
cd ~/lumibot
docker build -t lumibot-app .
docker rm -f lumibot-test
docker run -d --name lumibot-test \
  --network host \
  -v /home/ubuntu/trading-bot:/home/ubuntu/trading-bot \
  -e GROQ_API_KEY=$(grep GROQ_API_KEY ~/trading-bot/.env | cut -d= -f2) \
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

### ntfy (Push Notifications)

| | |
|---|---|
| Location | `~/ntfy/` |
| Port | `7777` |
| Topic | `trading-alerts` |
| Credentials | `trading` / `Ntfy@IKR2026` |
| Token | `tk_a6zvk0mbv3p9de8he9u2o4vzqw8kg` |

**Android app setup:**

1. Install [ntfy](https://ntfy.sh) from Play Store or F-Droid
2. Settings → Manage accounts → Add:
   - Server: `http://158.180.57.245:7777`
   - Username: `trading`
   - Password: `Ntfy@IKR2026`
3. Subscribe to topic `trading-alerts`
4. Android → Settings → Apps → ntfy → Battery → **Unrestricted** (required for background delivery)

### Webhook Server

| | |
|---|---|
| Location | `~/trading-bot/webhook.py` |
| Framework | FastAPI + uvicorn |
| Port | `8080` |
| Persistence | `nohup` only — **dies on reboot** (see [Known Issues](#known-issues)) |

Endpoints:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/approve/{order_id}` | Approve a pending trade |
| `POST` | `/reject/{order_id}` | Reject a pending trade |
| `GET` | `/decision/{order_id}` | Poll for decision |
| `DELETE` | `/decision/{order_id}` | Clear after reading |

**Check / restart:**

```bash
ps aux | grep uvicorn
cd ~/trading-bot && source .env && \
  nohup python3 -m uvicorn webhook:app --host 0.0.0.0 --port 8080 &
```

### Trade Notifier

| | |
|---|---|
| Location | `~/trading-bot/trade_notifier.py` |
| Secrets | `~/trading-bot/.env` |

Sentiment sources (tried in order):

1. **Reddit r/options** via Tor — on HTTP 429, requests a fresh Tor circuit and retries once
2. **Google News RSS** — direct fetch, always works from OCI

**Manual test:**

```bash
cd ~/trading-bot && source .env && python3 -c "
from trade_notifier import request_approval
request_approval('AAPL', 'BUY', 1, 210.50)
"
```

### Tor

| | |
|---|---|
| Package | `tor`, `torsocks`, `stem` |
| SOCKS5 proxy | `127.0.0.1:9050` |
| Control port | `127.0.0.1:9051` (cookie auth) |
| Auth cookie | `/var/run/tor/control.authcookie` |

```bash
sudo systemctl status tor@default
# Verify exit IP:
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

`ubuntu` user is in the `debian-tor` group to read the auth cookie.

---

## Configuration

### Secrets

| Secret | File | Notes |
|---|---|---|
| IB username / password | `~/ib-gateway/.env` | `TWS_USERID`, `TWS_PASSWORD` |
| Groq API key | `~/trading-bot/.env` | `GROQ_API_KEY=gsk_...` |
| ntfy password | in notifier source | `Ntfy@IKR2026` |

> **TODO:** Migrate to OCI Vault + Instance Principal (removes plaintext secrets from disk entirely).

### Strategy Parameters

Edit `~/lumibot/strategy.py`:

```python
MAX_BUDGET = 10_000          # hard cap in USD
parameters = {
    'symbol':   'AAPL',      # ticker to trade
    'quantity': 1,           # shares per order
}
```

After editing, rebuild the Docker image (see [Lumibot](#lumibot) section).

### Groq

- **Model:** `llama-3.3-70b-versatile`
- **Note:** Must include `User-Agent: Mozilla/5.0` header — OCI IPs are Cloudflare-blocked otherwise.
- **Prompt:** Instructs a 2-line reply: one-sentence market analysis + `APPROVE`/`REJECT` with reason.

---

## Usage

### SSH access from Termux

```bash
ssh -i /root/.ssh/oci_instance_key ubuntu@158.180.57.245
```

### Access IB Gateway API locally (SSH tunnel)

```bash
ssh -i /root/.ssh/oci_instance_key \
  -L 4004:localhost:4004 -N ubuntu@158.180.57.245
```

Then connect from any local Python script:

```python
from ib_insync import IB
ib = IB()
ib.connect('127.0.0.1', 4004, clientId=1)
```

### Check all services

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}'
ps aux | grep uvicorn
sudo systemctl status tor@default --no-pager
```

---

## Known Issues

| Issue | Workaround |
|---|---|
| Webhook server not persistent across reboots | Manually restart with `nohup uvicorn ...`; needs systemd unit |
| Lumibot not in docker-compose (no auto-restart) | `docker restart lumibot-test`; needs compose file |
| ARM instance still pending (OCI capacity) | Cron retry loop running every 5 min |
| ntfy push requires "Unrestricted" battery setting | Must be set manually on Android |

---

## Roadmap

- [ ] Migrate secrets to OCI Vault + Instance Principal
- [ ] Add systemd unit for webhook server
- [ ] Add docker-compose for Lumibot with restart policy
- [ ] Migrate Lumibot to ARM instance once provisioned
- [ ] Replace BuyAndHold placeholder with real options strategy
- [ ] Add position sizing logic beyond single-share orders
