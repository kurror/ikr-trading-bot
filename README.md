# IKR Trading Bot

AI-driven options trading system for Interactive Brokers. Pulls multi-source sentiment via Tor, generates structured analysis with a Groq LLM, and pushes a trade approval request to your phone — you tap Approve or Reject before any order is placed.

> Supports **paper and live trading**. Start on IBKR's simulated environment, switch to a live account once the strategy is proven. Risk is always defined: the bot only buys options (calls or puts), never sells naked.

---

## Table of Contents

- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Setup guide](#setup-guide)
  - [1. Oracle Cloud free tier](#1-oracle-cloud-free-tier)
  - [2. IB Gateway](#2-ib-gateway)
  - [3. ntfy push notifications](#3-ntfy-push-notifications)
  - [4. Tor](#4-tor)
  - [5. Webhook server](#5-webhook-server)
  - [6. Lumibot strategy](#6-lumibot-strategy)
  - [7. ARM instance (optional)](#7-arm-instance-optional)
- [Configuration](#configuration)
- [Strategy parameters](#strategy-parameters)
- [Development](#development)
- [Known issues](#known-issues)
- [Roadmap](#roadmap)

---

## How it works

```
Market open (09:30 ET)
  └─ Lumibot on_trading_iteration()
      ├─ _spot_price()         IB delayed data → Yahoo Finance fallback
      ├─ _pick_option()        yfinance chain → nearest expiry ≥ TARGET_DTE, first OTM strike
      ├─ budget check          MAX_PREMIUM_BUDGET cap
      ├─ get_market_sentiment()
      │     ├─ WSB DD posts    via Tor (flair:DD, monthly lookback)
      │     ├─ r/options posts via Tor (weekly lookback)
      │     ├─ WSB general     via Tor (weekly lookback)
      │     └─ Google News RSS direct fallback (always works)
      ├─ groq_analyse()        llama-3.3-70b-versatile → SENTIMENT / RISK / CATALYST / VERDICT
      ├─ POST /report/{id}     store HTML report on webhook server
      └─ ntfy notification     3 buttons: [View Report] [Approve] [Reject]
          └─ first tap wins — duplicate taps silently ignored
              └─ Lumibot submits or skips order via IB Gateway
```

---

## Architecture

```
Your machine (Linux / WSL2 / macOS)
  └─ SSH ──────────────────────────► OCI instance-main (x86, 1 vCPU, 1 GB)
                                           │
                                           ├─ IB Gateway  :4004 (localhost only)
                                           ├─ Lumibot     (Docker, Python 3.11)
                                           ├─ Webhook     :8080  (FastAPI)
                                           ├─ ntfy        :7777  (push server)
                                           └─ Tor         :9050  (SOCKS5 proxy)
                                                 │
                                           ntfy push ──────────────► Android / iOS
                                                                       [View Report]
                                                                       [Approve]
                                                                       [Reject]
```

Two OCI instances are used — both **always-free**:

| Instance | Shape | Role |
|---|---|---|
| `instance-main` | VM.Standard.E2.1.Micro (1 vCPU, 1 GB RAM, x86) | Runs all services |
| `instance-arm-trading` | VM.Standard.A1.Flex (4 OCPU, 24 GB RAM, ARM) | Heavier workloads, Lumibot migration target |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| [Oracle Cloud account](https://cloud.oracle.com) | Free tier — no credit card required for always-free resources |
| [Interactive Brokers account](https://www.interactivebrokers.com) | Paper account for development, live for production. IBKR Pro required (not Lite) |
| [Groq API key](https://console.groq.com) | Free tier is sufficient. See [LLM options](#llm-options) for alternatives |
| Linux machine | Ubuntu / Debian / WSL2 / macOS recommended. Android + Termux also works |
| [ntfy app](https://ntfy.sh) | Android (Play Store / F-Droid) or iOS (App Store) |

### LLM options

The bot uses [Groq](https://console.groq.com) by default (`llama-3.3-70b-versatile`) — fast inference, generous free tier. Alternatives:

- **OCI Generative AI** — keeps everything inside Oracle Cloud. Supports Llama 3 and Cohere via a compatible REST API. Available in Frankfurt and Chicago regions. Replace the Groq endpoint in `groq_analyse()` with your OCI GenAI endpoint.
- **Ollama on the ARM instance** — the 24 GB A1.Flex can run 7–13B models locally with no external API calls. Set `OLLAMA_HOST` and point `groq_analyse()` to `http://localhost:11434`.

---

## Setup guide

### 1. Oracle Cloud free tier

#### Create instance-main (x86 micro)

1. Sign in to [cloud.oracle.com](https://cloud.oracle.com)
2. Compute → Instances → **Create Instance**
3. Shape: **VM.Standard.E2.1.Micro** (Always Free)
4. Image: Ubuntu 22.04
5. Add your SSH public key
6. Note the public IP — used as `YOUR_OCI_IP` throughout

#### Open firewall ports

OCI has two independent firewall layers — **both** must allow each port.

**OCI Security List** (VCN → Security Lists → Ingress Rules):

| Port | Protocol | Source CIDR |
|---|---|---|
| 22 | TCP | 0.0.0.0/0 |
| 7777 | TCP | 0.0.0.0/0 |
| 8080 | TCP | 0.0.0.0/0 |

**iptables on the instance** (insert before the default REJECT rule):

```bash
sudo iptables -I INPUT 5 -p tcp --dport 7777 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

IB Gateway ports (`4003`, `4004`, `5900`) are bound to `127.0.0.1` — never exposed externally.

#### Install base dependencies

```bash
ssh ubuntu@YOUR_OCI_IP

sudo apt update && sudo apt install -y \
  docker.io docker-compose-plugin \
  tor python3-pip python3-venv \
  iptables-persistent

sudo usermod -aG docker ubuntu
# Log out and back in for docker group to take effect
```

---

### 2. IB Gateway

Runs in Docker, handles all communication with Interactive Brokers.

```bash
mkdir ~/ib-gateway && cd ~/ib-gateway
# Copy docker-compose.yml and .env.example from repo
cp .env.example .env && chmod 600 .env
# Edit .env with your IBKR credentials
docker compose up -d
```

**`~/ib-gateway/.env`:**

```env
TWS_USERID=your_ibkr_username
TWS_PASSWORD=your_ibkr_password
TRADING_MODE=paper        # change to 'live' when ready
VNC_SERVER_PASSWORD=changeme
```

> Tip: create a secondary IBKR username for API use — website logins and API sessions can collide.

```bash
# Verify
docker compose ps
docker logs ib-gateway-ib-gateway-1 --tail 30
```

**Market data:** IB paper accounts have no subscription by default (errors 10089, 10167). The strategy handles this automatically — delayed data via IB, Yahoo Finance fallback for spot price, yfinance for the full option chain.

---

### 3. ntfy push notifications

Self-hosted push server. Runs in Docker on port 7777.

```bash
mkdir ~/ntfy && cd ~/ntfy
# Copy docker-compose.yml from repo
# Edit NTFY_BASE_URL to http://YOUR_OCI_IP:7777
docker compose up -d

# Create user
docker compose exec ntfy ntfy user add --role=admin trading
# Enter and note the password — add it to trading-bot/.env
```

**Mobile setup:**

1. Install [ntfy](https://ntfy.sh) from Play Store, F-Droid, or App Store
2. Settings → Manage accounts → Add server:
   - URL: `http://YOUR_OCI_IP:7777`
   - Username: `trading`
   - Password: your chosen password
3. Subscribe to topic `trading-alerts`
4. Android: Apps → ntfy → Battery → **Unrestricted** (prevents kill during sleep)

---

### 4. Tor

Routes Reddit requests through residential-style exit nodes, bypassing OCI datacenter IP rate limits.

```bash
sudo systemctl enable tor@default
sudo systemctl start tor@default

# Enable control port for auto circuit renewal on HTTP 429
echo -e "ControlPort 9051\nCookieAuthentication 1" | sudo tee -a /etc/tor/torrc
sudo systemctl restart tor@default

# Verify
curl --socks5-hostname 127.0.0.1:9050 https://check.torproject.org/api/ip
```

---

### 5. Webhook server

FastAPI server that stores trade reports and holds approve/reject decisions until Lumibot polls for them.

```bash
mkdir ~/trading-bot && cd ~/trading-bot
# Copy trade_notifier.py, webhook.py, requirements.txt from repo

pip3 install -r requirements.txt

# Create .env from example
cp .env.example .env && chmod 600 .env
# Edit .env with real values

# Start (survives SSH logout)
nohup python3 -m uvicorn webhook:app --host 0.0.0.0 --port 8080 >> webhook.log 2>&1 &

# Verify
curl http://localhost:8080/decision/test
# → {"decision":"pending"}
```

---

### 6. Lumibot strategy

Runs in Docker (Python 3.11), connects to IB Gateway on localhost.

```bash
mkdir ~/lumibot && cd ~/lumibot
# Copy Dockerfile and strategy.py from repo

docker build -t lumibot-app .

docker run -d --name lumibot-live \
  --network host \
  -v ~/trading-bot:/home/ubuntu/trading-bot \
  --env-file ~/trading-bot/.env \
  lumibot-app
```

**Rebuild after editing `strategy.py`:**

```bash
cd ~/lumibot
docker build -t lumibot-app . && \
docker rm -f lumibot-live && \
docker run -d --name lumibot-live \
  --network host \
  -v ~/trading-bot:/home/ubuntu/trading-bot \
  --env-file ~/trading-bot/.env \
  lumibot-app
```

---

### 7. ARM instance (optional)

The ARM A1.Flex (4 OCPU, 24 GB) is always-free but capacity in Frankfurt is often temporarily unavailable. A retry script runs on `instance-main` (which is always on) and self-removes from cron when it succeeds.

```bash
# On instance-main: install OCI CLI
bash -c "$(curl -fsSL https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)" --accept-all-defaults

# Copy OCI credentials from your local machine
scp ~/.oci/config ubuntu@YOUR_OCI_IP:~/.oci/config
scp ~/.oci/oci_api_key.pem ubuntu@YOUR_OCI_IP:~/.oci/oci_api_key.pem
ssh ubuntu@YOUR_OCI_IP "chmod 600 ~/.oci/oci_api_key.pem"

# Copy and configure retry script
scp retry_arm.sh ubuntu@YOUR_OCI_IP:~/retry_arm.sh
ssh ubuntu@YOUR_OCI_IP "chmod +x ~/retry_arm.sh"
# Edit retry_arm.sh: fill in YOUR_OCI_COMPARTMENT_ID, IMAGE_ID, SUBNET_ID

# Register cron on instance-main
ssh ubuntu@YOUR_OCI_IP \
  "(crontab -l 2>/dev/null; echo '*/5 * * * * /home/ubuntu/retry_arm.sh >> /home/ubuntu/retry_arm.log 2>&1') | crontab -"

# Monitor
ssh ubuntu@YOUR_OCI_IP "tail -f ~/retry_arm.log"
```

---

## Configuration

All secrets live in `~/trading-bot/.env` on the server. The app loads this file automatically via `python-dotenv` — no `source .env` needed.

```env
GROQ_API_KEY=gsk_...
NTFY_USER=trading
NTFY_PASSWORD=your_ntfy_password
NTFY_SERVER=http://YOUR_OCI_IP:7777
NTFY_TOPIC=trading-alerts
WEBHOOK_URL=http://YOUR_OCI_IP:8080
```

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | [console.groq.com](https://console.groq.com) — free tier |
| `NTFY_USER` | Yes | ntfy username |
| `NTFY_PASSWORD` | Yes | ntfy password |
| `NTFY_SERVER` | No | ntfy URL (default: `http://YOUR_OCI_IP:7777`) |
| `NTFY_TOPIC` | No | Topic name (default: `trading-alerts`) |
| `WEBHOOK_URL` | No | Webhook base URL (default: `http://YOUR_OCI_IP:8080`) |

IB Gateway credentials go in `~/ib-gateway/.env` — separate file, separate container.

---

## Strategy parameters

Edit `~/lumibot/strategy.py` then rebuild the Docker image:

```python
MAX_PREMIUM_BUDGET = 2000       # max total USD in open option premiums
TARGET_DTE         = 30         # target days to expiration (use ~245 for Jan LEAPS)
SYMBOL             = 'UBER'     # underlying ticker
DIRECTION          = 'bearish'  # 'bullish' → buy calls  |  'bearish' → buy puts
```

---

## Development

### Repository layout

```
ikr/
├─ lumibot/
│   ├─ Dockerfile          Python 3.11 image — installs lumibot + yfinance
│   └─ strategy.py         OptionsStrategy — main trading loop (configurable direction)
├─ trading-bot/
│   ├─ trade_notifier.py   Sentiment fetch · Groq analysis · ntfy push · decision polling
│   ├─ webhook.py          FastAPI — approve / reject / report endpoints
│   ├─ requirements.txt    Runtime dependencies
│   └─ .env.example        Required environment variables (no real secrets)
├─ ib-gateway/
│   ├─ docker-compose.yml  IB Gateway container
│   └─ .env.example        IBKR credentials template
├─ ntfy/
│   └─ docker-compose.yml  ntfy push notification server
├─ tests/
│   ├─ conftest.py         autouse fixture — isolated tmp storage per test
│   └─ test_webhook.py     14 pytest tests covering all webhook endpoints
├─ pyproject.toml          Project metadata + dev dependencies + pytest config
├─ Makefile                install · test · lint · clean
└─ retry_arm.sh            ARM instance provisioning retry loop (runs on instance-main)
```

### Running tests

Requires Python 3.11+ on Linux, WSL2, or macOS.

```bash
git clone https://github.com/kurror/ikr-trading-bot.git
cd ikr-trading-bot

ln -sf trading-bot trading_bot   # makes package importable
make install                     # creates .venv + installs dev deps
make test                        # runs pytest
```

### Webhook API reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/approve/{order_id}` | First call wins; subsequent calls return `already_decided` |
| `POST` | `/reject/{order_id}` | First call wins |
| `GET` | `/decision/{order_id}` | Returns `{decision: approved\|rejected\|pending}` |
| `DELETE` | `/decision/{order_id}` | Clears decision after Lumibot reads it |
| `POST` | `/report/{order_id}` | Store trade analysis JSON |
| `GET` | `/report/{order_id}` | Serve dark-themed HTML report with clickable source links |

---

## Known issues

| Issue | Workaround |
|---|---|
| Webhook dies on reboot | Manually restart; systemd unit planned |
| Lumibot not in docker-compose | `docker restart lumibot-live`; compose file planned |
| ARM instance capacity unavailable | Retry cron running on `instance-main` every 5 min |
| IB delayed data returns NaN | Yahoo Finance fallback handles this automatically |
| IB options tick data unavailable (no subscription) | yfinance used for all option pricing |
| Yahoo Finance terms restrict commercial use | Fine for paper trading; upgrade to ThetaData / Polygon for live |

---

## Roadmap

- [ ] systemd unit for webhook server (persistence across reboots)
- [ ] docker-compose for Lumibot with restart policy
- [ ] Migrate Lumibot to ARM instance once provisioned
- [ ] OCI Vault + Instance Principal for secret management
- [ ] Structured audit log per trade (sources → analysis → decision → fill)
- [ ] Multi-ticker scanning
- [ ] Richer LLM analysis: bull/bear debate + risk agent
- [ ] Premium data feed for live trading (ThetaData for options, Polygon for equities)
