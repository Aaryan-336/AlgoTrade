# Deploy: run 24/7 on a server, with alerts on your phone

The result:

- A small Linux server runs the bot all day, every day. Docker restarts anything that crashes and starts everything again after a reboot.
- Upstox market data comes in through an **Analytics Token**, so there is no daily login.
- You open the dashboard from your phone or laptop over **Tailscale**, a private network. It never gets a public address.
- **Telegram** sends you every buy and sell, a morning readiness check, an evening summary, and an alert whenever the bot stops working.
- **Healthchecks.io** alerts you if the whole server goes down. In that case the bot can't send anything itself.

```
phone / laptop ──Tailscale (private, encrypted)──▶ server
                                                   ├─ dashboard (Next.js)   127.0.0.1:3000
                                                   ├─ api + engine          127.0.0.1:8000
                                                   ├─ watchdog (separate process)
                                                   ├─ postgres + nightly backup
                                                   └─▶ Telegram, Healthchecks.io, Upstox, Groq (outbound only)
```

Time needed: about an hour the first time. Do it on a weekend or after 15:30 IST.

---

## 1. Get a server

Any always-on Linux machine works. Use **2 GB of RAM or more**, because building the dashboard needs it, and **Ubuntu 24.04**. A region in India keeps Upstox calls fast, but this is a daily-bar bot, so the region isn't critical.

| Option | Notes |
|---|---|
| Oracle Cloud "Always Free" (Mumbai or Hyderabad) | Free ARM VM with plenty of RAM. Sign-up needs a card, and free capacity is sometimes unavailable. |
| AWS Lightsail (Mumbai), DigitalOcean (Bangalore), Hetzner, etc. | Roughly ₹500–1,000 a month for 2 GB. Simple and reliable. Check current prices. |
| A spare PC or Raspberry Pi 5 at home | Free. Only as reliable as your power and internet. |

Free "app hosting" tiers (Render, Railway, Vercel and similar) aren't a good fit. Free services go to sleep when idle, which stops the bot. The bot also needs a long-running engine, a separate watchdog and a private dashboard.

## 2. Secure the server

From your laptop, using the IP address the provider gives you:

```bash
ssh ubuntu@SERVER_IP          # use the SSH key you added when creating the server

sudo apt update && sudo apt -y upgrade
sudo apt -y install unattended-upgrades ufw git
sudo dpkg-reconfigure -plow unattended-upgrades   # automatic security updates

# Firewall: block everything coming in except SSH (closed fully in step 5)
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
```

Make sure password login is off (`PasswordAuthentication no` in `/etc/ssh/sshd_config`). Most cloud images already set this.

## 3. Install Docker and get the code

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

git clone https://github.com/Aaryan-336/AlgoTrade.git
cd AlgoTrade
# git checkout <branch>   # only if you deploy a branch that isn't merged yet
```

## 4. Get the keys

| Key | Where |
|---|---|
| `UPSTOX_ANALYTICS_TOKEN` | [Upstox Developer Apps](https://account.upstox.com/developer/apps), **Analytics** tab, **Generate**. It's valid for 1 year and is read-only (it can't place orders). The dashboard warns you 3 weeks before it expires. |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) |
| `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` | Section 6 |
| `HEALTHCHECK_PING_URL` | Section 7 |
| `API_TOKEN` | Run `openssl rand -hex 32` on the server. It's the password for the dashboard and API. |

## 5. Private access with Tailscale

Tailscale gives the server a private address that only your own devices can reach. It's free for personal use.

1. Install it on the server and sign in:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up --hostname=algotrade
   ```
