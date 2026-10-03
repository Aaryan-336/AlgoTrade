"""Alerts: kept in memory for the dashboard, logged, and optionally sent to
Telegram. Alert text never contains secrets or account identifiers."""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

log = logging.getLogger("algotrade.alerts")
Level = Literal["info", "warning", "critical"]
RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}


@dataclass(frozen=True, slots=True)
class Alert:
    ts: datetime
    level: Level
    text: str


class Notifier:
    def __init__(self, telegram_token: str | None = None, chat_id: str | None = None,
                 client: httpx.AsyncClient | None = None, quiet: bool = False,
                 min_level: Level = "warning") -> None:
        self.quiet = quiet
        self.min_rank = RANK[min_level]
        self.recent: deque[Alert] = deque(maxlen=200)
        self._token = telegram_token
        self._chat = chat_id
        self._client = client
        self._outbox: list[Alert] = []

    def send(self, ts: datetime, level: Level, text: str) -> None:
        alert = Alert(ts, level, text)
        self.recent.appendleft(alert)
        if not self.quiet:
            getattr(log, "critical" if level == "critical" else level)(text)
        if self._token and self._chat and RANK[level] >= self.min_rank:
            self._outbox.append(alert)

    async def flush(self) -> None:
        """Deliver queued Telegram alerts. Failures are logged, never raised."""
        if not self._outbox or not self._token or not self._chat:
            return
        pending, self._outbox = self._outbox, []
        client = self._client or httpx.AsyncClient(timeout=10)
        try:
            for a in pending:
                icon = {"warning": "⚠️", "critical": "🛑"}.get(a.level, "ℹ️")
                try:
                    await client.post(
                        f"https://api.telegram.org/bot{self._token}/sendMessage",
                        json={"chat_id": self._chat, "text": f"{icon} [PAPER] {a.text}"})
                except httpx.HTTPError as exc:
                    log.warning("telegram delivery failed: %s", type(exc).__name__)
        finally:
            if self._client is None:
                await client.aclose()
