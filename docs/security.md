# Security and Compliance

## 1. Threat model

| Threat | Example | Impact |
|--------|---------|--------|
| Credential theft | Leaked Kite API secret or access token | Attacker places trades |
| Server compromise | Exposed SSH or dashboard | Full control of the bot |
| Supply-chain attack | Malicious package | Code execution on the server |
| Prompt injection | News text that tries to instruct the LLM | Manipulated sentiment |
| Data poisoning | Bad ticks or fake news | Wrong trades |
| Insider/self error | Wrong config, script run against live | Loss |
| Replay or duplicate orders | Network retry sends an order twice | Double exposure |
| Denial of service | Broker or feed outage | Stuck positions |

## 2. Secrets management

- Secrets live in environment variables or a secret manager; **never** in the repo, logs, prompts or dashboard.
- Separate credentials for paper and live; the live secret is not present on the dev machine.
- `.env` files are git-ignored; a secret scanner runs in pre-commit and CI.
- Access tokens are kept in memory or in an encrypted store with short retention; broker tokens expire daily.
- **Do not store your broker account password or TOTP secret on the server.** Perform the daily login manually (or via the broker's approved flow), which keeps a stolen server from being able to re-authenticate. (Automated TOTP logins are common but expand the blast radius and may conflict with broker terms; verify.)
- Rotate API keys on a schedule and immediately after any suspected exposure.

## 3. Infrastructure hardening (VPS)

- SSH key authentication only; password login disabled; non-default user, no root login.
- Firewall default-deny; only required ports open. Fail2ban or equivalent.
- PostgreSQL and Redis bound to the private network, never public; authentication on; TLS where available.
- Dashboard reachable only through a VPN or private tunnel (for example WireGuard or Tailscale), with app-level login and 2FA on top.
- Automatic security updates; regular reboots in non-market hours.
- Static IP registered with the broker; outbound traffic restricted where practical.
- Encrypted backups, tested restores, stored off-server.
- Separate OS user and container for the watchdog with minimal privileges.

## 4. Application security

- Least-privilege database roles: the app role cannot delete or update `audit_log`, `order_events`, `fills`.
- Input validation on every API endpoint; no shell execution from user or network input.
- Dashboard actions that change state (kill switch, approvals, mode changes) require re-authentication and are logged.
- Rate limiting and CSRF protection on the API.
- Dependencies pinned with hashes; automated vulnerability scanning (for example pip-audit, npm audit, Dependabot).
- Container images minimal, non-root, regularly rebuilt.
- Logs redact secrets and account identifiers.

## 5. LLM and news security

- News, web pages and filings are **untrusted data**. They may contain text that looks like instructions.
- The LLM is given a fixed system prompt, the text as quoted data, and must return JSON matching a schema; anything else is discarded.
- The LLM has **no tools**, no network access and no ability to place orders or change config.
- Output fields are bounded (enums, numeric ranges, length limits).
- Sudden large sentiment swings on thin evidence are capped and flagged; single-source items from low-credibility sources have low weight.
- Prompt version and model are stored with every score for reproducibility.
- Never send secrets, account details or holdings to the LLM provider.

## 6. Data integrity

- Bad-tick filters and price-band checks (see `data-sources.md`).
- Hash-chained audit log to detect tampering.
- Reconciliation with the broker every few seconds in live mode.
- Clock synchronization (NTP) and monitoring of skew.

## 7. Operational security

- Live mode requires explicit confirmation at startup; never auto-resumes after a crash (rule I-04).
- Alerts for logins, config changes, mode changes and kill-switch use.
- Two notification channels for critical alerts (for example Telegram and email).
- Keep a written offline copy of emergency steps (see `runbook.md`), including how to cancel orders and flatten positions from the broker's own app.

## 8. Regulatory and compliance checklist (verify each item; rules change)

- [ ] Orders are placed only through a SEBI-registered broker's official API.
- [ ] SEBI's retail algo framework (in force from April 2026) requirements are understood: order tagging with an Algo-ID, static IP, 2FA, and any order-rate thresholds that decide whether strategy registration applies to personal API use.
- [ ] Broker's own API terms and static-IP policy are followed.
- [ ] Bot trades only the owner's own account; no pooled funds, no advice or signals given to others.
- [ ] Tax treatment understood (delivery gains vs business-income treatment for F&O) and trades exportable for filing.
- [ ] Market data terms respected (no redistribution, no terms-violating scraping).
- [ ] Records retained (orders, fills, P&L) for the period required for tax and audit.
- [ ] Re-check circulars from SEBI, exchanges and the broker monthly.

Confirm the above with the broker's documentation and a qualified professional before enabling live trading.

## 9. Incident response (security)

1. Trigger kill switch; cancel open orders.
2. Rotate broker API secret and invalidate sessions from the broker console.
3. Preserve logs and snapshots; do not wipe the server before review.
4. Check positions and orders directly in the broker's app.
5. Rebuild from clean images; restore from known-good backups.
6. Write a post-incident note and add a regression test or control.