2. Install the Tailscale app on your **phone** and **laptop**, and sign in with the same account.
3. In the [Tailscale admin console](https://login.tailscale.com/admin/dns), turn on **MagicDNS** and **HTTPS certificates**.
4. Give the dashboard and the API private HTTPS addresses. Your tailnet name is shown in the admin console, for example `tail1234.ts.net`.
   ```bash
   sudo tailscale serve --bg --https=443  http://127.0.0.1:3000   # dashboard
   sudo tailscale serve --bg --https=8443 http://127.0.0.1:8000   # API
   tailscale serve status
   ```
   - Dashboard: `https://algotrade.tail1234.ts.net`
   - API: `https://algotrade.tail1234.ts.net:8443`
5. Optional but recommended: once you can SSH in over Tailscale (`ssh ubuntu@algotrade`), close the public SSH port.
   ```bash
   sudo ufw allow in on tailscale0
   sudo ufw delete allow OpenSSH
   ```

## 6. Telegram alerts

1. In Telegram, message **@BotFather** and send `/newbot`. Pick a name. BotFather replies with a **token** like `123456:ABC...`.
2. Open a chat with your new bot and send it any message, for example "hi".
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and find `"chat":{"id":123456789`. That number is your **chat id**.
4. Test it:
   ```bash
   curl -s "https://api.telegram.org/bot<TOKEN>/sendMessage" -d chat_id=<CHAT_ID> -d text="AlgoTrade test"
   ```

## 7. Server-down alerts (Healthchecks.io)

The bot pings a URL every minute. If the pings stop because the server crashed, lost power or lost internet, Healthchecks.io alerts you. This is your second alert channel. It doesn't depend on the bot working.

1. Sign up at [healthchecks.io](https://healthchecks.io) (the free plan is enough) and **Add Check**.
2. Set **Period 2 minutes** and **Grace 5 minutes**.
3. Under **Integrations**, add **Telegram**, **Email** and/or the mobile app.
4. Copy the ping URL (`https://hc-ping.com/...`). It goes into `HEALTHCHECK_PING_URL`.

## 8. Configure and start

```bash
cp backend/.env.example backend/.env
cp infra/.env.example infra/.env
nano backend/.env
nano infra/.env
```

`backend/.env`. Put in your own values, using your Tailscale names from section 5:

```ini
DATA_PROVIDER=upstox
UPSTOX_ANALYTICS_TOKEN=eyJ...
GROQ_API_KEY=gsk_...
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_ALERT_LEVEL=info
HEALTHCHECK_PING_URL=https://hc-ping.com/your-uuid
API_TOKEN=<output of openssl rand -hex 32>
DASHBOARD_URL=https://algotrade.tail1234.ts.net
CORS_ORIGINS=["https://algotrade.tail1234.ts.net"]
# Only if you also want the daily-login flow as a fallback (register this exact
# URL in your Upstox app):
# UPSTOX_REDIRECT_URI=https://algotrade.tail1234.ts.net:8443/api/auth/upstox/callback
```

`infra/.env`:

```ini
POSTGRES_PASSWORD=<another long random string>
PUBLIC_API_URL=https://algotrade.tail1234.ts.net:8443
```

Start everything:

```bash
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --build
docker compose -f infra/docker-compose.yml --env-file infra/.env ps        # all "running"
docker compose -f infra/docker-compose.yml --env-file infra/.env logs -f api
```

Then:

1. Open `https://algotrade.tail1234.ts.net` on your phone with Tailscale connected.
2. Paste your `API_TOKEN` when the dashboard asks for it.
3. Add the page to your home screen: in the browser menu, **Add to Home Screen**.
4. On the Overview, **Is the bot working?** should show ✓ for engine, prices (shown as waiting while the market is closed), history and safety within a minute or two.
5. Set your paper capital on the live strategy version (Settings), after market hours.

The server starts with a fresh paper account. Data from your laptop's SQLite file isn't copied over.

## 9. What arrives on your phone

| When | Message |
|---|---|
| 09:00 on trading days | **Good morning**: ready or **NOT READY** (with the reason), equity, plans queued for 09:20, last close of Nifty, VIX and breadth |
| Each trade | "Bought INFY x12 @ ₹1,520.40", "Closed TCS x3 P&L ₹230.10", "Stop hit on X" |
| During market hours, if something breaks | "Bot is NOT trading: Live prices: …" (critical), or "Bot needs attention: …", then "Bot is working normally again." |
| Any breaker or kill switch | "Breaker daily_loss: …", "Kill switch (flatten): …" |
| Watchdog | "engine heartbeat lost", sent from the separate watchdog process |
| About 15:50 | **Day summary**: equity, today's and total P&L, bought and sold today, positions with stops, tomorrow's plan |
| Server down | Healthchecks.io: "algotrade is DOWN" |

For fewer messages, set `TELEGRAM_ALERT_LEVEL=warning` (problems only, no trades or briefs) and restart the API.

## 10. Day-to-day

- **Nothing to do each morning.** Read the 09:00 message. If it says NOT READY, open the dashboard.
- **Update the code**, only after 15:30 IST:
  ```bash
  cd ~/AlgoTrade && git pull
  docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --build
  ```
- **Renew the Analytics Token** when warned (the yearly expiry): generate a new one, update `backend/.env`, and run `docker compose ... up -d api`.
- **Backups**: a dump is written every night to `infra/backups/` and kept for 14 days. Copy it off the server now and then, for example `scp algotrade:AlgoTrade/infra/backups/*.gz .`. To restore:
  ```bash
  gunzip -c algotrade-2026-10-05.sql.gz | docker compose -f infra/docker-compose.yml --env-file infra/.env exec -T postgres psql -U algotrade algotrade
  ```
- **Emergency stop**: use the **Kill switch** on the dashboard, or from the server:
  `docker compose -f infra/docker-compose.yml --env-file infra/.env exec watchdog algotrade-watchdog kill --action block --reason "manual"`.

## 11. Troubleshooting

| Symptom | Check |
|---|---|
| Dashboard doesn't load on the phone | Tailscale is connected on the phone; `tailscale serve status` on the server |
| Dashboard loads but shows "Connecting to the trading engine…" | `PUBLIC_API_URL` must be the `:8443` address, then **rebuild** (`up -d --build`), because it's built into the dashboard; `CORS_ORIGINS` must contain the dashboard address |
| "Upstox rejected the analytics token" | The token expired or was regenerated somewhere else (only one can exist per account). Generate a new one. |
| No Telegram messages | Run the curl test in section 6; check `docker compose logs api \| grep -i telegram` |
| Healthchecks says DOWN but the server is up | `docker compose ps`; check the api logs for errors; `HEALTHCHECK_PING_URL` is set |
| Build runs out of memory | Use a 2 GB+ server, or add swap: `sudo fallocate -l 2G /swap && sudo chmod 600 /swap && sudo mkswap /swap && sudo swapon /swap` |
