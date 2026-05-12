# IKR Trading Bot — Onboarding Guide

Paper-trading options bot that runs headlessly on Oracle Cloud, pulls multi-source sentiment via Tor, generates AI analysis with Groq, and waits for a one-tap approval from your Android phone before placing any order.

---

## Architecture at a Glance

```
Android (ntfy app)
  ├─ [View Report] ──────► OCI Webhook :8080/report/{id}   (HTML)
  ├─ [Approve]     ──────► OCI Webhook :8080/approve/{id}  (POST)
  └─ [Reject]      ──────► OCI Webhook :8080/reject/{id}   (POST)

OCI instance-main (158.180.57.245)
  ├─ IB Gateway Docker  :4004 (localhost only)
  ├─ Lumibot strategy   (Docker, Python 3.11)
  ├─ Webhook server     :8080  (FastAPI + uvicorn)
  ├─ ntfy server        :7777  (push notifications)
  └─ Tor daemon         :9050  (SOCKS5 for Reddit)
```

**Trade flow:**
```
09:30 ET market open
  └─ on_trading_iteration()
      ├─ _spot_price()       IB delayed (reqMarketDataType=3) → Yahoo Finance fallback
      ├─ _pick_call()        yfinance option chain → nearest expiry ≥30d, first OTM strike
      ├─ budget check        MAX_PREMIUM_BUDGET = $2,000
      ├─ get_market_sentiment()
      │     ├─ WSB DD posts  (Tor SOCKS5, flair:DD, monthly lookback)
      │     ├─ r/options     (Tor SOCKS5, weekly lookback)
      │     ├─ WSB general   (Tor SOCKS5, weekly lookback)
      │     └─ Google News   (direct RSS, always-on fallback)
      ├─ groq_analyse()      llama-3.3-70b-versatile → SENTIMENT/RISK/CATALYST/VERDICT
      ├─ POST /report/{id}   store HTML report on webhook
      ├─ ntfy push           3 action buttons: View Report · Approve · Reject
      └─ wait_for_decision() polls /decision/{id} every 3s for up to 5 min
```

---

## Tech Stack

| Component | Version | Role | Docs |
|---|---|---|---|
| **Python** | 3.11 (Docker) / 3.8 (host) | Runtime | — |
| **FastAPI** | 0.136.1 | Webhook HTTP server | [fastapi.tiangolo.com](https://fastapi.tiangolo.com/) |
| **uvicorn** | latest | ASGI server for FastAPI | [uvicorn.org](https://www.uvicorn.org/) |
| **Lumibot** | 4.5.10 | Trading strategy framework | [lumibot.lumiwealth.com](https://lumibot.lumiwealth.com/) |
| **IB Gateway** | stable (Docker) | Interactive Brokers paper API | [ghcr.io/gnzsnz/ib-gateway](https://github.com/gnzsnz/ib-gateway) |
| **yfinance** | latest | Option chain + spot price data | [ranaroussi.github.io/yfinance](https://ranaroussi.github.io/yfinance/) |
| **Groq API** | — | LLM inference (llama-3.3-70b-versatile) | [console.groq.com/docs](https://console.groq.com/docs/models) |
| **ntfy** | latest | Self-hosted push notifications | [docs.ntfy.sh](https://docs.ntfy.sh/) |
| **Tor + stem** | system | SOCKS5 proxy for Reddit scraping | [stem.torproject.org](https://stem.torproject.org/) |
| **pytest** | latest | Test suite | [docs.pytest.org](https://docs.pytest.org/) |
| **Docker** | latest | Container runtime | [docs.docker.com](https://docs.docker.com/) |

---

## Key Design Decisions

### Why yfinance instead of IB data?
IB paper accounts have no market data subscription by default (errors 10089, 10167). `get_last_price()` returns NaN; `get_strikes()` / `get_chains()` raise `NotImplementedError`. yfinance provides the full option chain (expiries, strikes, bid/ask) for free — IB is only used for **order submission**.

### Why Tor for Reddit?
OCI datacenter IPs are rate-blocked by Reddit (HTTP 429). Tor SOCKS5 routes through a residential-style exit node. On 429, stem sends `SIGNAL NEWNYM` to rotate the circuit, then retries once.

### Why first-decision-wins?
ntfy action buttons can both fire if tapped quickly or retried. The webhook records the first decision and returns `{'status': 'already_decided'}` on subsequent calls — preventing a reject from overriding an approve (and vice versa).

### Why 3 ntfy buttons max?
ntfy enforces a hard limit of 3 action buttons per notification. A 4th button causes HTTP 400. We use: View Report (opens HTML) · Approve (POST) · Reject (POST).

### Why not naked calls?
The strategy only buys calls (defined risk = premium paid). `MAX_PREMIUM_BUDGET = $2,000` caps total open option premium. No selling, no spreads.

---

## Repository Layout

```
ikr/
├─ lumibot/
│   ├─ Dockerfile          Python 3.11, installs lumibot + yfinance
│   └─ strategy.py         LongCallStrategy — the main trading loop
├─ trading-bot/            (importable as trading_bot via symlink)
│   ├─ trade_notifier.py   Sentiment fetch · Groq analysis · ntfy push
│   └─ webhook.py          FastAPI app — approve/reject/report endpoints
├─ tests/
│   ├─ conftest.py         autouse fixture: redirects JSON files to tmp_path
│   └─ test_webhook.py     16 pytest tests for webhook endpoints
├─ trading_bot -> trading-bot   (symlink, makes package importable)
├─ README.md
└─ ONBOARDING.md           (this file)
```

---

## Infrastructure

### OCI Instances

| Name | Shape | vCPU | RAM | IP | Status |
|---|---|---|---|---|---|
| `instance-main` | VM.Standard.E2.1.Micro | 1 | 1 GB | `158.180.57.245` | Running |
| `instance-arm-trading` | VM.Standard.A1.Flex | 4 ARM | 24 GB | pending | OCI capacity retry loop |

SSH: `ssh -i /root/.ssh/oci_instance_key ubuntu@158.180.57.245`

### Open Ports

| Port | Service |
|---|---|
| 22 | SSH |
| 7777 | ntfy push notifications |
| 8080 | Webhook server (FastAPI) |
| 4004 | IB Gateway API (localhost only) |

### Firewall Note
OCI has two independent firewall layers — both must allow a port:
1. OCI Security List (VCN-level)
2. iptables on the instance (`iptables-persistent`)

Insert new rules before the REJECT rule:
```bash
sudo iptables -I INPUT 5 -p tcp --dport <PORT> -j ACCEPT
sudo netfilter-persistent save
```

---

## Running Locally (Tests)

```bash
cd /root/projects/ikr

# trading_bot symlink must exist (created once)
ln -sf trading-bot trading_bot

# Install test deps
pip install fastapi pytest httpx

# Run webhook tests
pytest tests/test_webhook.py -v
```

---

## Secrets

| Secret | Location | Notes |
|---|---|---|
| IB credentials | `~/ib-gateway/.env` on OCI | `TWS_USERID`, `TWS_PASSWORD` |
| Groq API key | `~/trading-bot/.env` on OCI | `GROQ_API_KEY=gsk_...` |
| ntfy credentials | hardcoded in `trade_notifier.py` | `trading` / `Ntfy@IKR2026` |

> **TODO:** Migrate to OCI Vault + Instance Principal

---

## Webhook API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/approve/{order_id}` | First call wins; subsequent calls return `already_decided` |
| `POST` | `/reject/{order_id}` | First call wins |
| `GET` | `/decision/{order_id}` | Returns `{decision: approved\|rejected\|pending}` |
| `DELETE` | `/decision/{order_id}` | Clears after Lumibot reads it |
| `POST` | `/report/{order_id}` | Store trade report JSON |
| `GET` | `/report/{order_id}` | Serve dark-themed HTML report with clickable source links |

---

## Common Commands

```bash
# Check all services on OCI
docker ps --format 'table {{.Names}}\t{{.Status}}'
ps aux | grep uvicorn
sudo systemctl status tor@default --no-pager

# Restart webhook
cd ~/trading-bot && source .env && \
  nohup python3 -m uvicorn webhook:app --host 0.0.0.0 --port 8080 &

# Rebuild Lumibot after editing strategy.py
cd ~/lumibot && docker build -t lumibot-app . && docker rm -f lumibot-test
docker run -d --name lumibot-test --network host \
  -v /home/ubuntu/trading-bot:/home/ubuntu/trading-bot \
  -e GROQ_API_KEY=$(grep GROQ_API_KEY ~/trading-bot/.env | cut -d= -f2) \
  lumibot-app

# Check ARM provision retry log
tail -20 /root/projects/ikr/retry_arm.log

# Test Tor connectivity
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

---

## Sources

- [FastAPI docs](https://fastapi.tiangolo.com/)
- [Lumibot docs](https://lumibot.lumiwealth.com/)
- [Groq models](https://console.groq.com/docs/models)
- [ntfy publishing docs](https://docs.ntfy.sh/publish/)
- [yfinance docs](https://ranaroussi.github.io/yfinance/)
- [IB Gateway Docker](https://github.com/gnzsnz/ib-gateway)
- [stem (Tor control)](https://stem.torproject.org/)
